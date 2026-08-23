"""Integration tests for the thermostat climate platform (climate.py),
run against a real (test) Home Assistant core instance via
`pytest-homeassistant-custom-component`.

Requires `requirements-test.txt` to be installed. Run with:

    pytest tests/test_climate.py
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
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_THERMOSTAT_POLL_INTERVAL,
    CONF_THERMOSTAT_PROFILES,
    DEVICE_TYPE_THERMOSTAT,
    DOMAIN,
)

_DEVICE_ID = "M048B-30AEA4A6D463"
_CMND_TOPIC = f"{_DEVICE_ID}/cmnd"
_INFO_TOPIC = f"{_DEVICE_ID}/info"
_ENTITY_ID = "climate.m048b_30aea4a6d463"


@pytest.fixture
def expected_lingering_timers():
    return True


def _thermostat_entry(*, profiles: str = "") -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=_DEVICE_ID,
        data={
            CONF_DEVICE_ID: _DEVICE_ID,
            CONF_DEVICE_TYPE: DEVICE_TYPE_THERMOSTAT,
            CONF_THERMOSTAT_POLL_INTERVAL: 120,
            CONF_THERMOSTAT_PROFILES: profiles,
        },
        source=config_entries.SOURCE_USER,
        unique_id=_DEVICE_ID,
    )


def _respond_to(responses: dict[str, str]):
    """Build an `mqtt.async_publish` side_effect answering known
    `/cmnd` payloads with a matching `/info` reply, simulating the
    device."""

    async def _side_effect(hass_arg, topic, payload, *args, **kwargs):
        if topic == _CMND_TOPIC and payload in responses:
            async_fire_mqtt_message(hass_arg, _INFO_TOPIC, responses[payload])

    return _side_effect


_STANDARD_RESPONSES = {
    "thermostatMode=GET": "1",
    "tS=GET": "20.5 (gradi Celsius)",
    "sht4x=t_compensated": "19.8 (gradi Celsius)",
    "sht4x=rh_compensated": "45.0 (% RH)",
}


async def test_climate_entity_populates_from_initial_poll(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """All four polled values (mode, setpoint, temperature, humidity)
    must populate from the coordinator's first poll at setup."""
    entry = _thermostat_entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.fourbox_s_series.coordinator.mqtt.async_publish",
        side_effect=_respond_to(_STANDARD_RESPONSES),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get(_ENTITY_ID)
    assert state is not None
    assert state.state == "heat"  # mode=1
    assert state.attributes["temperature"] == 20.5
    assert state.attributes["current_temperature"] == 19.8
    assert state.attributes["current_humidity"] == 45.0


async def test_climate_hvac_mode_off_and_cool_map_correctly(
    hass: HomeAssistant, mqtt_mock
) -> None:
    entry = _thermostat_entry()
    entry.add_to_hass(hass)

    responses = dict(_STANDARD_RESPONSES)
    responses["thermostatMode=GET"] = "0"
    with patch(
        "custom_components.fourbox_s_series.coordinator.mqtt.async_publish",
        side_effect=_respond_to(responses),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert hass.states.get(_ENTITY_ID).state == "off"

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    entry2 = _thermostat_entry()
    entry2.add_to_hass(hass)
    responses["thermostatMode=GET"] = "2"
    with patch(
        "custom_components.fourbox_s_series.coordinator.mqtt.async_publish",
        side_effect=_respond_to(responses),
    ):
        assert await hass.config_entries.async_setup(entry2.entry_id)
        await hass.async_block_till_done()
    assert hass.states.get(_ENTITY_ID).state == "cool"


async def test_setting_hvac_mode_publishes_correct_command(
    hass: HomeAssistant, mqtt_mock
) -> None:
    entry = _thermostat_entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.fourbox_s_series.coordinator.mqtt.async_publish",
        side_effect=_respond_to(_STANDARD_RESPONSES),
    ) as mock_publish:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        await hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": _ENTITY_ID, "hvac_mode": "off"},
            blocking=True,
        )
        await hass.async_block_till_done()

    mock_publish.assert_any_call(hass, _CMND_TOPIC, "thermostatMode=0")


async def test_setting_temperature_publishes_correct_command(
    hass: HomeAssistant, mqtt_mock
) -> None:
    entry = _thermostat_entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.fourbox_s_series.coordinator.mqtt.async_publish",
        side_effect=_respond_to(_STANDARD_RESPONSES),
    ) as mock_publish:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": _ENTITY_ID, "temperature": 21.5},
            blocking=True,
        )
        await hass.async_block_till_done()

    mock_publish.assert_any_call(hass, _CMND_TOPIC, "tS=21.5")


async def test_no_profiles_means_no_preset_support(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """An empty profiles string (the default) must not expose the
    PRESET_MODE feature at all."""
    entry = _thermostat_entry(profiles="")
    entry.add_to_hass(hass)

    with patch(
        "custom_components.fourbox_s_series.coordinator.mqtt.async_publish",
        side_effect=_respond_to(_STANDARD_RESPONSES),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get(_ENTITY_ID)
    assert "preset_modes" not in state.attributes


async def test_configured_profiles_become_selectable_presets(
    hass: HomeAssistant, mqtt_mock
) -> None:
    entry = _thermostat_entry(profiles="Eco:901,Comfort:902")
    entry.add_to_hass(hass)

    with patch(
        "custom_components.fourbox_s_series.coordinator.mqtt.async_publish",
        side_effect=_respond_to(_STANDARD_RESPONSES),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get(_ENTITY_ID)
    assert state.attributes["preset_modes"] == ["Eco", "Comfort"]


async def test_setting_preset_mode_publishes_correct_profile_command(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """Selecting a configured preset must send `tS=<value>` with the
    user-declared profile value verbatim, and reflect it back as the
    (optimistic) current preset."""
    entry = _thermostat_entry(profiles="Eco:901,Comfort:902")
    entry.add_to_hass(hass)

    with patch(
        "custom_components.fourbox_s_series.coordinator.mqtt.async_publish",
        side_effect=_respond_to(_STANDARD_RESPONSES),
    ) as mock_publish:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        await hass.services.async_call(
            "climate",
            "set_preset_mode",
            {"entity_id": _ENTITY_ID, "preset_mode": "Eco"},
            blocking=True,
        )
        await hass.async_block_till_done()

    mock_publish.assert_any_call(hass, _CMND_TOPIC, "tS=901")
    state = hass.states.get(_ENTITY_ID)
    assert state.attributes["preset_mode"] == "Eco"


async def test_setting_unknown_preset_mode_is_ignored_gracefully(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """Selecting a preset that isn't in the configured list must not
    raise or publish anything -- just log and do nothing."""
    entry = _thermostat_entry(profiles="Eco:901")
    entry.add_to_hass(hass)

    with patch(
        "custom_components.fourbox_s_series.coordinator.mqtt.async_publish",
        side_effect=_respond_to(_STANDARD_RESPONSES),
    ) as mock_publish:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        mock_publish.reset_mock()

        from custom_components.fourbox_s_series.climate import SSeriesThermostat

        # Directly exercise the guard clause for an unknown preset,
        # since the "preset_mode" selector in HA's own service schema
        # would normally reject a value outside preset_modes before it
        # ever reaches the entity.
        entity = next(
            e
            for e in hass.data["entity_components"]["climate"].entities
            if isinstance(e, SSeriesThermostat)
        )
        await entity.async_set_preset_mode("NotConfigured")

    mock_publish.assert_not_called()
