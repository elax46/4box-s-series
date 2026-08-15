"""Integration tests for the power/current active-polling floor combined
with MQTT push in SSeriesPowerSensor/SSeriesCurrentSensor, run against a
real (test) Home Assistant core instance via
`pytest-homeassistant-custom-component`.

Requires `requirements-test.txt` to be installed. Run with:

    pytest tests/test_sensor_polling.py
"""

from __future__ import annotations

from unittest.mock import patch

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
    CONF_ENERGY_POLL_INTERVAL,
    CONF_HAS_ENERGY,
    DEVICE_TYPE_RELAY,
    DOMAIN,
)

_DEVICE_ID = "M048D-901506BADF40"
_CMND_TOPIC = f"{_DEVICE_ID}/cmnd"
_INFO_TOPIC = f"{_DEVICE_ID}/info"


@pytest.fixture
def expected_lingering_timers():
    return True


def _relay_entry_with_energy() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=_DEVICE_ID,
        data={
            CONF_DEVICE_ID: _DEVICE_ID,
            CONF_DEVICE_TYPE: DEVICE_TYPE_RELAY,
            CONF_CHANNELS: 1,
            CONF_HAS_ENERGY: True,
            CONF_ENERGY_POLL_INTERVAL: 300,
        },
        source=config_entries.SOURCE_USER,
        unique_id=_DEVICE_ID,
    )


def _respond_to(responses: dict[str, str]):
    """Build an `mqtt.async_publish` side_effect answering known
    `/cmnd` payloads with a matching `/info` reply, simulating the
    device -- used because no push message would otherwise ever populate
    power/current here, exercising only the active-poll path."""

    async def _side_effect(hass_arg, topic, payload, *args, **kwargs):
        if topic == _CMND_TOPIC and payload in responses:
            async_fire_mqtt_message(hass_arg, _INFO_TOPIC, responses[payload])

    return _side_effect


async def test_power_and_current_populate_via_active_poll_without_any_push(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """Core regression test: power/current/energy sensors must populate
    from SSeriesEnergyCoordinator's active polling alone, with NO `/stat`
    push message ever published -- this is what "sensors stuck on
    unknown for a long time" actually meant: the firmware's spontaneous
    push wasn't reliable/frequent enough on its own, and there was no
    active query as a floor.
    """
    entry = _relay_entry_with_energy()
    entry.add_to_hass(hass)

    responses = {
        "power=RELAY1": "123.4 (Watt)",
        "current=RELAY1": "0.54 (Ampere)",
        "energyActive=RELAY1": "12.345 (kWh)",
    }

    with patch(
        "custom_components.fourbox_s_series.coordinator.mqtt.async_publish",
        side_effect=_respond_to(responses),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    power_state = hass.states.get("sensor.m048d_901506badf40_power")
    assert power_state is not None
    assert power_state.state == "123.4"

    current_state = hass.states.get("sensor.m048d_901506badf40_current")
    assert current_state is not None
    assert current_state.state == "0.54"

    energy_state = hass.states.get("sensor.m048d_901506badf40_energy")
    assert energy_state is not None
    assert energy_state.state == "12.345"


async def test_partial_metric_failure_does_not_blank_other_metrics(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """If the `power=RELAY1` query times out but `current=RELAY1` and
    `energyActive=RELAY1` succeed in the same poll cycle, the current and
    energy sensors must still populate -- one bad metric must not poison
    the whole channel's poll cycle.
    """
    entry = _relay_entry_with_energy()
    entry.add_to_hass(hass)

    # Deliberately no entry for "power=RELAY1": it will time out.
    responses = {
        "current=RELAY1": "0.54 (Ampere)",
        "energyActive=RELAY1": "12.345 (kWh)",
    }

    with (
        patch(
            "custom_components.fourbox_s_series.coordinator.DEFAULT_MQTT_RESPONSE_TIMEOUT",
            0.05,
        ),
        patch(
            "custom_components.fourbox_s_series.coordinator.mqtt.async_publish",
            side_effect=_respond_to(responses),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    current_state = hass.states.get("sensor.m048d_901506badf40_current")
    assert current_state is not None
    assert current_state.state == "0.54"

    energy_state = hass.states.get("sensor.m048d_901506badf40_energy")
    assert energy_state is not None
    assert energy_state.state == "12.345"

    # Power got no response at all -- stays at its initial unset state,
    # but critically the ENTRY still loaded successfully and the other
    # two metrics are correct, rather than the whole poll cycle failing.
    power_state = hass.states.get("sensor.m048d_901506badf40_power")
    assert power_state is not None
    assert power_state.state == "unknown"
