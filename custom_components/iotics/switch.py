"""
Switch platform for Iotics — real-time on/off toggles via coordinator entity.

Entity naming convention:
  - Regular buttons (b1-b7): switch.iotics_{room_slug}_{label_slug}
    Example: switch.iotics_kitchen_light
  - Fan toggles (f1): switch.iotics_{room_slug}_fan
    Example: switch.iotics_kitchen_fan
  - Fan speed (l1): SKIPPED here — handled by number.py

State management:
  - is_on reads from coordinator.entity_state (updated by MQTT handler)
  - async_turn_on/off sends HTTP command to device LAN IP, then updates state
  - CoordinatorEntity ensures state refresh on coordinator data updates

Device communication:
  - HTTP GET to http://<device_ip>/action?button=<btn>&status=<0|1>
  - Same endpoint the Iotics mobile app uses on LAN

Important:
  - entity_id is set explicitly to match coordinator entity_state keys
  - entities_by_id registration enables direct async_write_ha_state from MQTT handler
"""

from __future__ import annotations
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN, COORDINATOR
from .iotics_api import slugify, IoticsApiClient

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Iotics switch entities."""
    coordinator = hass.data[DOMAIN][COORDINATOR]
    devices = coordinator.data
    buttons = IoticsApiClient.extract_buttons(devices)

    entities = []
    for b in buttons:
        room_slug = slugify(b["device_name"])

        if b["is_fan"]:
            if b["btn"] == "l1":
                continue  # speed control, not a switch
            unique_id = f"iotics_{room_slug}_fan"
            name = f"{b['device_name']} Fan"
        else:
            label_slug = slugify(b["label"])
            unique_id = f"iotics_{room_slug}_{label_slug}"
            name = b["label"]

        entity = IoticsSwitch(
            coordinator=coordinator,
            entity_id_str=f"switch.{unique_id}",
            name=name,
            device_name=b["device_name"],
            token=b["token"],
            btn=b["btn"],
            ip=b["ip"],
            unique_id=unique_id,
            is_fan=b["is_fan"],
        )
        entities.append(entity)

        # Store entity reference for direct state writes
        coordinator.entities_by_id[entity.entity_id] = entity

    async_add_entities(entities)


class IoticsSwitch(CoordinatorEntity, SwitchEntity):
    """Iotics switch/plug/light with real-time state."""

    def __init__(
        self,
        coordinator,
        entity_id_str: str,
        name: str,
        device_name: str,
        token: str,
        btn: str,
        ip: str,
        unique_id: str,
        is_fan: bool = False,
    ) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._token = token
        self._btn = btn
        self._ip = ip
        self._entity_id_str = entity_id_str
        self.entity_id = entity_id_str
        self._is_fan = is_fan

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, token)},
            name=device_name,
            manufacturer="Iotics",
            model="Iotics Smart Switch",
        )

    @property
    def is_on(self) -> bool | None:
        """Return current state from coordinator's entity_state."""
        state = self._coordinator.entity_state.get(self._entity_id_str, "off")
        return state == STATE_ON

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        _LOGGER.warning("IoticsSwitch.turn_on: %s (ip=%s, btn=%s)", self._entity_id_str, self._ip, self._btn)
        if self._ip:
            await self._send_command("1")
        self._coordinator.entity_state[self._entity_id_str] = "on"
        self.async_write_ha_state()
        # Record for MQTT token learning
        self._coordinator._last_toggled = {"eid": self._entity_id_str, "time": __import__("time").time()}

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        _LOGGER.warning("IoticsSwitch.turn_off: %s", self._entity_id_str)
        if self._ip:
            await self._send_command("0")
        self._coordinator.entity_state[self._entity_id_str] = "off"
        self.async_write_ha_state()
        # Record for MQTT token learning
        self._coordinator._last_toggled = {"eid": self._entity_id_str, "time": __import__("time").time()}

    async def _send_command(self, status: str) -> None:
        """Send HTTP command to the physical Iotics device."""
        import urllib.request
        loop = __import__("asyncio").get_event_loop()
        url = f"http://{self._ip}/action?button={self._btn}&status={status}"
        try:
            await loop.run_in_executor(
                None, lambda: urllib.request.urlopen(url, timeout=5).read()
            )
            _LOGGER.debug("HTTP command sent: %s", url)
        except Exception as err:
            _LOGGER.error("HTTP command to %s failed: %s", url, err)
