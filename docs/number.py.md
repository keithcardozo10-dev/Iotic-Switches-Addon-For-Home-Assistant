# `number.py` — Fan Speed Control Platform

## Role

Defines the `number` entity platform for Iotics fan speed controls. Creates `NumberEntity` instances for fan buttons (l1, f1) that allow setting speed from 0 (off) to 4 (max).

## Entity Properties

| Property | Value |
|----------|-------|
| `native_min_value` | 0 |
| `native_max_value` | 4 |
| `native_step` | 1 |
| Entity ID format | `number.iotics_{room_slug}_{label_slug}` |

## MQTT Command Publishing

Unlike switch toggles (which use HTTP commands), fan speed changes are sent via **MQTT publish**:

```python
topic = f"io/{token}/{btn}/sw"
payload = status  # "0", "1", "2", "3", or "4"
```

The device receives the MQTT message via AWS IoT and sets its fan speed accordingly.

### Why MQTT for fan speeds?

Fan speed commands (0-4) use the `io/{token}/{btn}/sw` topic pattern. This is the standard Iotics protocol for fan speed control. HTTP commands on the `/action` endpoint only support binary on/off states.

## State Reading

State is read from the coordinator's `entity_state` dict as a float:

```python
@property
def native_value(self) -> float | None:
    raw = self.coordinator.entity_state.get(self.entity_id, "0")
    try:
        return float(raw)
    except (ValueError, TypeError):
        return 0.0
```

## How Fan Speed Changes Flow

```
User drags slider to 3 in HA
  → async_set_native_value(3.0)
    → status = "3"
    → MQTT publish: io/{token}/{btn}/sw = "3"
    → Update entity_state dict
    → async_write_ha_state()

Physical fan responds
  -> MQTT message: io/{token}/{btn}/hw = "3"
    -> MQTT handler validates: btn=="l1" -> new_state=val (raw value, NOT "on"/"off")
    -> O(1) mqtt_lookup -> find eid
    -> Update entity_state[eid] = "3"
    -> call_soon_threadsafe(entity.async_write_ha_state) -- via entities_by_id

## Critical: Entities by ID Registration

**Without this, MQTT updates are invisible to the dashboard.**

The MQTT handler checks `if eid in coordinator.entities_by_id` before calling
`async_write_ha_state()`. If number.py never registers its entities there,
the entity_state is updated but the entity's `native_value` is never re-read
by HA -- the slider appears frozen.

```python
# In async_setup_entry:
entity = IoticsNumber(...)
coordinator.entities_by_id[entity.entity_id] = entity  # <-- REQUIRED
entities.append(entity)
```

This was the root cause of "fan speed slider doesn't move when physically
changed" (June 2026 fix). switch.py already had the registration line,
but number.py was missing it.
