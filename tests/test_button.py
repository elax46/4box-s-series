"""Integration tests for the button platform (button.py), run against a
real (test) Home Assistant core instance via
`pytest-homeassistant-custom-component`.

Requires `requirements-test.txt` to be installed. Run with:

    pytest tests/test_button.py
"""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from custom_components.fourbox_s_series.const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_PULSE_DURATION_MS,
    DEVICE_TYPE_MOTOR,
    DEVICE_TYPE_PUSH,
    DOMAIN,
)

_MOTOR_DEVICE_ID = "M053B-30AEA4A6D461"
_PUSH_DEVICE_ID = "M048B-30AEA4A6D462"


@pytest.fixture
def expected_lingering_timers():
    return True


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


def _push_entry(*, pulse_duration_ms: int = 1000) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=_PUSH_DEVICE_ID,
        data={
            CONF_DEVICE_ID: _PUSH_DEVICE_ID,
            CONF_DEVICE_TYPE: DEVICE_TYPE_PUSH,
            CONF_PULSE_DURATION_MS: pulse_duration_ms,
        },
        source=config_entries.SOURCE_USER,
        unique_id=_PUSH_DEVICE_ID,
    )


async def test_motor_calibrate_button_created(hass: HomeAssistant, mqtt_mock) -> None:
    entry = _motor_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("button.m053b_30aea4a6d461_calibrate")
    assert state is not None


async def test_motor_calibrate_button_publishes_calibration_command(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """Pressing Calibrate must publish `motor=CALIBRATION`, per the
    vendor guide (required once before percentage positioning works)."""
    entry = _motor_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.m053b_30aea4a6d461_calibrate"},
        blocking=True,
    )

    mqtt_mock.async_publish.assert_any_call(
        f"{_MOTOR_DEVICE_ID}/cmnd", "motor=CALIBRATION", 0, False
    )


async def test_push_pulse_button_created(hass: HomeAssistant, mqtt_mock) -> None:
    entry = _push_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("button.m048b_30aea4a6d462_pulse")
    assert state is not None


async def test_push_pulse_button_publishes_default_duration(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """Pressing Pulse must publish the exact PULSETIME command format
    documented in the guide, using the configured default duration."""
    entry = _push_entry(pulse_duration_ms=1000)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.m048b_30aea4a6d462_pulse"},
        blocking=True,
    )

    mqtt_mock.async_publish.assert_any_call(
        f"{_PUSH_DEVICE_ID}/cmnd",
        "pulsetime=PULSETIME<ron>:1000&&RELAY1:ON",
        0,
        False,
    )


async def test_push_pulse_button_uses_configured_custom_duration(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """A non-default pulse duration set at config time must be reflected
    in the published command."""
    entry = _push_entry(pulse_duration_ms=500)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.m048b_30aea4a6d462_pulse"},
        blocking=True,
    )

    mqtt_mock.async_publish.assert_any_call(
        f"{_PUSH_DEVICE_ID}/cmnd",
        "pulsetime=PULSETIME<ron>:500&&RELAY1:ON",
        0,
        False,
    )
