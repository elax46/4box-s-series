"""Constants for the 4box S Series (MQTT) integration.

Covers Finder/4box devices built on the Morpheos ESP32IoT firmware that
share the same <ID>/cmnd, <ID>/info, <ID>/stat, <ID>/connect topic scheme.
Four device families are supported, matching the sections of the vendor's
MQTT guide:

- "relay":       P40S, M048B, M048C, M048D, M054F, and M053B configured as
                 dual-light -- plain on/off/toggle outputs, some with
                 power/current/energy metering.
- "motor":       M053B configured as a motorized shutter/awning actuator.
- "push":        Uniko Push pulsed outputs (PULSETIME).
- "thermostat":  Morpheos thermostats (mode + setpoint + SHT4x sensor).

The domain name stays device-family-agnostic ("s_series") so all four
(and any future ones) live under the same integration and config entries.
"""

DOMAIN = "s_series_mqtt"

# --- config entry keys ----------------------------------------------------
CONF_DEVICE_ID = "device_id"
CONF_NAME = "name"
CONF_DEVICE_TYPE = "device_type"

# relay-specific
CONF_CHANNELS = "channels"
CONF_HAS_ENERGY = "has_energy"
CONF_ENERGY_POLL_INTERVAL = "energy_poll_interval"

# motor-specific
CONF_HAS_TILT = "has_tilt"

# push-specific
CONF_PULSE_DURATION_MS = "pulse_duration_ms"

# thermostat-specific
CONF_THERMOSTAT_POLL_INTERVAL = "thermostat_poll_interval"

DEVICE_TYPE_RELAY = "relay"
DEVICE_TYPE_MOTOR = "motor"
DEVICE_TYPE_PUSH = "push"
DEVICE_TYPE_THERMOSTAT = "thermostat"
DEVICE_TYPES = [
    DEVICE_TYPE_RELAY,
    DEVICE_TYPE_MOTOR,
    DEVICE_TYPE_PUSH,
    DEVICE_TYPE_THERMOSTAT,
]

DEFAULT_CHANNELS = 1
DEFAULT_ENERGY_POLL_INTERVAL = 300  # seconds
DEFAULT_THERMOSTAT_POLL_INTERVAL = 120  # seconds
DEFAULT_PULSE_DURATION_MS = 1000
DEFAULT_MQTT_RESPONSE_TIMEOUT = 10  # seconds, shared by every /cmnd-/info poller

MANUFACTURER = "Finder / 4box"

# --- topic templates -------------------------------------------------------
# `{id}` is the device ID as printed on the device (format <model>-<MAC>,
# e.g. M048B-30AEA4A6D460), `{channel}` is 1 or 2.
TOPIC_CMND = "{id}/cmnd"
TOPIC_INFO = "{id}/info"
TOPIC_STAT = "{id}/stat"
TOPIC_CONNECT = "{id}/connect"

# relay family
TOPIC_RELAY_STATE = "{id}/stat/relay/{channel}"
TOPIC_RELAY_POWER = "{id}/stat/relay/{channel}/power/w"
TOPIC_RELAY_CURRENT = "{id}/stat/relay/{channel}/current/a"
TOPIC_RELAY_OVERCURRENT = "{id}/stat/relay/{channel}/overcurrent"
TOPIC_VOLTAGE = "{id}/stat/voltage"
TOPIC_TEMPERATURE = "{id}/stat/temperature"
TOPIC_OVERTEMPERATURE = "{id}/stat/overtemperature"

# motor family: single combined payload on <ID>/stat, see coordinator/cover
# docstrings for the "<gpio>=><position>=><relay1_w>=><relay2_w>=><status>"
# format.
TOPIC_MOTOR_STAT = "{id}/stat"

MOTOR_CMD_UP = "motor=UP"
MOTOR_CMD_DOWN = "motor=DOWN"
MOTOR_CMD_STOP = "motor=STOP"
MOTOR_CMD_CALIBRATION = "motor=CALIBRATION"


def motor_move_payload(perc: int) -> str:
    """Command to move the motor to an absolute position (0-100)."""
    return f"motor=MOVE&perc={perc}"


def motor_tilt_payload(perc: int) -> str:
    """Command to set slat/blade tilt (0-100)."""
    return f"motor=TILT&perc={perc}"


# push family
def pulse_payload(duration_ms: int, relay: str = "RELAY1") -> str:
    """Command for a timed relay pulse, e.g. PULSETIME<ron>:1000&&RELAY1:ON."""
    return f"pulsetime=PULSETIME<ron>:{duration_ms}&&{relay}:ON"


TOPIC_PUSH_RELAY_STATE = "{id}/stat/relay/{channel}"

# thermostat family (polled request/response, like energyActive)
CMD_THERMOSTAT_MODE_GET = "thermostatMode=GET"
CMD_TEMPERATURE_GET = "sht4x=t_compensated"
CMD_HUMIDITY_GET = "sht4x=rh_compensated"
CMD_SETPOINT_GET = "tS=GET"


def thermostat_mode_payload(mode: int) -> str:
    """Command to set thermostat mode: 0=OFF, 1=HEATING, 2=COOLING."""
    return f"thermostatMode={mode}"


def setpoint_payload(temperature: float) -> str:
    """Command to set the setpoint using the short form, e.g. tS=20.5."""
    return f"tS={temperature}"


def thermostat_profile_payload(profile_value: str) -> str:
    """Command to recall a configured setpoint profile, e.g. tS=901 or
    tS=1;Eco. `profile_value` is whatever the user declared it to be in
    the integration's options (see CONF_THERMOSTAT_PROFILES) -- this
    integration has no way to look up valid profile ids/names itself.
    """
    return f"tS={profile_value}"


# --- initial-state fetch on setup/reload ------------------------------
# Confirmed on a real M048D (firmware MO.14.00): `/stat/...` topics are
# NOT published with the MQTT retain flag, so a fresh subscriber (e.g.
# this integration right after HA starts or the entry reloads) gets
# nothing until the device's state actually changes next.
# `gpiostatus=GET` and `motor=STATUS` are request/response commands we
# use once at startup to force an immediate read instead of waiting on a
# push that may not come for a while. See utils.parse_gpio_status for the
# confirmed response format of gpiostatus=GET (semicolon-separated
# KEY:value pairs, e.g. "...;RELAY1:ON;..."), reverse-engineered from
# real hardware since the vendor guide only documents the command name.
CMD_GPIOSTATUS_GET = "gpiostatus=GET"
MOTOR_CMD_STATUS = "motor=STATUS"

# --- MQTT discovery (config flow) --------------------------------------
# There's no vendor-documented HA-style MQTT discovery topic. Instead,
# every device announces itself online via its own LWT birth message on
# `<ID>/connect` (retained, payload "true"). The config flow does a
# short-lived wildcard subscription to `+/connect` to passively collect
# device IDs currently online, purely as a convenience for the picker --
# manual entry always remains available as a fallback.
TOPIC_CONNECT_WILDCARD = "+/connect"
DISCOVERY_SCAN_SECONDS = 2

# --- LED indicator (undocumented, reverse-engineered read-only) --------
# Not mentioned anywhere in the vendor guide. Discovered from a real
# M048D: `gpiostatus=GET` includes LED1_R/G/B fields, and the device also
# spontaneously pushes /stat/led/1/{r,g,b}. No write command has been
# found or documented, so this can only be exposed read-only (diagnostic
# sensors), not a controllable `light` entity. Opt-in via CONF_HAS_LED
# since it's unconfirmed whether every relay-family model has this LED.
CONF_HAS_LED = "has_led"
DEFAULT_HAS_LED = False
TOPIC_LED_R = "{id}/stat/led/1/r"
TOPIC_LED_G = "{id}/stat/led/1/g"
TOPIC_LED_B = "{id}/stat/led/1/b"

# --- thermostat presets (profile recall) --------------------------------
# Fully documented in the vendor guide (section 6.2): a setpoint can be
# recalled by numeric profile id (`tS=901`) or by mode+name (`tS=1;Eco`).
# What ISN'T available over MQTT is a way to enumerate which profiles are
# actually configured on a given device -- that's set up through the
# vendor's own app/device config, invisible to this integration. So
# instead of guessing, the user declares their own known profiles as an
# option at setup time (see CONF_THERMOSTAT_PROFILES), which become
# selectable Home Assistant climate presets.
CONF_THERMOSTAT_PROFILES = "thermostat_profiles"
