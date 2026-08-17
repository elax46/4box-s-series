[![GitHub release (latest by date)](https://img.shields.io/github/v/release/elax46/4box-s-series?style=for-the-badge)](https://github.com/elax46/4box-s-series/releases/latest)
[![MIT License][mit-shield]][mit-license]
![GitHub last commit](https://img.shields.io/github/last-commit/elax46/4box-s-series?style=for-the-badge)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-yellow?style=for-the-badge&logo=buymeacoffee&logoColor=white)](https://www.buymeacoffee.com/elax46)
[![Coverage](https://img.shields.io/codecov/c/github/elax46/4box-s-series?style=for-the-badge)](https://codecov.io/gh/elax46/4box-s-series)

[mit-license]: https://opensource.org/licenses/MIT
[mit-shield]: https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge

# HA Custom Integration: 4box S Series

![logo](https://res.cloudinary.com/dcongin7u/image/upload/v1786703881/cover_ooeehp.jpg)

A native Home Assistant integration for **Finder / 4box "S Series"** MQTT
devices, covering all four device families documented in the vendor's MQTT
guide for the Morpheos ESP32IoT firmware (protocol Rev. 3):

| Family | Devices | HA platforms |
|---|---|---|
| Relay / socket | P40S, M048B, M048C, M048D, M054F, M053B (dual-light) | `switch`, `sensor` (power/current/voltage/temperature/energy), `binary_sensor` (overcurrent/overtemperature) |
| Motorized shutter | M053B (motor mode) | `cover` (position + optional tilt), `button` (calibrate), `sensor` (power) |
| Uniko Push | pulsed relay outputs | `button` (trigger pulse), `binary_sensor` (relay active) |
| Thermostat | Morpheos thermostat | `climate` (mode, setpoint, temperature, humidity) |

No cloud, no separate gateway required: these devices connect directly to
your Wi-Fi and speak plain MQTT to a broker of your choice. This
integration talks to them over that same broker via Home Assistant's
built-in **MQTT integration**.

> [!WARNING]  
> Please note that integration has currently only been tested with the P40S device. The implementation for other devices is based on the manufacturer's documentation and has not been tested due to the lack of physical devices.

## Setting up the physical device

Before Home Assistant can see anything, the device itself needs to be
told which MQTT broker to publish to. This is done once, per device,
through the vendor's own app — it isn't something this integration can
do for you:

1. Provision and pair the socket normally in the official **4box app**
   (Wi-Fi setup, etc.).
2. Open the device's **advanced settings** in the app and enable **MQTT**.
3. Fill in:
   - **Broker IP** (or hostname) of your MQTT broker.
   - **Username** and **password** for that broker (if your broker
     requires auth; see the [Development setup](#development-setup)
     section below for a no-auth broker you can use while testing).
   - A **Client ID** for this device — must be unique per device on the
     broker.
4. **Save**. The device will reconnect and start publishing to
   `<model>-<MAC>/...` topics using the device ID you'll enter in Home
   Assistant's config flow.

Only after this step will the device actually publish anything for the
integration to subscribe to.

## Requirements

- Home Assistant 2024.1 or newer.
- The core **MQTT** integration already configured and connected to the
  same broker your devices publish to (see above).
- A device already provisioned as described above.

## Installation

### Via HACS (recommended once published)

We recommend installing Custom brand icons card via [Home Assistant Community Store](https://hacs.xyz)
[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=elax46&repository=4box-s-series&category=integration)


1. HACS → Integrations → the "⋮" menu → *Custom repositories* → add this
   repository URL with category *Integration*.
2. Search for "4box S Series (MQTT)" and install.
3. Restart Home Assistant.

### Manual

1. Copy `custom_components/fourbox_s_series` into your Home Assistant
   `config/custom_components/` folder.
2. Restart Home Assistant.

## Configuration

1. Make sure the MQTT integration is set up first, and the physical
   device has MQTT enabled as described above.
2. Settings → Devices & Services → **Add Integration** → search for
   "4box S Series".
3. **Step 1**: pick the **device family** (relay / motorized shutter /
   Uniko Push / thermostat), enter the **device ID** exactly as printed
   on the device (format `<model>-<MAC>`, e.g. `M048B-30AEA4A6D460`), and
   an optional friendly name.
4. **Step 2**: family-specific options:
   - *Relay*: number of channels (1 or 2), whether it has an energy
     meter, energy poll interval.
   - *Motorized shutter*: whether it supports tilt.
   - *Uniko Push*: default pulse duration in ms.
   - *Thermostat*: poll interval.
5. Repeat for each physical device.

You can revisit poll intervals and pulse duration later via the
integration's **Configure** button without re-adding the device.

## Features by family

### Relay / socket
- **Switch** per relay channel (on/off/toggle), state kept correct by a
  combination of live MQTT push and an active re-check (`gpiostatus=GET`)
  every time the device announces itself online — including right at
  setup and again on any future reconnect, not just once.
- **Power / current sensors**: combine the firmware's spontaneous MQTT
  push with periodic active polling (`power=RELAY<n>`, `current=RELAY<n>`,
  piggybacked on the same poll cycle as the energy counter below) — the
  poll acts as a floor so these are never stuck at "unknown" for longer
  than one poll interval, even if the device stays quiet on the push
  side, while still taking whichever update (push or poll) is freshest.
- **Voltage / temperature sensors**: push-only (`/stat/voltage`,
  `/stat/temperature`), since the vendor guide documents no read command
  for either — the firmware pushes these roughly every 15 minutes on its
  own, so expect them to take a while to populate on a fresh device; this
  is a firmware limitation this integration has no way to bypass.
- **Energy sensor** compatible with the HA **Energy dashboard**
  (`device_class: energy`, `state_class: total_increasing`), obtained by
  periodically polling `energyActive=RELAY<n>` (the firmware doesn't push
  this value on its own).
- **Diagnostic binary sensors**: overcurrent, overtemperature.
- Single-channel and two-channel (M053B dual-light) devices both supported.
- **Optional, experimental**: indicator LED RGB value sensors (read-only
  diagnostic sensors), if you enable "Device has an indicator LED" at
  setup. No write command exists for it — six plausible syntaxes were
  tried against real hardware (`led=`, `led1=`, `ledColor=`,
  `led1_r=&led1_g=&led1_b=`, `LED1_R=&LED1_G=&LED1_B=`, and the
  `KEY:value;` form matching `gpiostatus=GET`'s own response format),
  all returning `(null)` with no change to the physical LED. Given the
  LED's observed behavior (color shifts on its own around relay state
  changes, e.g. red/orange right after a relay turns off), it looks more
  like an internal status indicator than something meant to be
  user-controllable, so this is treated as read-only rather than an open
  question — see the note in `sensor.py`'s `SSeriesLedChannelSensor` if
  you want to try further.

### Motorized shutter (cover)
- Full open/close/stop plus **absolute position** (`motor=MOVE&perc=`),
  using the device's native 0-100 scale, which matches Home Assistant's
  own convention directly.
- Optional **tilt** support (`motor=TILT&perc=`) for frangisole/venetian
  blinds, toggled per-device at setup time.
- Position and moving state come from the device's combined `/stat` push
  message; `motor=STATUS` is requested once at setup/reload so the
  current position is correct immediately (see below).
- A **Calibrate** button (`motor=CALIBRATION`), since the guide states
  percentage-based positioning requires calibration to have run at least
  once.
- A **power sensor**, parsed from the same combined `/stat` message.

### Uniko Push
- A **Pulse** button that triggers a timed relay pulse
  (`pulsetime=PULSETIME<ron>:<ms>&&RELAY1:ON`) — for gate openers,
  electric locks, doorbells, momentary resets, etc.
- Default pulse duration is set at config time (and adjustable later via
  the integration's Options).
- A read-only **Active** binary sensor reflecting the relay while a pulse
  is in progress.

### Thermostat
- **Climate** entity: HVAC modes Off / Heat / Cool (`thermostatMode`
  0/1/2), target temperature (`tS`, short form), current temperature and
  humidity (`sht4x` compensated readings).
- Unlike the other families, none of this is pushed spontaneously by the
  firmware — it's all request/response over the shared `/cmnd`-`/info`
  pair, so this entity is backed by periodic polling (interval
  configurable at setup, default 120s), which also means it's always
  correct immediately after setup/reload without any special handling.
- **Optional setpoint presets**: the guide documents recalling a
  pre-configured setpoint profile by numeric id (`tS=901`) or by
  mode+name (`tS=1;Eco`) — fully valid, documented commands. What isn't
  available over MQTT is a way to discover which profiles exist on a
  given device (that's set up through the vendor's own app). So instead
  of guessing, you can declare your own known profiles as a setup/options
  field, format `Name:value,Name:value`, e.g.:
  ```
  Eco:901,Comfort:902,Manual 18:-1;Manual;18
  ```
  Each declared name becomes a selectable Home Assistant climate preset;
  selecting one sends `tS=<value>` verbatim. Leave the field empty (the
  default) to skip presets entirely.
  - **Caveat**: the device doesn't report *which* profile (if any) is
    currently active — only the resulting numeric setpoint. The preset
    shown in Home Assistant reflects the last preset *this integration*
    selected, optimistically, and doesn't repopulate correctly after a
    setpoint change made some other way (the vendor app, or a manual
    temperature change here). Treat it as a shortcut for *setting* a
    known profile, not as an authoritative readout of which one is active.

## Entities created

<details>
<summary>Relay / socket (single-channel example)</summary>

| Entity | Platform |
|---|---|
| `switch.<device>_socket` | switch |
| `sensor.<device>_power` | sensor |
| `sensor.<device>_current` | sensor |
| `sensor.<device>_voltage` | sensor |
| `sensor.<device>_temperature` | sensor |
| `sensor.<device>_energy` | sensor |
| `binary_sensor.<device>_overcurrent` | binary_sensor |
| `binary_sensor.<device>_overtemperature` | binary_sensor |
| `sensor.<device>_led_red` / `_green` / `_blue` | sensor *(only if "has an indicator LED" is enabled)* |
</details>

<details>
<summary>Motorized shutter</summary>

| Entity | Platform |
|---|---|
| `cover.<device>` | cover |
| `button.<device>_calibrate` | button |
| `sensor.<device>_power` | sensor |
</details>

<details>
<summary>Uniko Push</summary>

| Entity | Platform |
|---|---|
| `button.<device>_pulse` | button |
| `binary_sensor.<device>_active` | binary_sensor |
</details>

<details>
<summary>Thermostat</summary>

| Entity | Platform |
|---|---|
| `climate.<device>` | climate — presets appear automatically if you declared any setpoint profiles at setup |
</details>

## Architecture

```
custom_components/fourbox_s_series/
├── __init__.py        # entry setup/teardown; routes device_type -> platforms;
│                       # fetches initial relay state via gpiostatus=GET
├── config_flow.py      # two-step UI flow: family picker, then family options
├── const.py             # domain, topic templates, per-family command builders
├── coordinator.py     # shared RequestResponsePoller + energy/thermostat coordinators
├── switch.py             # relay family: switch entities (MQTT push + initial fetch)
├── sensor.py             # relay family sensors + motor power sensor
├── binary_sensor.py    # relay diagnostics + push "active" sensor
├── cover.py               # motorized shutter (MQTT push + motor=STATUS on setup)
├── button.py             # motor calibrate + push pulse trigger
├── climate.py             # thermostat (polled)
├── utils.py               # model-from-ID, motor /stat parser, gpiostatus
│                           # parser, thermostat profile string parser
├── manifest.json
├── strings.json / translations/en.json
```

Design notes:

- **`device_type` drives everything.** `__init__.py` keeps a
  `PLATFORMS_BY_DEVICE_TYPE` map and only forwards the config entry to the
  platforms that family actually needs.
- **Push vs. poll, per value.** Relay state, power, current, voltage,
  temperature, fault flags, and motor position/status are all pushed
  spontaneously by the firmware on `<ID>/stat/...` — those entities
  subscribe once and need no coordinator. Energy counters and the entire
  thermostat state are request/response only (`<ID>/cmnd` →
  `<ID>/info`), so they go through `coordinator.py`'s
  `RequestResponsePoller`, shared by `SSeriesEnergyCoordinator`,
  `SSeriesThermostatCoordinator`, and the one-shot initial relay-state
  fetch in `__init__.py`.
- **Known limitation of `/info` polling**: `<ID>/info` is a single shared
  topic used for *every* command's response. The pollers serialize their
  own requests and only trust the next message that arrives after they
  published one, with a timeout — but if you (or another tool) manually
  publish a command to the same device while a poll is in flight, that
  cycle may pick up the wrong response. It self-corrects on the next poll.
- **Motor status parsing is best-effort.** The vendor guide documents the
  combined `/stat` format and shows "STOPPED" as the only concrete
  `motor_status` example. `cover.py`'s `_infer_moving_direction` makes a
  reasonable guess at `is_opening`/`is_closing` from keywords, but
  position and open/closed state (which come from the numeric position
  field, not the status string) are always accurate regardless.
- **Model comes from the device ID**, not a hardcoded constant, via
  `utils.model_from_device_id`.

## Development setup

### 1. Start Home Assistant + Mosquitto

```bash
cd dev
docker compose up
```

This starts:
- `mosquitto` on `localhost:1883`, published on **all** host network
  interfaces (open, no auth — dev only).
- Home Assistant on `http://localhost:8123`, with `../custom_components`
  bind-mounted read-write into `config/custom_components`.

Finish onboarding, then:
1. Settings → Devices & Services → Add Integration → **MQTT** → broker
   `mosquitto`, port `1883`, no credentials.
2. Settings → Devices & Services → Add Integration → **4box S Series
   (MQTT)** → pick a family and a device ID matching whatever you run the
   simulator with below (or your real device — see next section).

### 2a. Testing with the simulators

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r scripts/requirements-dev.txt

# Relay / socket
python scripts/simulate_device.py --device-type relay \
    --device-id M048B-30AEA4A6D460

# Motorized shutter
python scripts/simulate_device.py --device-type motor \
    --device-id M053B-30AEA4A6D461

# Uniko Push
python scripts/simulate_device.py --device-type push \
    --device-id M048B-30AEA4A6D462

# Thermostat
python scripts/simulate_device.py --device-type thermostat \
    --device-id M048B-30AEA4A6D463
```

Run as many instances as you need (one process per simulated device, in
separate terminals) to test multiple config entries at once. Each
simulator announces itself online via LWT and reacts to the commands
documented for its family — see `scripts/simulate_device.py` docstrings
for exactly what each one implements. Relay/motor state pushes are
deliberately **not retained**, matching the real firmware, so the
simulator reproduces the "state doesn't show up until setup actively
fetches it" scenario the integration now handles.

### 2b. Testing with a real device

The `mosquitto` broker started by `docker compose` is reachable from
other devices on your LAN, not just from your host machine — so you can
point a real socket at it instead of (or alongside) the simulator:

1. Find your host machine's LAN IP (e.g. `ipconfig getifaddr en0` on
   macOS, `hostname -I` on Linux) — not `127.0.0.1`/`localhost`, since
   the physical device can't reach that.
2. In the 4box app's advanced settings for the device, enable MQTT and
   set the broker to that LAN IP, port `1883`. The dev broker allows
   anonymous connections, so username/password can be any non-empty
   value if the app requires them to be filled in; pick any Client ID.
3. Add the device to the integration in the dev Home Assistant instance
   as usual, using its real device ID.

This is how the initial-state fetch behavior described above was
validated against a real P40S.

### 3. Iterate

After editing any file under `custom_components/fourbox_s_series/`, restart
Home Assistant (`docker compose restart homeassistant`, or Developer
Tools → YAML → *Restart* from the HA UI).

### 4. Run the automated test suite

```bash
python3 -m venv .venv-test && source .venv-test/bin/activate
pip install -r requirements-test.txt
pytest
```

This runs entirely against a **real (test) Home Assistant core instance**
via `pytest-homeassistant-custom-component` — not just static imports —
so it exercises the actual config entry / MQTT / entity plumbing, not
only pure-Python logic. Coverage as of this writing:

- `tests/test_utils.py` — parsing logic (`parse_motor_stat`,
  `parse_gpio_status`, `model_from_device_id`, `build_action_payload`,
  `parse_thermostat_profiles`). Includes the exact real payloads captured
  from an M048D (FW MO.14.00) as regression tests, so a change to the
  vendor firmware's response format — or an accidental regression in the
  parser — would be caught immediately.
- `tests/test_const_payloads.py` — every `/cmnd` payload builder
  (`motor_move_payload`, `pulse_payload`, `thermostat_mode_payload`,
  `setpoint_payload`, etc.), since a typo here silently breaks commands
  against real hardware.
- `tests/test_config_flow.py` — the full two-step config flow against a
  real Home Assistant instance and a mocked MQTT client: happy path,
  invalid device ID rejection, duplicate device ID abort, and MQTT
  discovery (a device announcing itself is picked up, an offline device
  is excluded, and a scan failure falls back to manual entry gracefully).

Not yet covered: entity-level tests for `switch`/`cover`/`climate`/etc.
behavior (state updates from MQTT push messages, service calls) and the
energy/thermostat polling coordinators. 

### Code style

Plain `async`/`await`, standard Home Assistant entity patterns
(`DataUpdateCoordinator` for polled data, direct MQTT subscription for
pushed data). No external runtime dependencies beyond Home Assistant core
and its bundled MQTT integration (`paho-mqtt` and the packages in
`requirements-test.txt` are *dev-only* dependencies of the simulator
script and test suite, not of the integration itself).

Contributions for any of the above are very welcome — open an issue or PR.

## Disclaimer

This is an independent, community-built integration. It is not affiliated
with, endorsed by, or supported by Finder S.p.A. or 4box. All product
names and trademarks belong to their respective owners.

