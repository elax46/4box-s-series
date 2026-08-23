"""Integration tests for the config flow, run against a real (test) Home
Assistant core instance via `pytest-homeassistant-custom-component`.

Requires `requirements-test.txt` to be installed. Run with:

    pytest tests/test_config_flow.py
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
from homeassistant.data_entry_flow import FlowResultType

from custom_components.fourbox_s_series.const import (
    CONF_CHANNELS,
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_ENERGY_POLL_INTERVAL,
    CONF_HAS_ENERGY,
    CONF_HAS_LED,
    CONF_HAS_TILT,
    CONF_PULSE_DURATION_MS,
    CONF_THERMOSTAT_POLL_INTERVAL,
    CONF_THERMOSTAT_PROFILES,
    DEVICE_TYPE_MOTOR,
    DEVICE_TYPE_PUSH,
    DEVICE_TYPE_RELAY,
    DEVICE_TYPE_THERMOSTAT,
    DOMAIN,
)


@pytest.fixture(autouse=True)
def mock_setup_entry():
    """Avoid actually setting up the integration (starting MQTT pollers,
    etc.) while testing the config flow in isolation."""
    with patch(
        "custom_components.fourbox_s_series.async_setup_entry", return_value=True
    ):
        yield


@pytest.fixture
def expected_lingering_timers():
    """The real `mqtt` component's client keeps its own internal periodic
    housekeeping timer running (`MQTT._async_start_misc_periodic`) for as
    long as `mqtt_mock` is active. This is an artifact of testing against
    the real MQTT component (which the discovery feature necessarily
    does), not something this integration's code starts or controls, so
    it's safe to allow rather than something to chase down further.
    """
    return True


async def test_relay_flow_creates_entry(hass: HomeAssistant, mqtt_mock) -> None:
    """The full happy path: family picker -> relay options -> entry created."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEVICE_TYPE: DEVICE_TYPE_RELAY,
            CONF_DEVICE_ID: "M048D-901506BADF40",
            "name": "",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "relay"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_CHANNELS: 1, CONF_HAS_ENERGY: True, "energy_poll_interval": 300},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "M048D-901506BADF40"
    assert result["data"][CONF_DEVICE_ID] == "M048D-901506BADF40"
    assert result["data"][CONF_DEVICE_TYPE] == DEVICE_TYPE_RELAY
    assert result["data"][CONF_CHANNELS] == 1


async def test_invalid_device_id_rejected(hass: HomeAssistant, mqtt_mock) -> None:
    """A device ID containing a slash must be rejected with an error, not
    silently accepted (it would break every topic built from it)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEVICE_TYPE: DEVICE_TYPE_RELAY,
            CONF_DEVICE_ID: "invalid/id",
            "name": "",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_device_id"}


async def test_duplicate_device_id_aborts(hass: HomeAssistant, mqtt_mock) -> None:
    """Adding the same device ID twice must abort the second attempt,
    not create two competing config entries for the same physical device."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEVICE_TYPE: DEVICE_TYPE_RELAY,
            CONF_DEVICE_ID: "M048D-901506BADF40",
            "name": "",
        },
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_CHANNELS: 1, CONF_HAS_ENERGY: True, "energy_poll_interval": 300},
    )

    # Try to add the exact same device ID again.
    result2 = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result2["flow_id"],
        {
            CONF_DEVICE_TYPE: DEVICE_TYPE_RELAY,
            CONF_DEVICE_ID: "M048D-901506BADF40",
            "name": "",
        },
    )
    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


async def test_discovery_finds_devices_announced_via_connect(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """A device that fires its retained `<ID>/connect = true` birth
    message during the flow's scan window should show up as a discovered
    option -- this is the core promise of the discovery feature."""

    async def _fire_during_scan(*args, **kwargs):
        async_fire_mqtt_message(hass, "M048D-901506BADF40/connect", "true")

    # Patch the scan's sleep so the test doesn't actually wait 2 real
    # seconds, and fire the discovery message during that "window".
    with patch(
        "custom_components.fourbox_s_series.config_flow.asyncio.sleep",
        side_effect=_fire_during_scan,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        # async_fire_mqtt_message schedules a background dispatch task;
        # let it finish before the test's strict lingering-task check runs.
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    # The device ID field's selector should list the discovered device.
    device_id_field = next(
        f for f in result["data_schema"].schema if str(f) == "device_id"
    )
    selector_config = result["data_schema"].schema[device_id_field].config
    assert "M048D-901506BADF40" in selector_config["options"]


async def test_discovery_excludes_offline_devices(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """A `connect = false` message must not be treated as a discovered
    (usable) device."""

    async def _fire_during_scan(*args, **kwargs):
        async_fire_mqtt_message(hass, "M048D-901506BADF40/connect", "false")

    with patch(
        "custom_components.fourbox_s_series.config_flow.asyncio.sleep",
        side_effect=_fire_during_scan,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        await hass.async_block_till_done()

    device_id_field = next(
        f for f in result["data_schema"].schema if str(f) == "device_id"
    )
    field_type = result["data_schema"].schema[device_id_field]
    # No devices discovered -> plain str field, not a SelectSelector.
    assert field_type is str


async def test_discovery_failure_falls_back_to_manual_entry(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """If the MQTT scan itself raises (e.g. MQTT not ready yet), the flow
    must still show a usable manual-entry form rather than crashing."""
    with patch(
        "custom_components.fourbox_s_series.config_flow.mqtt.async_subscribe",
        side_effect=RuntimeError("boom"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_motor_flow_creates_entry(hass: HomeAssistant, mqtt_mock) -> None:
    """Happy path for the motor family: family picker -> motor options
    (has_tilt) -> entry created."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEVICE_TYPE: DEVICE_TYPE_MOTOR,
            CONF_DEVICE_ID: "M053B-30AEA4A6D461",
            "name": "",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "motor"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HAS_TILT: True}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE_TYPE] == DEVICE_TYPE_MOTOR
    assert result["data"][CONF_HAS_TILT] is True


async def test_push_flow_creates_entry(hass: HomeAssistant, mqtt_mock) -> None:
    """Happy path for the Uniko Push family: family picker -> pulse
    duration -> entry created."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEVICE_TYPE: DEVICE_TYPE_PUSH,
            CONF_DEVICE_ID: "M048B-30AEA4A6D462",
            "name": "",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "push"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PULSE_DURATION_MS: 2000}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PULSE_DURATION_MS] == 2000


async def test_thermostat_flow_creates_entry(hass: HomeAssistant, mqtt_mock) -> None:
    """Happy path for the thermostat family: family picker -> poll
    interval + profiles -> entry created."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEVICE_TYPE: DEVICE_TYPE_THERMOSTAT,
            CONF_DEVICE_ID: "M048B-30AEA4A6D463",
            "name": "",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "thermostat"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_THERMOSTAT_POLL_INTERVAL: 60,
            CONF_THERMOSTAT_PROFILES: "Eco:901",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_THERMOSTAT_POLL_INTERVAL] == 60
    assert result["data"][CONF_THERMOSTAT_PROFILES] == "Eco:901"


async def test_options_flow_relay_updates_poll_interval_and_led(
    hass: HomeAssistant, mqtt_mock
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="M048D-901506BADF40",
        data={
            CONF_DEVICE_ID: "M048D-901506BADF40",
            CONF_DEVICE_TYPE: DEVICE_TYPE_RELAY,
            CONF_CHANNELS: 1,
            CONF_HAS_ENERGY: True,
        },
        source=config_entries.SOURCE_USER,
        unique_id="M048D-901506BADF40",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENERGY_POLL_INTERVAL: 600, CONF_HAS_LED: True},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_ENERGY_POLL_INTERVAL] == 600
    assert entry.options[CONF_HAS_LED] is True


async def test_options_flow_push_updates_pulse_duration(
    hass: HomeAssistant, mqtt_mock
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="M048B-30AEA4A6D462",
        data={
            CONF_DEVICE_ID: "M048B-30AEA4A6D462",
            CONF_DEVICE_TYPE: DEVICE_TYPE_PUSH,
            CONF_PULSE_DURATION_MS: 1000,
        },
        source=config_entries.SOURCE_USER,
        unique_id="M048B-30AEA4A6D462",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PULSE_DURATION_MS: 3000}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_PULSE_DURATION_MS] == 3000


async def test_options_flow_thermostat_updates_poll_interval_and_profiles(
    hass: HomeAssistant, mqtt_mock
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="M048B-30AEA4A6D463",
        data={
            CONF_DEVICE_ID: "M048B-30AEA4A6D463",
            CONF_DEVICE_TYPE: DEVICE_TYPE_THERMOSTAT,
            CONF_THERMOSTAT_POLL_INTERVAL: 120,
            CONF_THERMOSTAT_PROFILES: "",
        },
        source=config_entries.SOURCE_USER,
        unique_id="M048B-30AEA4A6D463",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_THERMOSTAT_POLL_INTERVAL: 30, CONF_THERMOSTAT_PROFILES: "Eco:901"},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_THERMOSTAT_POLL_INTERVAL] == 30
    assert entry.options[CONF_THERMOSTAT_PROFILES] == "Eco:901"


async def test_options_flow_motor_has_nothing_to_configure(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """Motor devices have no post-setup options today; opening the
    options flow must still complete cleanly (empty entry, no form)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="M053B-30AEA4A6D461",
        data={
            CONF_DEVICE_ID: "M053B-30AEA4A6D461",
            CONF_DEVICE_TYPE: DEVICE_TYPE_MOTOR,
            CONF_HAS_TILT: True,
        },
        source=config_entries.SOURCE_USER,
        unique_id="M053B-30AEA4A6D461",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.CREATE_ENTRY
