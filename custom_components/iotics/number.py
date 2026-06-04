"""
Number platform for Iotics — fan speed controls (0-4).

Creates NumberEntity instances for l1 (fan speed) buttons only.
State is read from the coordinator's entity_state map (updated by 
MQTT handler in real-time). Commands sent via MQTT publish.

Key design:
  - Only l1 buttons create number entities (f1 = on/off, handled by switch.py)
  - native_value reads from coordinator.entity_state for real-time sync
  - async_set_native_value publishes MQTT to io/{token}/{btn}/sw topic
  - entities_by_id registration enables direct async_write_ha_state from MQTT handler
  - The CoordinatorEntity superclass links to DataUpdateCoordinator for cloud sync

Important (June 4-11, 2026 fixes):
  - CRITICAL: coordinator.entities_by_id MUST be populated (was missing, causing
    fan speed slider to never update on physical change)
  - l1 payloads "0"-"4" stay as raw strings (not converted to "on"/"off")
  - The slugify function produces underscores (hall_1_1), not hyphens (hall-1-1)
  - native_value returns float(coordinator.entity_state.get(self.entity_id, "0"))
    or 0.0 on ValueError (e.g. if garbage data somehow stored)
"""

from __future__ import annotations
import logging
from typing import Any

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN, COORDINATOR, MQTT_CLIENT
from .iotics_api import slugify, IoticsApiClient

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Iotics number entities (fan speeds)."""
    coordinator = hass.data[DOMAIN][COORDINATOR]
    mqtt_client = hass.data[DOMAIN][MQTT_CLIENT]
    devices = coordinator.data

    buttons = IoticsApiClient.extract_buttons(devices)
    fans = [b for b in buttons if b["btn"] == "l1"]  # Only l1 is fan speed control

    entities = []
    coordinator = hass.data[DOMAIN][COORDINATOR]
    for b in fans:
        room_slug = slugify(b["device_name"])
        label_slug = slugify(b["label"])
        entity_id = f"number.iotics_{room_slug}_{label_slug}"

        entity = IoticsNumber(
                coordinator=coordinator,
                mqtt_client=mqtt_client,
                entity_id=entity_id,
                name=b["label"],
                device_name=b["device_name"],
                token=b["token"],
                btn=b["btn"],
                ip=b["ip"],
                unique_id=f"iotics_{room_slug}_{label_slug}",
            )
        coordinator.entities_by_id[entity.entity_id] = entity
        entities.append(entity)

    async_add_entities(entities)


class IoticsNumber(CoordinatorEntity, NumberEntity):
    """An Iotics fan speed control that shows under Devices & Services."""

    _attr_native_min_value = 0
    _attr_native_max_value = 4
    _attr_native_step = 1

    def __init__(
        self,
        coordinator,
        mqtt_client,
        entity_id: str,
        name: str,
        device_name: str,
        token: str,
        btn: str,
        ip: str,
        unique_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._mqtt_client = mqtt_client
        self.entity_id = entity_id
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._token = token
        self._btn = btn
        self._ip = ip

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, token)},
            name=device_name,
            manufacturer="Iotics",
            model="Iotics Smart Switch",
        )

    @property
    def native_value(self) -> float | None:
        """Return current fan speed from the coordinator's MQTT-updated map."""
        raw = self.coordinator.entity_state.get(self.entity_id, "0")
        try:
            return float(raw)
        except (ValueError, TypeError):
            return 0.0

    async def async_set_native_value(self, value: float) -> None:
        """Set the fan speed."""
        status = str(int(value))
        if status not in ("0", "1", "2", "3", "4"):
            _LOGGER.warning("Invalid fan speed: %s", value)
            return

        await self._send_mqtt_command(status)
        self.coordinator.entity_state[self.entity_id] = status
        self.async_write_ha_state()

    async def _send_mqtt_command(self, status: str) -> None:
        """Publish fan speed command to MQTT."""
        if not self._mqtt_client:
            _LOGGER.warning("MQTT not available for %s", self.entity_id)
            return
        topic = f"io/{self._token}/{self._btn}/sw"
        success = await self._mqtt_client.publish(topic, status)
        if success:
            _LOGGER.debug("MQTT published %s = %s", topic, status)
        else:
            _LOGGER.warning("MQTT publish failed for %s", topic)
