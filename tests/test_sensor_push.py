"""Integration tests for push-driven sensor updates -- voltage,
temperature, the optional LED indicator sensors, and the motor power
sensor -- run against a real (test) Home Assistant core instance via
`pytest-homeassistant-custom-component`.

Requires `requirements-test.txt` to be installed. Run with:

    pytest tests/test_sensor_push.py
"""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from custom_components.fourbox_s_series.const import (
    CONF_CHANNELS,
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_HAS_ENERGY,
    CONF_HAS_LED,
    DEVICE_TYPE_MOTOR,
    DEVICE_TYPE_RELAY,
    DOMAIN,
)

_RELAY_DEVICE_ID = "M048D-901506BADF40"
_MOTOR_DEVICE_ID = "M053B-30AEA4A6D461"


@pytest.fixture
def expected_lingering_timers():
    return True


def _relay_entry(*, has_led: bool = False) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=_RELAY_DEVICE_ID,
        data={
            CONF_DEVICE_ID: _RELAY_DEVICE_ID,
            CONF_DEVICE_TYPE: DEVICE_TYPE_RELAY,
            CONF_CHANNELS: 1,
            CONF_HAS_ENERGY: False,
            CONF_HAS_LED: has_led,
        },
        source=config_entries.SOURCE_USER,
        unique_id=_RELAY_DEVICE_ID,
    )


def _motor_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=_MOTOR_DEVICE_ID,
        data={
            CONF_DEVICE_ID: _MOTOR_DEVICE_ID,
            CONF_DEVICE_TYPE: DEVICE_TYPE_MOTOR,
        },
        source=config_entries.SOURCE_USER,
        unique_id=_MOTOR_DEVICE_ID,
    )


async def test_voltage_and_temperature_update_from_push(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """Voltage/temperature have no documented read command -- push is
    the only way they're ever populated (roughly every 15 minutes per
    the vendor guide)."""
    entry = _relay_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    async_fire_mqtt_message(hass, f"{_RELAY_DEVICE_ID}/stat/voltage", "230.1")
    async_fire_mqtt_message(hass, f"{_RELAY_DEVICE_ID}/stat/temperature", "30.1")
    await hass.async_block_till_done()

    voltage_state = hass.states.get("sensor.m048d_901506badf40_voltage")
    assert voltage_state is not None
    assert voltage_state.state == "230.1"

    temperature_state = hass.states.get("sensor.m048d_901506badf40_temperature")
    assert temperature_state is not None
    assert temperature_state.state == "30.1"


async def test_non_numeric_push_payload_is_ignored_not_crashed(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """A garbled payload must be logged and ignored, leaving the sensor
    at its previous value rather than raising."""
    entry = _relay_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    async_fire_mqtt_message(hass, f"{_RELAY_DEVICE_ID}/stat/voltage", "230.1")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.m048d_901506badf40_voltage").state == "230.1"

    async_fire_mqtt_message(hass, f"{_RELAY_DEVICE_ID}/stat/voltage", "not-a-number")
    await hass.async_block_till_done()

    # Value unchanged, no crash / no "unknown" reset.
    assert hass.states.get("sensor.m048d_901506badf40_voltage").state == "230.1"


async def test_led_sensors_not_created_when_has_led_disabled(
    hass: HomeAssistant, mqtt_mock
) -> None:
    entry = _relay_entry(has_led=False)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.m048d_901506badf40_led_red") is None


async def test_led_sensors_created_and_update_from_push_when_enabled(
    hass: HomeAssistant, mqtt_mock
) -> None:
    entry = _relay_entry(has_led=True)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    async_fire_mqtt_message(hass, f"{_RELAY_DEVICE_ID}/stat/led/1/r", "244")
    async_fire_mqtt_message(hass, f"{_RELAY_DEVICE_ID}/stat/led/1/g", "110")
    async_fire_mqtt_message(hass, f"{_RELAY_DEVICE_ID}/stat/led/1/b", "99")
    await hass.async_block_till_done()

    assert hass.states.get("sensor.m048d_901506badf40_led_red").state == "244"
    assert hass.states.get("sensor.m048d_901506badf40_led_green").state == "110"
    assert hass.states.get("sensor.m048d_901506badf40_led_blue").state == "99"


async def test_motor_power_sensor_updates_from_combined_stat(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """The motor power sensor is parsed out of the SAME combined /stat
    message the cover entity uses, per the vendor guide's example
    format."""
    entry = _motor_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    async_fire_mqtt_message(
        hass, f"{_MOTOR_DEVICE_ID}/stat", "00000011=>50=>12.3=>0.0=>STOPPED"
    )
    await hass.async_block_till_done()

    state = hass.states.get("sensor.m053b_30aea4a6d461_power")
    assert state is not None
    assert state.state == "12.3"
