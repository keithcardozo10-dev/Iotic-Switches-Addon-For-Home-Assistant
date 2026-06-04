"""Iotics Smart Home — custom integration for Home Assistant.

Architecture (exact Mac replica):
  1. Cloud API login + device discovery (at startup and every 5 min)
  2. MQTT WSS to AWS IoT for realtime push updates
  3. HA REST API sync for initial states (via supervisor endpoint)
  4. call_service interception for toggle --> device command

No cloud polling loops. No dashboard generation. Pure realtime.
"""

from __future__ import annotations
import asyncio
import logging
import os
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .iotics_api import IoticsApiClient, slugify, is_fan_button
from .mqtt_client import IoticsMqttClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SWITCH, Platform.NUMBER]
DOMAIN = "iotics"
UPDATE_INTERVAL = timedelta(seconds=300)

COORDINATOR = "coordinator"
MQTT_CLIENT = "mqtt_client"
API_CLIENT = "api_client"
MQTT_TASK = "mqtt_task"

# Supervisor socket helper
_SOCKET_PATH = "/run/supervisor/core.sock"


def _push_state_to_supervisor(eid: str, state_val: str) -> bool:
    import json, socket, http.client
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token or not os.path.exists(_SOCKET_PATH):
        return False
    try:
        domain = eid.split(".")[0]
        attrs = {"source": "iotics_mqtt"}
        if domain == "number":
            attrs["icon"] = "mdi:fan-speed"
        body = json.dumps({"state": state_val, "attributes": attrs})
        conn = http.client.HTTPConnection("localhost")
        conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.sock.connect(_SOCKET_PATH)
        conn.request("POST", f"/api/states/{eid}",
            body=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        conn.close()
        return True
    except Exception:
        return False


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the Iotics integration from a config entry."""
    _LOGGER.info("=== IOTICS ASYNC_SETUP_ENTRY STARTED ===")
    hass.data.setdefault(DOMAIN, {})

    # Create API client
    email = entry.data["email"]
    password = entry.data["password"]
    appid = entry.data.get("appid", "696f74696373617070")
    api = IoticsApiClient(email, password, appid)
    hass.data[DOMAIN][API_CLIENT] = api

    # Create MQTT client
    mqtt = IoticsMqttClient()
    hass.data[DOMAIN][MQTT_CLIENT] = mqtt

    # Coordinator: cloud API polling for backup + initial discovery
    async def async_update_data():
        _LOGGER.debug("Coordinator async_update_data called")
        try:
            devices = await hass.async_add_executor_job(api.discover_direct)
            if not devices:
                _LOGGER.error("No devices found from Iotics cloud API")
                raise UpdateFailed("No devices found from Iotics cloud API")

            # Rebuild entity_state from fresh cloud data (backup sync)
            if hasattr(coordinator, 'entity_state'):
                fresh_buttons = IoticsApiClient.extract_buttons(devices)
                changed = 0
                for b in fresh_buttons:
                    room_slug = slugify(b["device_name"])
                    label_slug = slugify(b["label"])
                    raw_status = b["status"]

                    if b["is_fan"]:
                        if b["btn"] == "l1":
                            eid = f"number.iotics_{room_slug}_{label_slug}"
                        elif b["btn"] == "f1":
                            eid = f"switch.iotics_{room_slug}_fan"
                        else:
                            continue
                    else:
                        eid = f"switch.iotics_{room_slug}_{label_slug}"

                    new_val = raw_status if b["btn"] == "l1" else ("on" if raw_status == "1" else "off")
                    if eid in coordinator.entity_state and coordinator.entity_state[eid] != new_val:
                        coordinator.entity_state[eid] = new_val
                        changed += 1

                if changed:
                    _LOGGER.debug("Cloud sync updated %d entities from cloud", changed)
                    if hasattr(coordinator, 'async_update_listeners'):
                        coordinator.async_update_listeners()

            # Rebuild IP->device mapping from fresh cloud data
            if hasattr(coordinator, 'ip_to_dev'):
                ip_to_dev = {}
                for d in devices:
                    ip = d.get("ip", "")
                    if ip:
                        ip_to_dev[ip] = d
                coordinator.ip_to_dev = ip_to_dev

            return devices
        except Exception as err:
            _LOGGER.error("Coordinator fetch failed: %s", err, exc_info=True)
            raise UpdateFailed(f"Error fetching devices: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass, _LOGGER, name="iotics",
        update_method=async_update_data,
        update_interval=UPDATE_INTERVAL,
    )

    # First fetch
    try:
        await coordinator.async_config_entry_first_refresh()
        _LOGGER.info("Coordinator first refresh: %d devices", len(coordinator.data))
    except Exception as e:
        _LOGGER.error("Coordinator first refresh FAILED: %s", e, exc_info=True)
        return False
    devices = coordinator.data

    _LOGGER.info("Registering %d devices in HA registry...", len(devices))
    dev_reg = dr.async_get(hass)
    device_ids: dict[str, str] = {}
    for dev in devices:
        token = dev.get("hardwaretoken") or dev.get("mac", "").replace(":", "")
        hwname = dev.get("hardwarename") or dev.get("room") or token
        connections = set()
        if dev.get("ip"):
            connections.add(("ip", dev["ip"]))
        device = dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, token)},
            name=hwname,
            manufacturer="Iotics",
            model="Iotics Smart Switch",
            sw_version="1.0",
            connections=connections,
        )
        device_ids[token] = device.id
    _LOGGER.info("Registered %d devices", len(device_ids))

    # Entity state map + lookups
    entity_state: dict[str, str] = {}
    mqtt_lookup: dict[str, dict[str, Any]] = {}
    ip_to_dev: dict[str, dict[str, Any]] = {}
    ip_to_entity: dict[str, dict[str, str]] = {}

    buttons = IoticsApiClient.extract_buttons(devices)
    _LOGGER.info("Extracted %d buttons from devices", len(buttons))
    for b in buttons:
        room_slug = slugify(b["device_name"])
        label_slug = slugify(b["label"])
        raw_status = b["status"]
        ip = b.get("ip", "")
        token = b.get("token", "")
        btn = b.get("btn", "")

        # Track device by IP
        if ip and token:
            ip_to_dev[ip] = {"token": token, "name": b["device_name"]}
            if ip not in ip_to_entity:
                ip_to_entity[ip] = {}

        if b["is_fan"]:
            if btn == "l1":
                eid = f"number.iotics_{room_slug}_{label_slug}"
                entity_state[eid] = raw_status
                key = f"{token}_{btn}"
                mqtt_lookup[key] = {"eid": eid, "is_fan": True, "ip": ip, "btn": btn}
                if ip:
                    ip_to_entity[ip][btn] = eid
            elif btn == "f1":
                eid = f"switch.iotics_{room_slug}_fan"
                entity_state[eid] = "on" if raw_status == "1" else "off"
                key = f"{token}_{btn}"
                mqtt_lookup[key] = {"eid": eid, "is_fan": False, "ip": ip, "btn": btn}
                if ip:
                    ip_to_entity[ip][btn] = eid
        else:
            eid = f"switch.iotics_{room_slug}_{label_slug}"
            entity_state[eid] = "on" if raw_status == "1" else "off"
            key = f"{token}_{btn}"
            mqtt_lookup[key] = {"eid": eid, "is_fan": False, "ip": ip, "btn": btn}
            if ip:
                ip_to_entity[ip][btn] = eid

    _LOGGER.info("Built entity_state=%d, mqtt_lookup=%d, ip_to_dev=%d, ip_to_entity=%d",
                 len(entity_state), len(mqtt_lookup), len(ip_to_dev), sum(len(v) for v in ip_to_entity.values()))

    coordinator.device_ids = device_ids
    coordinator.entity_state = entity_state
    coordinator.mqtt_lookup = mqtt_lookup
    coordinator.entities_by_id = {}
    coordinator.mqtt_token_to_eid = {}
    coordinator.ip_to_dev = ip_to_dev
    coordinator.ip_to_entity = ip_to_entity
    coordinator._mqtt_token_ip = {}

    # Store coordinator in hass.data
    hass.data[DOMAIN][COORDINATOR] = coordinator

    # Forward to entity platforms
    _LOGGER.info("Forwarding setup to entity platforms...")
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.info("Entity platform setups done")

    # Push initial states via Supervisor Unix socket
    def _push_initial_states():
        pushed = 0
        for eid, state_val in entity_state.items():
            if _push_state_to_supervisor(eid, state_val):
                pushed += 1
        _LOGGER.info("Pushed %d/%d initial states via socket", pushed, len(entity_state))

    await hass.async_add_executor_job(_push_initial_states)

    # --- MQTT setup ---
    mqtt.add_subscription("io/+/#")
    _mqtt_token_map: dict[str, str] = {}

    def _on_mqtt_message(topic: str, payload: str):
        nonlocal _mqtt_token_map
        parts = topic.split("/")
        mqtt_token = parts[1]

        # ----- Handle network messages: learn MQTT token <-> IP mapping -----
        if len(parts) == 3 and parts[2] == "network":
            try:
                ip = payload.split(",")[0].strip()
                if ip and ip in coordinator.ip_to_dev:
                    dev_info = coordinator.ip_to_dev[ip]
                    # Store direct mqtt_token -> api_token mapping
                    api_token = dev_info.get("token", "")
                    if api_token:
                        coordinator.mqtt_token_to_eid[mqtt_token] = api_token
                    # Also store mqtt_token -> IP for reverse IP lookup
                    coordinator._mqtt_token_ip[mqtt_token] = ip
                    _LOGGER.info("MQTT: mapped mqtt_token=%s -> IP=%s -> api_token=%s (%s)",
                                 mqtt_token, ip, api_token, dev_info.get("name", "?"))
            except Exception:
                pass
            return

        # ----- Handle hw messages: real-time state update -----
        if len(parts) < 3 or parts[-1] != "hw":
            return

        val = payload.strip()
        if val not in ("0", "1", "2", "3", "4"):
            return

        btn = parts[2]

        # Fan speed (l1) keeps raw value; switches convert to on/off
        if btn == "l1":
            new_state = val
        elif val in ("0", "1"):
            new_state = "on" if val == "1" else "off"
        else:
            new_state = val

        # Strategy 1: direct mqtt_lookup (MQTT token == API token for Keith's devices)
        direct_key = f"{mqtt_token}_{btn}"
        direct_info = coordinator.mqtt_lookup.get(direct_key)
        if direct_info:
            eid = direct_info["eid"]
            coordinator.entity_state[eid] = new_state
            if eid in coordinator.entities_by_id:
                try:
                    hass.loop.call_soon_threadsafe(coordinator.entities_by_id[eid].async_write_ha_state)
                    _LOGGER.debug("MQTT: %s = %s (direct MQTT lookup)", eid, new_state)
                except Exception:
                    pass
            return

        # Strategy 2: mqtt_token -> api_token map (learned from network msgs)
        api_token = coordinator.mqtt_token_to_eid.get(mqtt_token, "")
        if api_token:
            lookup_key = f"{api_token}_{btn}"
            info = coordinator.mqtt_lookup.get(lookup_key)
            if info:
                eid = info["eid"]
                coordinator.entity_state[eid] = new_state
                if eid in coordinator.entities_by_id:
                    try:
                        hass.loop.call_soon_threadsafe(coordinator.entities_by_id[eid].async_write_ha_state)
                        _LOGGER.debug("MQTT: %s = %s (API token map)", eid, new_state)
                    except Exception:
                        pass
                return

        # Strategy 3: mqtt_token -> IP -> ip_to_entity
        mqtt_token_ip = coordinator._mqtt_token_ip.get(mqtt_token)
        if mqtt_token_ip and mqtt_token_ip in coordinator.ip_to_entity:
            btn_map = coordinator.ip_to_entity[mqtt_token_ip]
            eid = btn_map.get(btn)
            if eid:
                coordinator.entity_state[eid] = new_state
                if eid in coordinator.entities_by_id:
                    try:
                        hass.loop.call_soon_threadsafe(coordinator.entities_by_id[eid].async_write_ha_state)
                        _LOGGER.debug("MQTT: %s = %s (IP reverse)", eid, new_state)
                    except Exception:
                        pass
                return

        # Strategy 3: Previously learned token
        known_eid = _mqtt_token_map.get(f"{mqtt_token}_{btn}")
        if known_eid and known_eid in coordinator.entity_state:
            coordinator.entity_state[known_eid] = new_state
            if known_eid in coordinator.entities_by_id:
                try:
                    hass.loop.call_soon_threadsafe(coordinator.entities_by_id[known_eid].async_write_ha_state)
                    _LOGGER.debug("MQTT: %s = %s (learned token)", known_eid, new_state)
                except Exception:
                    pass
            return

        # Strategy 4 (DISABLED): Fallback token learning caused false positives from
        # neighboring Iotics devices on the shared AWS IoT account.
        # Keith's devices use hardwaretoken = MQTT token, so Strategy 1 should always work.
        pass

    mqtt.set_message_callback(_on_mqtt_message)

    _LOGGER.info("Iotics: starting MQTT connection...")
    mqtt_task = asyncio.create_task(mqtt.connect())
    hass.data[DOMAIN][MQTT_TASK] = mqtt_task
    _LOGGER.info("MQTT connection task created")

    # --- call_service listener for fan speed ---
    async def _call_service_listener(event):
        if event.data.get("domain") != "number":
            return
        service = event.data.get("service", "")
        target = event.data.get("target", {})
        entity_ids = target.get("entity_id", [])
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
        for eid in entity_ids:
            if not eid.startswith("number.iotics_"):
                continue
            data = event.data.get("service_data", {})
            desired = str(int(data.get("value", 0)))
            if desired not in ("0", "1", "2", "3", "4"):
                continue
            coordinator.entity_state[eid] = desired
            _LOGGER.info("call_service number: %s -> %s", eid, desired)
            ip = ""
            btn = ""
            for key, info in coordinator.mqtt_lookup.items():
                if info["eid"] == eid:
                    ip = info.get("ip", "")
                    btn = info.get("btn", "")
                    break
            if ip and btn:
                try:
                    import urllib.request
                    req = urllib.request.Request(
                        f"http://{ip}/action?button={btn}&status={desired}"
                    )
                    urllib.request.urlopen(req, timeout=3)
                    _LOGGER.info("Iotics: sent fan cmd %s -> %s btn=%s speed=%s", eid, ip, btn, desired)
                except Exception as err:
                    _LOGGER.error("Iotics: fan cmd failed for %s: %s", eid, err)

    hass.bus.async_listen("call_service", _call_service_listener)
    _LOGGER.info("=== IOTICS ASYNC_SETUP_ENTRY COMPLETE ===")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the Iotics config entry."""
    _LOGGER.info("Iotics: unloading config entry")
    mqtt: IoticsMqttClient = hass.data[DOMAIN].get(MQTT_CLIENT)
    if mqtt:
        await mqtt.disconnect()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.pop(DOMAIN)
    return unload_ok
