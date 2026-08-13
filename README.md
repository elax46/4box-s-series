# HA Custom Integration: 4box S Series

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

Tested against a real P40S — which reports model **M048D** in its MQTT
device ID — on firmware **MO.14.00**.

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

1. HACS → Integrations → the "⋮" menu → *Custom repositories* → add this
   repository URL with category *Integration*.
2. Search for "4box S Series (MQTT)" and install.
3. Restart Home Assistant.

### Manual

1. Copy `custom_components/s_series_mqtt` into your Home Assistant
   `config/custom_components/` folder.
2. Restart Home Assistant.

## Configuration

1. Make sure the MQTT integration is set up first, and the physical
   device has MQTT enabled as described above.
2. Settings → Devices & Services → **Add Integration** → search for
   "4box S Series (MQTT)".
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
- **Switch** per relay channel (on/off/toggle), state pushed live, with
  its current state actively fetched at setup/reload (see
  [Initial state on setup](#initial-state-on-setupreload) below) so it's
  correct immediately rather than only after the next physical toggle.
- **Sensors**: power (W), current (A), voltage (V), temperature (°C) —
  pushed spontaneously by the firmware.
- **Energy sensor** compatible with the HA **Energy dashboard**
  (`device_class: energy`, `state_class: total_increasing`), obtained by
  periodically polling `energyActive=RELAY<n>` (the firmware doesn't push
  this value on its own).
- **Diagnostic binary sensors**: overcurrent, overtemperature.
- Single-channel and two-channel (M053B dual-light) devices both supported.

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
- The guide's profile-recall setpoint forms (`tS=901`, `tS=1;Eco`) aren't
  exposed, since they depend on per-device profile configuration this
  integration has no way to introspect from MQTT alone — only the manual
  short-form setpoint (`tS=20.5`) is used.

## Initial state on setup/reload

Real-hardware testing (P40S / M048D, firmware MO.14.00) confirmed that the
device's `/stat/relay/1` (and the combined motor `/stat`) pushes are
**not published with the MQTT retain flag**. That means a subscriber that
starts *after* the device already reached its current state — which is
exactly what happens every time the integration is set up, reloaded, or
Home Assistant restarts — receives nothing until the relay/motor's next
actual state change. Until then, a naive push-only implementation shows
the switch as off/unavailable even if the socket is physically on.

This integration works around it by actively requesting the current
state once, right at setup:

- **Relay devices**: `gpiostatus=GET` (documented in the guide's Appendix
  A cheat-sheet) is queried once in `__init__.py` before the switch
  platform is set up, and its response seeds the switch's initial state
  instead of defaulting to off/unavailable.
- **Motor devices**: `motor=STATUS` (documented explicitly in the guide's
  motor section: *"Stato runtime pubblicato su /stat"*) is published once
  the cover entity subscribes, which makes the firmware immediately
  re-publish its current position/status on the topic we're already
  listening to — no separate response-parsing path needed.
- **Thermostat**: unaffected, since it's polled from scratch on every
  refresh anyway (see above).
- **Uniko Push**: not addressed yet, since the relay state there is
  inherently transient (on only during a pulse) and less likely to be
  stale in a way that matters — tracked in the [Roadmap](#roadmap).

### `gpiostatus=GET` response format (reverse-engineered)

The vendor guide's Appendix A only lists the command name
(`gpiostatus=GET` → *"Lettura GPIO"*) without documenting its response
format. **Confirmed on a real M048D, firmware MO.14.00**, it's a
semicolon-separated list of `KEY:value` pairs:

```
LED1_R:0;LED1_G:0;LED1_B:0;RELAY1:ON;SW1_DC:PULL;SW1_AC:PULL;
```

`utils.parse_gpio_status` extracts `RELAY<n>:ON`/`RELAY<n>:OFF` tokens
from this and ignores the rest (LED indicator color, input pull state,
etc. — not relevant to relay state, and not necessarily present on every
model). This was verified for a single-channel device; the 2-channel
case (M053B dual-light, expected as `RELAY1:...;RELAY2:...;` in the same
list) is untested — if you have one and can confirm or correct it, please
open an issue with the raw payload.

Two other things worth knowing, discovered from the same real-device
capture, in case you're debugging with `mosquitto_sub` yourself:

- **`action=ON`/`action=OFF` replies on `/info` with `<ON>`/`<OFF>`**,
  not `"DONE"` as the PDF guide documents. The integration doesn't parse
  this particular response (it relies on the `/stat/relay/1` push or the
  `gpiostatus=GET` fetch instead), so this didn't require a code change,
  but it's a discrepancy worth knowing about if you're comparing raw MQTT
  traffic against the guide.
- The device also spontaneously publishes `/stat/led/1/{r,g,b}` and a
  second combined `/stat` message in the same `KEY:value` format as
  `gpiostatus=GET` (with a trailing `=>0`) — likely related to an
  indicator LED. Neither is currently used by this integration; possible
  future `light` platform, see [Roadmap](#roadmap).

If `gpiostatus=GET`'s response can't be parsed at all (no `RELAY<n>`
token found), the integration logs a debug message and falls back to the
old off/unavailable-until-first-push behavior for that device, rather
than failing setup.

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
| `climate.<device>` | climate |
</details>

## Architecture

```
custom_components/s_series_mqtt/
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
├── utils.py               # model-from-ID, motor /stat parser, gpiostatus parser
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

You don't need real hardware to develop against this integration — a
Home Assistant instance, a local MQTT broker, and the included device
simulator (covering all four families) is enough. If you *do* have
hardware, you can also point it at the same dev broker (see below).

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

### 2a. Testing with the simulator (no hardware needed)

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
fetches it" scenario the integration now handles — see
[Initial state on setup/reload](#initial-state-on-setupreload).

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

After editing any file under `custom_components/s_series_mqtt/`, restart
Home Assistant (`docker compose restart homeassistant`, or Developer
Tools → YAML → *Restart* from the HA UI).

### Code style

Plain `async`/`await`, standard Home Assistant entity patterns
(`DataUpdateCoordinator` for polled data, direct MQTT subscription for
pushed data). No external runtime dependencies beyond Home Assistant core
and its bundled MQTT integration (`paho-mqtt` is a *dev-only* dependency
of the simulator script, not of the integration itself).

Contributions for any of the above are very welcome — open an issue or PR.

## Disclaimer

This is an independent, community-built integration. It is not affiliated
with, endorsed by, or supported by Finder S.p.A. or 4box. All product
names and trademarks belong to their respective owners.
