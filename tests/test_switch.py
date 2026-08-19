"""Integration tests for switch.py's direct MQTT push handling and
on/off/toggle service calls, run against a real (test) Home Assistant
core instance via `pytest-homeassistant-custom-component`.

Requires `requirements-test.txt` to be installed. Run with:

    pytest tests/test_switch.py
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
    DEVICE_TYPE_RELAY,
    DOMAIN,
)

_DEVICE_ID = "M048D-901506BADF40"
_ENTITY_ID = "switch.m048d_901506badf40_socket"


@pytest.fixture
def expected_lingering_timers():
    return True


def _relay_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=_DEVICE_ID,
        data={
            CONF_DEVICE_ID: _DEVICE_ID,
            CONF_DEVICE_TYPE: DEVICE_TYPE_RELAY,
            CONF_CHANNELS: 1,
            CONF_HAS_ENERGY: False,
        },
        source=config_entries.SOURCE_USER,
        unique_id=_DEVICE_ID,
    )


async def test_switch_state_updates_from_direct_stat_push(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """A spontaneous /stat/relay/1 push (e.g. after a physical toggle or
    a command from the vendor app) must update the switch directly,
    independent of the gpiostatus=GET refresh mechanism."""
    entry = _relay_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    async_fire_mqtt_message(hass, f"{_DEVICE_ID}/connect", "true")
    await hass.async_block_till_done()

    async_fire_mqtt_message(hass, f"{_DEVICE_ID}/stat/relay/1", "on")
    await hass.async_block_till_done()
    assert hass.states.get(_ENTITY_ID).state == "on"

    async_fire_mqtt_message(hass, f"{_DEVICE_ID}/stat/relay/1", "off")
    await hass.async_block_till_done()
    assert hass.states.get(_ENTITY_ID).state == "off"


async def test_turn_on_off_toggle_publish_correct_commands(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """The switch.turn_on/turn_off/toggle services must publish the
    exact `action=` commands documented in the vendor guide."""
    entry = _relay_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    async_fire_mqtt_message(hass, f"{_DEVICE_ID}/connect", "true")
    await hass.async_block_till_done()

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": _ENTITY_ID}, blocking=True
    )
    mqtt_mock.async_publish.assert_any_call(f"{_DEVICE_ID}/cmnd", "action=ON", 0, False)

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": _ENTITY_ID}, blocking=True
    )
    mqtt_mock.async_publish.assert_any_call(
        f"{_DEVICE_ID}/cmnd", "action=OFF", 0, False
    )

    await hass.services.async_call(
        "switch", "toggle", {"entity_id": _ENTITY_ID}, blocking=True
    )
    mqtt_mock.async_publish.assert_any_call(
        f"{_DEVICE_ID}/cmnd", "action=TOGGLE", 0, False
    )
