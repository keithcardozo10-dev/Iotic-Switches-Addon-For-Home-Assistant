# Iotics Switches Addon for Home Assistant

[![GitHub release (latest by date)](https://img.shields.io/github/v/release/keithcardozo10-dev/Iotics-Switches-Addon-For-Home-Assistant)](https://github.com/keithcardozo10-dev/Iotics-Switches-Addon-For-Home-Assistant/releases)
[![GitHub all releases](https://img.shields.io/github/downloads/keithcardozo10-dev/Iotics-Switches-Addon-For-Home-Assistant/total)](https://github.com/keithcardozo10-dev/Iotics-Switches-Addon-For-Home-Assistant/releases)

A **Home Assistant Custom Integration** (custom_component) that connects [Iotics](https://www.iotics.io) smart home WiFi switches directly into HA. Automatic device discovery, real-time state sync via MQTT, and full dashboard control — no cloud polling, no bridge containers, no YAML packages.

**What makes this different:** Your Iotics switches become first-class HA entities. They appear under Settings > Devices & Services as proper devices. You can use them in automations, trigger them from Zigbee/WiFi sensors, set fan speeds, and see state changes instantly — without relying on the Iotics cloud for every state read.

---

## Two Ways to Install

### Option A: Custom Integration (Recommended)

The `custom_components/iotics/` folder in this repo is a native HA Custom Integration. It runs as part of HA itself — no Docker, no add-on manager, no separate container.

**Install in 30 seconds:**

1. Copy the `custom_components/iotics/` folder into your HA `config/custom_components/` directory
2. Restart Home Assistant
3. Go to Settings > Devices & Services > Add Integration > Search "Iotics Smart Home"
4. Enter your Iotics email and password

That's it. Your devices appear automatically.

### Option B: HA Add-on (Legacy)

The `bridge.py` + `Dockerfile` + `config.yaml` in this repo also work as a traditional HA add-on (add the repo via the add-on store). This is the original approach and is still fully functional.

---

## Why Custom Integration vs Add-on?

| Feature | Custom Integration (`custom_components/`) | HA Add-on (`bridge.py` + Docker) |
|---------|------------------------------------------|-----------------------------------|
| Installation | Copy folder, restart HA | Add repo to add-on store, install, configure |
| Runs as | Part of HA core | Separate Docker container |
| Entities appear under | Devices & Services (proper integration) | input_boolean / input_number (manual) |
| Real-time updates | MQTT WSS push (instant) | Cloud API poll (every 5s) |
| Resource usage | None (shares HA process) | ~100MB RAM container |
| Dashboard | Use your own Lovelace setup | Auto-generated dashboard |
| Dependencies | paho-mqtt (installed with integration) | paho-mqtt + websockets (in container) |
| Fan speed support | Native NumberEntity (0-4 slider) | input_number workaround |

**Choose Custom Integration if:**
- You want devices under Settings > Devices & Services with proper entities
- You want real-time state updates via MQTT (instant, no polling delay)
- You prefer no extra containers running
- You want to keep your existing Lovelace dashboards

**Choose Add-on if:**
- You want an auto-generated dashboard with room grouping
- You prefer the add-on store UI for management
- You need entity creation via HA REST API

---

## How It Works

```
                    +-----------------------------------------+
                    |         Home Assistant (HA)              |
                    |                                          |
                    |  +----------------------------------+    |
                    |  |  Iotics Custom Integration        |    |
                    |  |  (custom_components/iotics)       |    |
                    |  |                                   |    |
                    |  |  __init__.py  -- Coordinator      |    |
                    |  |  iotics_api.py -- Cloud API       |    |
                    |  |  mqtt_client.py -- MQTT WSS       |    |
                    |  |  switch.py ---- Switch Entity     |    |
                    |  |  number.py ---- Fan Speed         |    |
                    |  +----------+-----------------------+    |
                    |             |                            |
                    +-------------+----------------------------+
                                  |
          +-----------------------+-----------------------+
          |                       |                       |
          v                       v                       v
  +----------------+     +----------------------+     +----------------+
  |  Iotics        |     |  AWS IoT MQTT        |     |  Iotics        |
  |  Cloud API     |     |  WSS (SigV4)         |     |  Devices       |
  |  (discovery)   |     |  (real-time push)    |     |  (WiFi LAN)    |
  +----------------+     +----------------------+     +----------------+
```

### Data Flow

1. **Startup:** Integration logs into Iotics cloud API -> discovers all devices and buttons -> creates entities in HA device registry -> connects MQTT WSS for real-time updates
2. **Real-time updates:** Physical Iotics switch pressed -> device publishes to AWS IoT MQTT -> integration receives message -> updates entity state instantly (sub-second)
3. **Dashboard toggles:** Click a toggle in HA -> integration sends HTTP command directly to the device's local IP -> device responds -> MQTT confirms the state change
4. **Fan speed control:** Drag slider -> integration publishes MQTT command to AWS IoT -> device receives and sets fan speed -> MQTT confirms the new speed
5. **Backup sync:** Coordinator polls cloud API every 5 minutes to catch any missed state changes (redundant — MQTT push handles everything in real-time)

### No Cloud Polling Loop

Unlike typical smart home bridges, the custom integration does NOT poll HA's REST API for state changes. Instead:
- **Outbound** (HA -> device): Direct HTTP commands on the LAN, or MQTT publish via AWS IoT
- **Inbound** (device -> HA): MQTT WSS push from AWS IoT

The only polling is a 5-minute cloud API check as a safety net. The real-time path is MQTT push.

---

## How to Add the Custom Integration

### Step 1: Prerequisites

- Home Assistant (any installation: HAOS, Docker, Core, Supervised)
- An active Iotics account with devices registered
- An SSH or SMB connection to your HA config folder

### Step 2: Install the Custom Integration

```bash
# SSH into your HA host
ssh hassio@<ha-ip>

# Create custom_components directory if it doesn't exist
mkdir -p /config/custom_components/

# Copy the iotics folder from this repo
# (Option 1: via SCP from your computer)
scp -r custom_components/iotics/ hassio@<ha-ip>:/config/custom_components/iotics/

# (Option 2: via git clone on the HA host)
cd /config/custom_components/
git clone https://github.com/keithcardozo10-dev/Iotics-Switches-Addon-For-Home-Assistant.git temp
cp -r temp/custom_components/iotics/ .
rm -rf temp
```

Or copy the `custom_components/iotics/` folder via SMB / HA Samba add-on to `/config/custom_components/iotics/`.

### Step 3: Restart HA

Go to Settings > System > Restart, or use the CLI:
```bash
ha core restart
```

### Step 4: Add the Integration

1. Go to **Settings > Devices & Services**
2. Click **+ Add Integration** (bottom right)
3. Search for **"Iotics Smart Home"**
4. Enter your:
   - **Email**: Your Iotics account email
   - **Password**: Your Iotics account password
   - **App ID**: Leave as default (`696f74696373617070`)
5. Click **Submit**

If successful, you'll see a confirmation. Your devices appear within seconds.

### Step 5: Set Up Your Dashboard

The integration does NOT auto-generate a dashboard. Add entities manually to your Lovelace dashboard:

1. Go to your dashboard -> Edit Dashboard -> + Add Card
2. Choose **Entities** card
3. Search for `iotics` to see all available entities
4. Add the ones you want
5. You can group by room using card section dividers

Example Lovelace YAML for a room:
```yaml
type: entities
title: Kitchen
entities:
  - switch.iotics_kitchen_light
  - switch.iotics_kitchen_fan
  - number.iotics_kitchen_fan_speed
  - switch.iotics_kitchen_socket
```

---

## How to Add the HA Add-on (Legacy)

### Prerequisites
- Home Assistant OS or Supervised (with add-on support)
- Iotics devices on the same LAN

### Installation
1. Go to **Settings > Add-ons > Add-on Store**
2. Click the three dots (...) > **Repositories**
3. Add: `https://github.com/keithcardozo10-dev/Iotics-Switches-Addon-For-Home-Assistant`
4. Click **Add**
5. Find **Iotics Switches Addon** in the store and click **Install**
6. Go to **Configuration** tab, enter your email and password
7. Go to **Info** tab, click **Start**

---

## Features

| Feature | Supported | Notes |
|---------|-----------|-------|
| Auto device discovery | Yes | Via Iotics cloud API |
| Real-time state updates | Yes | Via MQTT WSS to AWS IoT (sub-second) |
| On/off toggle | Yes | HTTP command to device LAN IP |
| Fan speed control | Yes | 0-4 via MQTT publish |
| Fan on/off toggle | Yes | Separate switch entity (f1 button) |
| Multiple buttons per device | Yes | b1-b7, l1 (speed), f1 (fan toggle) |
| Device registry | Yes | Appears under Settings > Devices & Services |
| No polling loop | Yes | MQTT push + 5min backup poll |
| Re-auth on session expiry | Yes | Via config flow reauth |
| Survives HA restart | Yes | Automatic |
| Manual entity restore | No | Integration creates entities dynamically |
| Dashboard auto-generation | No | Add-on only |

---

## Entity Reference

### Switch Entities (lights, sockets, fan toggles)
```
switch.iotics_{room_slug}_{label_slug}      # Regular buttons (b1-b7)
switch.iotics_{room_slug}_fan                 # Fan toggles (f1)
```

### Number Entities (fan speed)
```
number.iotics_{room_slug}_{label_slug}        # Fan speed (l1)
```

Example for a Kitchen device with Light (b1), Fan toggle (f1), and Fan speed (l1):

| Entity | State | Type |
|--------|-------|------|
| `switch.iotics_kitchen_kitchen_middle_light` | on/off | Switch |
| `switch.iotics_kitchen_fan` | on/off | Switch |
| `number.iotics_kitchen_fan_speed` | 0-4 | Number (slider) |

---

## File Structure

### Integration (custom_components/iotics/)
```
custom_components/iotics/
+-- __init__.py        # Entry point, coordinator, MQTT handler, call_service listener
+-- iotics_api.py      # Iotics cloud API client, SigV4 signing, button extraction
+-- mqtt_client.py     # MQTT WSS to AWS IoT with watchdog reconnect
+-- config_flow.py     # Setup UI flow (add/reauth integration)
+-- switch.py          # Switch entity platform (on/off toggles)
+-- number.py          # Number entity platform (fan speed 0-4)
+-- manifest.json      # Integration metadata
+-- strings.json       # UI translation strings
```

### Add-on (Legacy)
```
+-- config.yaml        # Add-on configuration schema
+-- Dockerfile         # Container build instructions
+-- run.sh             # Startup script
+-- bridge.py          # Main bridge logic
+-- logo.svg           # Add-on icon
```

### Repository Root
```
+-- custom_components/iotics/   # Custom integration (recommended)
+-- docs/                       # Per-file documentation
+-- README.md                   # This file
+-- config.yaml                 # Add-on config (legacy)
+-- Dockerfile                  # Container build (legacy)
+-- run.sh                      # Startup script (legacy)
+-- bridge.py                   # Original bridge (legacy)
+-- logo.svg                    # Add-on icon
+-- _config.yml                 # GitHub Pages config
```

---

## Troubleshooting

### Custom Integration

| Problem | Likely Cause | Fix |
|---------|-------------|-----|
| "Iotics Smart Home" not in integration list | Folder not in right place | Check custom_components/iotics/ exists, restart HA |
| Cannot connect during setup | Wrong email/password | Verify in Iotics mobile app |
| Entities don't appear | Cloud API issue | Check HA logs for "Iotics" messages |
| Switch toggles don't work | Device IP not reachable | Ensure Iotics devices are on the same LAN |
| Fan speed slider doesn't move when physically changed | MQTT handler converts l1 payload "1" to "on" | Fixed in v1.0.1 -- update custom_component |
| Fan speed slider stuck at 0 after physical change | entity_state not pushed to entity | Fixed in v1.0.1 -- entities_by_id added to number.py |
| States reverting after toggle | Old bridge still running | Disable old bridge scripts, restart HA |
| Fan shows "on" when physically off | f1/l1 fan state inversion | Fixed in v1.0.1 -- f1 and l1 handled separately |
| MQTT reconnect loops | AWS IoT session drop | Watchdog auto-reconnects within 5-15s |

### Add-on (Legacy)

| Problem | Likely Cause | Fix |
|---------|-------------|-----|
| Add-on doesn't appear in store | Wrong repo URL | Add https://github.com/keithcardozo10-dev/Iotics-Switches-Addon-For-Home-Assistant |
| No devices discovered | Wrong credentials | Double-check email/password in Configuration tab |
| MQTT stays disconnected | Internet access issue | Wait 30-60s for retry |
| "unavailable" entities | Startup sync delay | Wait 5-10 seconds |

### Known Issues and Fix History

**v1.0.0 -> v1.0.1 (June 2026):**
- Fixed: Fan speed payload "1" was converted to "on" instead of staying as "1"
- Fixed: number.py missing entities_by_id registration (slider never updated on physical change)
- Fixed: Fallback token learning disabled (caused state flaps from neighboring devices)
- Fixed: Fan switch (f1) and fan speed (l1) now properly separated
- Added: Direct MQTT lookup (Strategy 1) with hardwaretoken == MQTT token

**If upgrading from v1.0.0:**
```bash
# 1. Copy the updated files
scp -r custom_components/iotics/ hassio@<ha-ip>:/config/custom_components/

# 2. SSH in and clear cache
ssh hassio@<ha-ip>
sudo docker exec homeassistant sh -c 'rm -rf /config/custom_components/iotics/__pycache__ && touch /config/custom_components/iotics/*.py'

# 3. Restart HA
sudo docker restart homeassistant
```

### Debug Logs

Enable debug logging for the integration by adding to `configuration.yaml`:
```yaml
logger:
  default: info
  logs:
    custom_components.iotics: debug
```

Check logs via **Settings > System > Logs** or:
```bash
docker logs homeassistant --tail 100 | grep -i iotics
```

---

## Development

### File Documentation
Each source file has detailed documentation in `docs/`:

- [__init__.py.md](docs/__init__.py.md) -- Entry point, coordinator, state management
- [iotics_api.py.md](docs/iotics_api.py.md) -- Cloud API client, SigV4 signing
- [mqtt_client.py.md](docs/mqtt_client.py.md) -- MQTT WSS connection, watchdog
- [switch.py.md](docs/switch.py.md) -- Switch entity platform
- [number.py.md](docs/number.py.md) -- Fan speed number platform
- [config_flow.py.md](docs/config_flow.py.md) -- Setup UI flow
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) -- Full architecture guide

### Requirements
- Python 3.12+
- paho-mqtt >= 2.1.0
- Home Assistant 2025.1+ (tested on 2026.6.1)

### Local Development
The custom integration runs inside HA's Python environment. To test changes:

1. Edit files in `custom_components/iotics/`
2. Clear cache: `rm -rf __pycache__ && touch *.py`
3. Restart HA or reload the integration via Settings > Devices & Services
4. Check logs for errors

For the add-on version, rebuild the Docker container after changes.

### Deployment Tips
- Changes to .py files may not take effect immediately -- HA caches .pyc bytecode. Always clear __pycache__ and restart.
- If writing credential files to a remote Pi via SSH, use base64 encoding (echo | tee corrupts credential strings).
- When the Iotics cloud API returns "session get invalid", the integration retries with a fresh login (3 attempts, 2s delay).

---

## License

MIT
