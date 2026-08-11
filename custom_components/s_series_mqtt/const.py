"""Constants for the 4box S Series (MQTT) integration.

Covers Finder/4box devices built on the Morpheos ESP32IoT firmware that
share the same <ID>/cmnd, <ID>/info, <ID>/stat, <ID>/connect topic scheme.
Today this means the S Series relay/socket devices (P40S, M048B, M048C,
M048D, M054F, and the two-channel M053B). The domain name is kept
device-family-agnostic ("s_series") so future platforms (covers, pulsed
outputs, thermostats -- see the README roadmap) can be added under the
same integration and config entries without a breaking rename."""

DOMAIN = "s_series_mqtt"

CONF_DEVICE_ID = "device_id"
CONF_NAME = "name"
CONF_CHANNELS = "channels"
CONF_HAS_ENERGY = "has_energy"
CONF_ENERGY_POLL_INTERVAL = "energy_poll_interval"

DEFAULT_CHANNELS = 1
DEFAULT_ENERGY_POLL_INTERVAL = 300  # seconds
DEFAULT_ENERGY_RESPONSE_TIMEOUT = 10  # seconds

MANUFACTURER = "Finder / 4box"

# Topic templates. `{id}` is the device ID as printed on the device
# (format <model>-<MAC>, e.g. M048B-30AEA4A6D460), `{channel}` is 1 or 2.
TOPIC_CMND = "{id}/cmnd"
TOPIC_INFO = "{id}/info"
TOPIC_CONNECT = "{id}/connect"

TOPIC_RELAY_STATE = "{id}/stat/relay/{channel}"
TOPIC_RELAY_POWER = "{id}/stat/relay/{channel}/power/w"
TOPIC_RELAY_CURRENT = "{id}/stat/relay/{channel}/current/a"
TOPIC_RELAY_OVERCURRENT = "{id}/stat/relay/{channel}/overcurrent"
TOPIC_VOLTAGE = "{id}/stat/voltage"
TOPIC_TEMPERATURE = "{id}/stat/temperature"
TOPIC_OVERTEMPERATURE = "{id}/stat/overtemperature"
