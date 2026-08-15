"""Integration tests for the config entry setup/unload lifecycle, run
against a real (test) Home Assistant core instance via
`pytest-homeassistant-custom-component`.

Requires `requirements-test.txt` to be installed. Run with:

    pytest tests/test_setup.py
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

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


def _relay_entry(*, has_energy: bool = False, **extra_data) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="M048D-901506BADF40",
        data={
            CONF_DEVICE_ID: "M048D-901506BADF40",
            CONF_DEVICE_TYPE: DEVICE_TYPE_RELAY,
            CONF_CHANNELS: 1,
            CONF_HAS_ENERGY: has_energy,
            **extra_data,
        },
        source=config_entries.SOURCE_USER,
        unique_id="M048D-901506BADF40",
    )


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
    entry = _relay_entry(has_energy=True, **{CONF_ENERGY_POLL_INTERVAL: 300})
    entry.add_to_hass(hass)

    # Nothing on the mock broker answers the energyActive=RELAY1 query,
    # so it will time out. Speed that up so the test doesn't burn 10 real
    # seconds (DEFAULT_MQTT_RESPONSE_TIMEOUT) waiting for it.
    with patch(
        "custom_components.fourbox_s_series.coordinator.DEFAULT_MQTT_RESPONSE_TIMEOUT",
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
    entry = _relay_entry()
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
    entry = _relay_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == "form"
    assert result["step_id"] == "init"


async def test_setup_succeeds_when_mqtt_subscribe_race_at_startup(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """Regression test for a real production crash: if the relay state
    refresher's `<ID>/connect` subscribe races ahead of MQTT actually
    being ready (observed in production as `HomeAssistantError: Cannot
    subscribe to topic ..., make sure MQTT is set up correctly`), that
    must not abort setup of the whole entry -- it should degrade
    gracefully (log a warning, leave initial state unknown), not require
    a manual reload.

    Only the FIRST `mqtt.async_subscribe` call is made to fail (which,
    given the call order in `async_setup_entry`, is the refresher's own
    subscribe -- it starts before the switch platform is forwarded).
    Later calls fall through to the real implementation, so the switch
    entity's own subscriptions (state/availability topics) still work
    normally; this also models the realistic case of a brief startup
    race that resolves itself moments later, not MQTT being broken for
    the rest of the test.
    """
    entry = _relay_entry()
    entry.add_to_hass(hass)

    from custom_components.fourbox_s_series.coordinator import mqtt as coordinator_mqtt

    real_async_subscribe = coordinator_mqtt.async_subscribe
    call_count = 0

    async def _flaky_subscribe(hass_arg, topic, msg_callback, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise HomeAssistantError(
                f'Cannot subscribe to topic "{topic}", '
                "make sure MQTT is set up correctly"
            )
        return await real_async_subscribe(
            hass_arg, topic, msg_callback, *args, **kwargs
        )

    with patch(
        "custom_components.fourbox_s_series.coordinator.mqtt.async_subscribe",
        side_effect=_flaky_subscribe,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.entry_id in hass.data[DOMAIN]
    assert hass.states.get("switch.m048d_901506badf40_socket") is not None


async def test_relay_state_refreshes_when_device_connects_after_setup(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """The core fix for the "new device: state not fetched until reload"
    bug: a device that only announces itself online (fires its retained
    `<ID>/connect = true` birth message) SOME TIME AFTER this integration
    finished setting up -- e.g. a brand-new device still completing its
    own MQTT reconnect after being configured in the vendor app -- must
    still get its switch state corrected automatically, with no manual
    reload required.
    """
    entry = _relay_entry()
    entry.add_to_hass(hass)

    async def _publish_side_effect(hass_arg, topic, payload, *args, **kwargs):
        # Simulate the device answering gpiostatus=GET the moment the
        # integration asks, exactly as SSeriesRelayStateRefresher does
        # when it sees the device come online.
        if topic == "M048D-901506BADF40/cmnd" and payload == "gpiostatus=GET":
            async_fire_mqtt_message(hass, "M048D-901506BADF40/info", "RELAY1:ON;")

    with patch(
        "custom_components.fourbox_s_series.coordinator.mqtt.async_publish",
        side_effect=_publish_side_effect,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Nothing has announced the device online yet -- the switch is
        # still unavailable at this point (available=False initially),
        # matching the reported symptom.
        state = hass.states.get("switch.m048d_901506badf40_socket")
        assert state is not None
        assert state.state == "unavailable"

        # The device finishes connecting to the broker some time later
        # and fires its retained birth message.
        async_fire_mqtt_message(hass, "M048D-901506BADF40/connect", "true")
        await hass.async_block_till_done()

    # No reload was triggered anywhere above -- the state must now be
    # correct purely from the refresher reacting to /connect.
    state = hass.states.get("switch.m048d_901506badf40_socket")
    assert state is not None
    assert state.state == "on"
