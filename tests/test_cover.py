"""Integration tests for the motor cover platform (cover.py), run
against a real (test) Home Assistant core instance via
`pytest-homeassistant-custom-component`.

Requires `requirements-test.txt` to be installed. Run with:

    pytest tests/test_cover.py
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
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_HAS_TILT,
    DEVICE_TYPE_MOTOR,
    DOMAIN,
)

_DEVICE_ID = "M053B-30AEA4A6D461"
_ENTITY_ID = "cover.m053b_30aea4a6d461"


@pytest.fixture
def expected_lingering_timers():
    return True


@pytest.fixture(autouse=True)
def mock_setup_publish():
    """Avoid failing on the motor=STATUS publish every cover entity
    sends on add (see cover.py's async_added_to_hass) when there's no
    device on the other end to answer it -- nothing waits on its
    response, so no patch is strictly required, but this keeps test
    output quiet.
    """
    yield


def _motor_entry(*, has_tilt: bool = True) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=_DEVICE_ID,
        data={
            CONF_DEVICE_ID: _DEVICE_ID,
            CONF_DEVICE_TYPE: DEVICE_TYPE_MOTOR,
            CONF_HAS_TILT: has_tilt,
        },
        source=config_entries.SOURCE_USER,
        unique_id=_DEVICE_ID,
    )


async def test_cover_created_with_tilt_features(hass: HomeAssistant, mqtt_mock) -> None:
    """A motor device with has_tilt=True must expose the tilt services,
    in addition to the always-present open/close/stop/set_position ones.
    """
    entry = _motor_entry(has_tilt=True)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(_ENTITY_ID)
    assert state is not None
    # CoverEntityFeature bitmask: OPEN(1)|CLOSE(2)|STOP(8)|SET_POSITION(4)
    # = 15, plus OPEN_TILT(16)|CLOSE_TILT(32)|SET_TILT_POSITION(128) = 176
    # -> 191 total.
    assert state.attributes["supported_features"] == 191


async def test_cover_created_without_tilt_features(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """A motor device with has_tilt=False must NOT expose tilt services."""
    entry = _motor_entry(has_tilt=False)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(_ENTITY_ID)
    assert state is not None
    # OPEN|CLOSE|STOP|SET_POSITION only = 15, no tilt bits set.
    assert state.attributes["supported_features"] == 15


async def test_cover_position_updates_from_stat_push(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """The exact combined /stat example from the vendor guide (section
    3.3) must correctly update position and clear the moving flags.
    """
    entry = _motor_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    async_fire_mqtt_message(hass, f"{_DEVICE_ID}/connect", "true")
    async_fire_mqtt_message(
        hass, f"{_DEVICE_ID}/stat", "00000011=>50=>0.0=>0.0=>STOPPED"
    )
    await hass.async_block_till_done()

    state = hass.states.get(_ENTITY_ID)
    assert state is not None
    assert state.attributes["current_position"] == 50


async def test_cover_infers_opening_and_closing_from_status(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """motor_status containing "UP"/"OPEN" or "DOWN"/"CLOS" must set the
    corresponding is_opening/is_closing state, per
    cover.py's `_infer_moving_direction`."""
    entry = _motor_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    async_fire_mqtt_message(hass, f"{_DEVICE_ID}/connect", "true")

    async_fire_mqtt_message(
        hass, f"{_DEVICE_ID}/stat", "00000011=>60=>5.0=>0.0=>OPENING"
    )
    await hass.async_block_till_done()
    state = hass.states.get(_ENTITY_ID)
    assert state.state == "opening"

    async_fire_mqtt_message(
        hass, f"{_DEVICE_ID}/stat", "00000011=>40=>5.0=>0.0=>CLOSING"
    )
    await hass.async_block_till_done()
    state = hass.states.get(_ENTITY_ID)
    assert state.state == "closing"


async def test_cover_is_closed_when_position_zero(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """Position 0 must report the cover as closed (is_closed)."""
    entry = _motor_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    async_fire_mqtt_message(hass, f"{_DEVICE_ID}/connect", "true")
    async_fire_mqtt_message(
        hass, f"{_DEVICE_ID}/stat", "00000011=>0=>0.0=>0.0=>STOPPED"
    )
    await hass.async_block_till_done()

    state = hass.states.get(_ENTITY_ID)
    assert state.state == "closed"


async def test_cover_availability_follows_connect_topic(
    hass: HomeAssistant, mqtt_mock
) -> None:
    entry = _motor_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Unavailable by default until a /connect message arrives.
    assert hass.states.get(_ENTITY_ID).state == "unavailable"

    async_fire_mqtt_message(hass, f"{_DEVICE_ID}/connect", "true")
    await hass.async_block_till_done()
    assert hass.states.get(_ENTITY_ID).state != "unavailable"

    async_fire_mqtt_message(hass, f"{_DEVICE_ID}/connect", "false")
    await hass.async_block_till_done()
    assert hass.states.get(_ENTITY_ID).state == "unavailable"


async def test_open_close_stop_publish_correct_commands(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """Calling the cover.open_cover/close_cover/stop_cover services must
    publish the exact vendor-documented commands."""
    entry = _motor_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    async_fire_mqtt_message(hass, f"{_DEVICE_ID}/connect", "true")

    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": _ENTITY_ID}, blocking=True
    )
    mqtt_mock.async_publish.assert_any_call(f"{_DEVICE_ID}/cmnd", "motor=UP", 0, False)

    await hass.services.async_call(
        "cover", "close_cover", {"entity_id": _ENTITY_ID}, blocking=True
    )
    mqtt_mock.async_publish.assert_any_call(
        f"{_DEVICE_ID}/cmnd", "motor=DOWN", 0, False
    )

    await hass.services.async_call(
        "cover", "stop_cover", {"entity_id": _ENTITY_ID}, blocking=True
    )
    mqtt_mock.async_publish.assert_any_call(
        f"{_DEVICE_ID}/cmnd", "motor=STOP", 0, False
    )


async def test_set_position_publishes_correct_command(
    hass: HomeAssistant, mqtt_mock
) -> None:
    entry = _motor_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    async_fire_mqtt_message(hass, f"{_DEVICE_ID}/connect", "true")

    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": _ENTITY_ID, "position": 42},
        blocking=True,
    )
    mqtt_mock.async_publish.assert_any_call(
        f"{_DEVICE_ID}/cmnd", "motor=MOVE&perc=42", 0, False
    )


async def test_tilt_commands_publish_correct_payloads(
    hass: HomeAssistant, mqtt_mock
) -> None:
    entry = _motor_entry(has_tilt=True)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    async_fire_mqtt_message(hass, f"{_DEVICE_ID}/connect", "true")

    await hass.services.async_call(
        "cover", "open_cover_tilt", {"entity_id": _ENTITY_ID}, blocking=True
    )
    mqtt_mock.async_publish.assert_any_call(
        f"{_DEVICE_ID}/cmnd", "motor=TILT&perc=100", 0, False
    )

    await hass.services.async_call(
        "cover", "close_cover_tilt", {"entity_id": _ENTITY_ID}, blocking=True
    )
    mqtt_mock.async_publish.assert_any_call(
        f"{_DEVICE_ID}/cmnd", "motor=TILT&perc=0", 0, False
    )

    await hass.services.async_call(
        "cover",
        "set_cover_tilt_position",
        {"entity_id": _ENTITY_ID, "tilt_position": 30},
        blocking=True,
    )
    mqtt_mock.async_publish.assert_any_call(
        f"{_DEVICE_ID}/cmnd", "motor=TILT&perc=30", 0, False
    )


async def test_setup_requests_status_on_add(hass: HomeAssistant, mqtt_mock) -> None:
    """The entity must ask the device to re-publish its current status
    on add (motor=STATUS), per the guide's documented mechanism for
    getting an immediate read instead of waiting for the next push."""
    entry = _motor_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    mqtt_mock.async_publish.assert_any_call(
        f"{_DEVICE_ID}/cmnd", "motor=STATUS", 0, False
    )
