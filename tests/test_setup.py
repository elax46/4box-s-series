"""Integration tests for the config entry setup/unload lifecycle, run
against a real (test) Home Assistant core instance via
`pytest-homeassistant-custom-component`.

Requires `requirements-test.txt` to be installed. Run with:

    pytest tests/test_setup.py
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.fourbox_s_series import async_unload_entry
from custom_components.fourbox_s_series.const import (
    CONF_CHANNELS,
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_ENERGY_POLL_INTERVAL,
    CONF_HAS_ENERGY,
    DEVICE_TYPE_RELAY,
    DOMAIN,
)


@pytest.fixture
def expected_lingering_timers():
    """The real `mqtt` component's client keeps its own internal periodic
    housekeeping timer running for as long as `mqtt_mock` is active --
    an artifact of testing against the real MQTT component, not
    something this integration starts or controls.
    """
    return True


async def test_setup_succeeds_even_when_energy_query_times_out(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """A relay device's energyActive=RELAY1 initial query can time out
    (device slow to respond, briefly offline, etc.) without that failure
    aborting setup of the entry as a whole.

    Regression test for a real bug: `async_config_entry_first_refresh()`
    raises `ConfigEntryNotReady` on failure, which used to make the
    *entire* entry (switch included, not just the energy sensor) fail to
    set up and left `hass.data[DOMAIN][entry_id]` unpopulated -- which
    then made `async_unload_entry` crash with a `KeyError` on the next
    reload/unload/Configure, surfacing to the user as a 500 error.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="M048D-901506BADF40",
        data={
            CONF_DEVICE_ID: "M048D-901506BADF40",
            CONF_DEVICE_TYPE: DEVICE_TYPE_RELAY,
            CONF_CHANNELS: 1,
            CONF_HAS_ENERGY: True,
            CONF_ENERGY_POLL_INTERVAL: 300,
        },
        source=config_entries.SOURCE_USER,
        unique_id="M048D-901506BADF40",
    )
    entry.add_to_hass(hass)

    # Nothing on the mock broker answers the energyActive=RELAY1 query,
    # so it will time out. Speed that up so the test doesn't burn 10 real
    # seconds (DEFAULT_MQTT_RESPONSE_TIMEOUT) waiting for it.
    with patch(
        "custom_components.s_series_mqtt.coordinator.DEFAULT_MQTT_RESPONSE_TIMEOUT",
        0.05,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # The entry must be fully LOADED, not stuck in SETUP_RETRY.
    assert entry.state is ConfigEntryState.LOADED
    assert entry.entry_id in hass.data[DOMAIN]

    # The switch entity must exist and be usable, independent of the
    # energy sensor's failed initial poll.
    assert hass.states.get("switch.m048d_901506badf40_socket") is not None

    # Unloading (exactly what Configure/Reload/Delete trigger under the
    # hood) must not raise, even though the energy coordinator never
    # successfully refreshed.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.entry_id not in hass.data[DOMAIN]


async def test_unload_does_not_crash_if_setup_never_completed(
    hass: HomeAssistant,
) -> None:
    """Direct regression test for the KeyError itself: calling
    `async_unload_entry` when `hass.data[DOMAIN]` has no entry for this
    entry_id (e.g. a prior setup attempt crashed before populating it)
    must not raise.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="M048D-901506BADF40",
        data={
            CONF_DEVICE_ID: "M048D-901506BADF40",
            CONF_DEVICE_TYPE: DEVICE_TYPE_RELAY,
            CONF_CHANNELS: 1,
            CONF_HAS_ENERGY: False,
        },
        source=config_entries.SOURCE_USER,
        unique_id="M048D-901506BADF40",
    )
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})  # deliberately NOT populated for this entry

    await async_unload_entry(hass, entry)  # must not raise


async def test_options_flow_configure_button_does_not_crash(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """Regression test for a real production crash: opening the options
    flow (exactly what the frontend's CONFIGURE button does, via
    `hass.config_entries.options.async_init`) used to raise
    `AttributeError: property 'config_entry' of 'SSeriesMqttOptionsFlow'
    object has no setter` on Home Assistant versions where the
    OptionsFlow.config_entry setter was removed (this integration's
    custom __init__ tried to assign to it manually) -- surfacing to the
    user as a 500 Internal Server Error.

    This test drives the flow the same way the real frontend does
    (through the FlowManager, not by constructing the class directly),
    so it exercises the exact code path that broke in production.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="M048D-901506BADF40",
        data={
            CONF_DEVICE_ID: "M048D-901506BADF40",
            CONF_DEVICE_TYPE: DEVICE_TYPE_RELAY,
            CONF_CHANNELS: 1,
            CONF_HAS_ENERGY: False,
        },
        source=config_entries.SOURCE_USER,
        unique_id="M048D-901506BADF40",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == "form"
    assert result["step_id"] == "init"


async def test_setup_succeeds_when_mqtt_subscribe_race_at_startup(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """Regression test for a real production crash: if the initial
    gpiostatus=GET subscribe races ahead of MQTT actually being ready
    (observed in production as `HomeAssistantError: Cannot subscribe to
    topic ..., make sure MQTT is set up correctly`), that must not abort
    setup of the whole entry -- it should degrade the same way a timed-out
    *response* already did (log a warning, leave initial state unknown),
    not a different, worse way (whole entry fails, requiring a manual
    reload).
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="M048D-901506BADF40",
        data={
            CONF_DEVICE_ID: "M048D-901506BADF40",
            CONF_DEVICE_TYPE: DEVICE_TYPE_RELAY,
            CONF_CHANNELS: 1,
            CONF_HAS_ENERGY: False,
        },
        source=config_entries.SOURCE_USER,
        unique_id="M048D-901506BADF40",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.s_series_mqtt.RequestResponsePoller.async_start",
        side_effect=HomeAssistantError(
            'Cannot subscribe to topic "M048D-901506BADF40/info", '
            "make sure MQTT is set up correctly"
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.entry_id in hass.data[DOMAIN]
    assert hass.states.get("switch.m048d_901506badf40_socket") is not None
