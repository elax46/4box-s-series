"""Integration tests for the config entry setup/unload lifecycle, run
against a real (test) Home Assistant core instance via
`pytest-homeassistant-custom-component`.

Requires `requirements-test.txt` to be installed. Run with:

    pytest tests/test_setup.py
"""

from __future__ import annotations

import asyncio
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
from custom_components.fourbox_s_series.coordinator import (
    RequestResponsePoller,
    SSeriesRelayStateRefresher,
)
from custom_components.fourbox_s_series.switch import SSeriesRelaySwitch
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
    """Regression test for a real production crash: if the FIRST MQTT
    subscribe in `async_setup_entry` races ahead of MQTT actually being
    ready (observed in production as `HomeAssistantError: Cannot
    subscribe to topic ..., make sure MQTT is set up correctly`), that
    must not abort setup of the whole entry -- it should degrade
    gracefully (log a warning, leave initial state unknown), not require
    a manual reload.

    Only the FIRST `mqtt.async_subscribe` call is made to fail (given the
    call order in `async_setup_entry`, this is the shared
    `RequestResponsePoller`'s own `/info` subscribe, which runs before
    the relay state refresher's `/connect` subscribe and before the
    switch platform is forwarded). Later calls fall through to the real
    implementation, so the switch entity's own subscriptions (state/
    availability topics) still work normally; this also models the
    realistic case of a brief startup race that resolves itself moments
    later, not MQTT being broken for the rest of the test.
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


async def test_concurrent_refresher_and_energy_poll_do_not_cross_talk(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """The actual root cause of "state not recovered even after
    reloading": an earlier design had the relay state refresher create
    its OWN independent `RequestResponsePoller` on every refresh, while
    `SSeriesEnergyCoordinator` had its own separate one too -- both
    subscribed to the same `<ID>/info` topic at once. Since that topic
    has no correlation ID, whichever reply arrived first resolved BOTH
    pollers' pending requests, corrupting whichever one didn't actually
    ask for it.

    This reproduces the exact trigger: the refresher's gpiostatus=GET
    fires the instant the device's retained `/connect=true` birth message
    arrives (right at setup), which happens concurrently with the energy
    coordinator's own first poll cycle (power/current/energy queries) for
    the SAME device. With a single shared poller (the fix), every
    request is serialized -- each command gets its own matching reply,
    regardless of how many features fire requests around the same time.
    """
    entry = _relay_entry(has_energy=True, **{CONF_ENERGY_POLL_INTERVAL: 300})
    entry.add_to_hass(hass)

    # One reply queued per expected command, in the order they'll be
    # requested: gpiostatus=GET (refresher) interleaved with
    # power/current/energyActive (energy coordinator). If requests were
    # NOT serialized, replies could be consumed out of order by the
    # wrong poller.
    responses = {
        "gpiostatus=GET": "RELAY1:ON;",
        "power=RELAY1": "123.4 (Watt)",
        "current=RELAY1": "0.54 (Ampere)",
        "energyActive=RELAY1": "12.345 (kWh)",
    }

    async def _publish_side_effect(hass_arg, topic, payload, *args, **kwargs):
        if topic == "M048D-901506BADF40/cmnd" and payload in responses:
            async_fire_mqtt_message(hass, "M048D-901506BADF40/info", responses[payload])

    with patch(
        "custom_components.fourbox_s_series.coordinator.mqtt.async_publish",
        side_effect=_publish_side_effect,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # The device's retained /connect=true birth message is what
        # triggers the refresher's gpiostatus=GET in the first place; on
        # the real broker it's already retained before setup even
        # starts, but the test broker starts empty, so fire it
        # explicitly here to trigger the refresher concurrently with the
        # energy coordinator's own poll (already in flight above).
        async_fire_mqtt_message(hass, "M048D-901506BADF40/connect", "true")
        await hass.async_block_till_done()

    # All four values must have landed on the RIGHT entity -- if
    # cross-talk occurred, at least one of these would be wrong, missing,
    # or would have received a value clearly meant for a different query
    # (e.g. the switch reading "123.4" instead of "on").
    switch_state = hass.states.get("switch.m048d_901506badf40_socket")
    assert switch_state is not None
    assert switch_state.state == "on"

    power_state = hass.states.get("sensor.m048d_901506badf40_power")
    assert power_state is not None
    assert power_state.state == "123.4"

    current_state = hass.states.get("sensor.m048d_901506badf40_current")
    assert current_state is not None
    assert current_state.state == "0.54"

    energy_state = hass.states.get("sensor.m048d_901506badf40_energy")
    assert energy_state is not None
    assert energy_state.state == "12.345"


async def test_shared_poller_serializes_concurrent_requests(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """Direct, deterministic test of `RequestResponsePoller`'s internal
    lock: two concurrent `async_request()` calls on the SAME poller
    instance must each get their own correct reply, even when both
    requests are genuinely in flight at once (simulated here via a
    short artificial delay before each reply, so neither resolves before
    the other is published -- unlike the higher-level integration test
    above, which turned out too deterministic/synchronous in the mock
    MQTT dispatch to reliably force real overlap).

    Without the lock, the second `async_request()` call would overwrite
    the first call's `_pending` future before it resolves, causing the
    first call to hang until timeout while the second grabs whichever
    reply arrives -- exactly the cross-talk mechanism that corrupted
    switch/sensor state in production.
    """
    poller = RequestResponsePoller(hass, "TESTDEV-0001")
    await poller.async_start()

    async def _delayed_publish_side_effect(hass_arg, topic, payload, *args, **kwargs):
        async def _respond():
            await asyncio.sleep(0.05)
            if payload == "CMD_A":
                async_fire_mqtt_message(hass, "TESTDEV-0001/info", "RESPONSE_A")
            elif payload == "CMD_B":
                async_fire_mqtt_message(hass, "TESTDEV-0001/info", "RESPONSE_B")

        hass.async_create_task(_respond())

    try:
        with patch(
            "custom_components.fourbox_s_series.coordinator.mqtt.async_publish",
            side_effect=_delayed_publish_side_effect,
        ):
            results = await asyncio.gather(
                poller.async_request("CMD_A"),
                poller.async_request("CMD_B"),
            )
    finally:
        await poller.async_stop()

    # Each request must be paired with ITS OWN reply, not whichever one
    # happened to arrive first for both.
    assert results == ["RESPONSE_A", "RESPONSE_B"]


async def test_switch_seeds_from_refresh_that_completed_before_it_existed(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """Regression test for a deterministic ordering bug: the relay state
    refresher starts (and its retained /connect message gets processed)
    BEFORE the switch platform is forwarded in `async_setup_entry`. If
    the refresher's gpiostatus=GET reply arrives and gets dispatched
    before any switch entity exists to receive it, the live dispatcher
    signal is lost -- entirely possible, since the device can reply
    faster than the rest of setup proceeds. The switch must still end up
    correct by reading `SSeriesRelayStateRefresher.last_states` directly
    at construction time as a fallback.

    Tested directly and deterministically here (bypassing the full
    integration setup flow, whose scheduling order isn't something a
    test can reliably force either way): complete a refresh FIRST, then
    construct the switch entity afterwards, exactly mirroring the
    "device replies before its entity exists" ordering.
    """
    poller = RequestResponsePoller(hass, "M048D-901506BADF40")
    await poller.async_start()
    refresher = SSeriesRelayStateRefresher(hass, "M048D-901506BADF40", 1, poller)

    async def _publish_side_effect(hass_arg, topic, payload, *args, **kwargs):
        if topic == "M048D-901506BADF40/cmnd" and payload == "gpiostatus=GET":
            async_fire_mqtt_message(hass, "M048D-901506BADF40/info", "RELAY1:ON;")

    with patch(
        "custom_components.fourbox_s_series.coordinator.mqtt.async_publish",
        side_effect=_publish_side_effect,
    ):
        # The refresh completes here, with NO switch entity in
        # existence yet -- any dispatcher signal fired during this call
        # has nobody listening for it.
        await refresher.async_refresh_now()

    await poller.async_stop()
    assert refresher.last_states == {1: True}

    # The switch is only constructed now, well after the refresh above
    # already ran -- exactly as switch.py's async_setup_entry does,
    # since platforms are forwarded after the refresher starts.
    switch = SSeriesRelaySwitch("M048D-901506BADF40", 1, 1, refresher)
    assert switch.is_on is True
    assert switch.available is True
