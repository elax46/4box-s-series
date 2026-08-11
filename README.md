# HA Custom Integration: 4box S Series

A native Home Assistant integration for **Finder / 4box "S Series"** MQTT
devices — starting with the **P40 Smart (P40S)** socket, and built to grow
into the rest of the family (motorized shutters, pulsed outputs,
thermostats) documented in the vendor's MQTT guide, all sharing the same
firmware and topic scheme (Morpheos ESP32IoT, protocol Rev. 3).

No cloud, no gateway required: these devices connect directly to your Wi-Fi
and speak plain MQTT. This integration talks to them over your own MQTT
broker via Home Assistant's built-in **MQTT integration**.

## Features

- **Switch** entity per relay channel (on/off/toggle), state pushed live by
  the device (`local_push`, no polling).
- **Sensors**: instantaneous power (W), current (A), mains voltage (V),
  internal temperature (°C) — all pushed spontaneously by the firmware.
- **Energy sensor** compatible with the Home Assistant **Energy dashboard**
  (`device_class: energy`, `state_class: total_increasing`), obtained by
  periodically polling `energyActive=RELAY<n>` since the firmware doesn't
  push this value on its own.
- **Diagnostic binary sensors**: overcurrent and overtemperature.
- **Availability**: entities go unavailable automatically when the device's
  MQTT LWT (`<ID>/connect`) reports `false`.
- Single-channel (P40S, M048B/C/D, M054F) and dual-channel (M053B, "doppia
  luce") devices are both supported from the same config flow.
- Device model is read straight from the device ID (`<model>-<MAC>`), so
  new relay-family models need no code change to show up correctly in the
  device registry.
- UI-based config flow — no YAML required.

### Not (yet) covered

- Motorized shutters/awnings (`motor=UP/DOWN/STOP/MOVE&perc=/TILT&perc=`)
  — planned as a `cover` platform.
- Uniko Push pulsed outputs (`pulsetime=PULSETIME<ron>:<ms>&&RELAY1:ON`)
  — planned as a `button`/`switch` helper.
- Thermostats (`thermostatMode`, `tS`, `sht4x`) — planned as a `climate`
  platform.

The architecture (topic templates in `const.py`, one platform module per
entity type, model resolved dynamically from the device ID) is built so
these can be added without touching the existing switch/sensor code.
Contributions welcome.

## Requirements

- Home Assistant 2024.1 or newer.
- The core **MQTT** integration already configured and connected to the
  same broker your devices publish to.
- A device already provisioned on your Wi-Fi/MQTT broker (provisioning
  itself — BLE setup, broker credentials — is done via the vendor's own
  app/process and is out of scope for this integration).

## Installation

### Via HACS (recommended once published)

1. HACS → Integrations → the "⋮" menu → *Custom repositories* → add this
   repository URL with category *Integration*.
2. Search for "4box S Series" and install.
3. Restart Home Assistant.

### Manual

1. Copy `custom_components/s_series_mqtt` into your Home Assistant
   `config/custom_components/` folder.
2. Restart Home Assistant.

## Configuration

1. Make sure the MQTT integration is set up first.
2. Settings → Devices & Services → **Add Integration** → search for
   "4box S Series (MQTT)".
3. Fill in:
   - **Device ID**: exactly as printed on the device / in its pairing
     info, format `<model>-<MAC>`, e.g. `M048B-30AEA4A6D460`.
   - **Friendly name** (optional).
   - **Number of relay channels**: `1` for a standard P40S, `2` only for an
     M053B configured as dual-light.
   - **Device has an energy meter**: leave enabled for P40S/M048B/M048C/
     M048D/M053B (all of which expose `power`/`current`/`energyActive`);
     disable for devices without energy metering (e.g. M054F).
   - **Energy poll interval**: how often to query `energyActive=RELAY<n>`,
     default 300s. Lower it for a more granular Energy dashboard, at the
     cost of more MQTT/CPU chatter.
4. Repeat for each physical device.

## Entities created (single-channel P40S example)

| Entity | Platform | Notes |
|---|---|---|
| `switch.<device>_socket` | switch | on/off/toggle |
| `sensor.<device>_power` | sensor | W, live |
| `sensor.<device>_current` | sensor | A, live |
| `sensor.<device>_voltage` | sensor | V, every ~15 min |
| `sensor.<device>_temperature` | sensor | °C, every ~15 min |
| `sensor.<device>_energy` | sensor | kWh, polled — usable in the Energy dashboard |
| `binary_sensor.<device>_overcurrent` | binary_sensor | diagnostic |
| `binary_sensor.<device>_overtemperature` | binary_sensor | diagnostic |

## Architecture

```
custom_components/s_series_mqtt/
├── __init__.py        # entry setup/teardown, forwards to platforms
├── config_flow.py      # UI config + options flow
├── const.py             # domain, topic templates, defaults
├── coordinator.py     # DataUpdateCoordinator for the polled energy counter
├── switch.py             # relay switch entities (MQTT push)
├── sensor.py             # power/current/voltage/temperature/energy sensors
├── binary_sensor.py    # overcurrent / overtemperature
├── utils.py               # command payload builders, model-from-ID helper
├── manifest.json
├── strings.json / translations/en.json
```

Design notes:

- **Everything except energy is `local_push`.** The firmware already
  publishes relay state, power, current, voltage, temperature and fault
  flags spontaneously on `<ID>/stat/...`, so those entities simply
  subscribe once in `async_added_to_hass` — no polling loop.
- **Energy is request/response on a shared topic.** The firmware only
  reports the cumulative kWh counter when asked (`energyActive=RELAY<n>`
  on `<ID>/cmnd`), and replies once on the single shared `<ID>/info`
  topic — the same topic used for every other command's response. The
  `SSeriesEnergyCoordinator` serializes its own requests and only trusts
  the next `/info` message that arrives after it published a request,
  with a timeout.
  - **Known limitation**: if you (or another tool) manually publish a
    command to the same device while the coordinator's request is in
    flight, the coordinator may pick up that unrelated response instead
    of the energy value for one polling cycle. In practice this only
    matters if you're scripting extra manual commands against the same
    device outside Home Assistant; it self-corrects on the next poll
    since it will simply fail to parse an unexpected payload as a number
    and log the mismatch for that cycle.
- **Model comes from the device ID**, not a hardcoded constant — so
  `M048B-30AEA4A6D460` shows up in the device registry as model `M048B`,
  a P40S shows up with its own model code, etc., with zero extra code per
  new relay-family model.
- **Availability** is derived from the device's own MQTT LWT topic, exactly
  the mechanism the vendor firmware is designed around.

## Development setup

You don't need real hardware to develop against this integration — a
Home Assistant instance plus a local MQTT broker plus the included device
simulator is enough.

### 1. Start Home Assistant + Mosquitto

```bash
cd dev
docker compose up
```

This starts:
- `mosquitto` on `localhost:1883` (open, no auth — dev only).
- Home Assistant on `http://localhost:8123`, with `../custom_components`
  bind-mounted read-write into `config/custom_components`, so edits to the
  integration source are picked up on the next HA restart.

Finish the Home Assistant onboarding wizard, then:

1. Settings → Devices & Services → Add Integration → **MQTT** → broker
   `mosquitto`, port `1883`, no credentials.
2. Settings → Devices & Services → Add Integration → **4box S Series
   (MQTT)** → device ID `M048B-30AEA4A6D460` (or whatever you pass to the
   simulator below).

### 2. Run the device simulator

In a separate terminal, on your host (not inside the containers):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install paho-mqtt
python scripts/simulate_p40s.py --host localhost --port 1883 \
    --device-id M048B-30AEA4A6D460
```

The simulator behaves like a real P40S: it announces itself online via
LWT, reacts to `action=ON/OFF/TOGGLE`, replies to `power=RELAY1`,
`current=RELAY1`, `energyActive=RELAY1`, and streams periodic
power/current/voltage/temperature telemetry. Toggle the switch entity in
Home Assistant and watch the simulator log the incoming command, or run
`mosquitto_pub`/`mosquitto_sub` directly against `localhost:1883` to poke
at it manually.

### 3. Iterate

After editing any file under `custom_components/s_series_mqtt/`, restart
Home Assistant (`docker compose restart homeassistant`, or from the HA UI:
Developer Tools → YAML → *Restart*) to reload the integration.

### Code style

Plain `async`/`await`, standard Home Assistant entity patterns
(`DataUpdateCoordinator` for polled data, direct MQTT subscription for
pushed data). No external dependencies beyond Home Assistant core and its
bundled MQTT integration.

Contributions for any of the above are very welcome — open an issue or PR.

## Disclaimer

This is an independent, community-built integration. It is not affiliated
with, endorsed by, or supported by Finder S.p.A. or 4box. All product
names and trademarks belong to their respective owners.
