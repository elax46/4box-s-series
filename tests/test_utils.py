"""Unit tests for `custom_components.s_series_mqtt.utils`.

These are plain, dependency-free tests (no Home Assistant required) --
everything in `utils.py` is pure string/data parsing, so it can and
should be tested in isolation. Run with:

    pytest tests/test_utils.py

The `parse_gpio_status` cases include the exact payloads captured from a
real M048D on firmware MO.14.00 (see the README's "Initial state on
setup/reload" section) as regression tests: if the firmware's response
format ever changes, or if the parsing logic regresses, these will catch
it immediately.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Import directly from the package folder without needing Home Assistant
# installed or the package installed in site-packages.
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "custom_components" / "s_series_mqtt")
)

from utils import (  # noqa: E402
    MotorStat,
    build_action_payload,
    model_from_device_id,
    parse_gpio_status,
    parse_motor_stat,
)


class TestBuildActionPayload:
    def test_single_channel_on(self):
        assert build_action_payload("ON", channel=1, total_channels=1) == "action=ON"

    def test_single_channel_off(self):
        assert build_action_payload("OFF", channel=1, total_channels=1) == "action=OFF"

    def test_single_channel_toggle(self):
        assert (
            build_action_payload("TOGGLE", channel=1, total_channels=1)
            == "action=TOGGLE"
        )

    def test_dual_channel_targets_correct_channel(self):
        assert build_action_payload("ON", channel=1, total_channels=2) == "action=ON1"
        assert build_action_payload("OFF", channel=2, total_channels=2) == "action=OFF2"


class TestModelFromDeviceId:
    def test_standard_format(self):
        assert model_from_device_id("M048B-30AEA4A6D460") == "M048B"

    def test_real_hardware_id(self):
        assert model_from_device_id("M048D-901506BADF40") == "M048D"

    def test_no_dash_falls_back_to_full_id(self):
        assert model_from_device_id("malformed") == "malformed"


class TestParseMotorStat:
    def test_vendor_guide_example(self):
        # Exact example from the vendor's MQTT guide, section 3.3.
        stat = parse_motor_stat("00000011=>50=>0.0=>0.0=>STOPPED")
        assert stat == MotorStat(
            gpio_status="00000011",
            position=50.0,
            power_w=0.0,
            motor_status="STOPPED",
        )

    def test_moving_status(self):
        stat = parse_motor_stat("00000011=>75=>12.3=>0.0=>OPENING")
        assert stat.position == 75.0
        assert stat.power_w == 12.3
        assert stat.motor_status == "OPENING"

    def test_malformed_payload_does_not_raise(self):
        stat = parse_motor_stat("not a valid payload")
        assert stat.position is None
        assert stat.power_w is None

    def test_empty_payload_does_not_raise(self):
        stat = parse_motor_stat("")
        assert stat.gpio_status == ""
        assert stat.position is None

    def test_truncated_payload_missing_fields(self):
        stat = parse_motor_stat("00000011=>50")
        assert stat.position == 50.0
        assert stat.power_w is None
        assert stat.motor_status is None


class TestParseGpioStatus:
    """Regression tests using real payloads captured from an M048D."""

    REAL_ON_PAYLOAD = "LED1_R:0;LED1_G:0;LED1_B:0;RELAY1:ON;SW1_DC:PULL;SW1_AC:PULL;"
    REAL_OFF_PAYLOAD = "LED1_R:0;LED1_G:0;LED1_B:0;RELAY1:OFF;SW1_DC:PULL;SW1_AC:PULL;"
    REAL_OFF_PAYLOAD_WITH_LED_COLOR = (
        "LED1_R:244;LED1_G:110;LED1_B:99;RELAY1:OFF;SW1_DC:PULL;SW1_AC:PULL;"
    )

    def test_real_on_payload(self):
        assert parse_gpio_status(self.REAL_ON_PAYLOAD, channels=1) == {1: True}

    def test_real_off_payload(self):
        assert parse_gpio_status(self.REAL_OFF_PAYLOAD, channels=1) == {1: False}

    def test_led_color_does_not_affect_relay_parsing(self):
        assert parse_gpio_status(
            self.REAL_OFF_PAYLOAD_WITH_LED_COLOR, channels=1
        ) == {1: False}

    def test_hypothetical_two_channel_format(self):
        # Not yet confirmed against real M053B dual-light hardware --
        # see the README roadmap -- but the parser should handle it if
        # the device follows the same convention as the single-channel
        # devices already verified.
        payload = (
            "LED1_R:0;LED1_G:0;LED1_B:0;RELAY1:ON;RELAY2:OFF;"
            "SW1_DC:PULL;SW1_AC:PULL;"
        )
        assert parse_gpio_status(payload, channels=2) == {1: True, 2: False}

    def test_channels_beyond_requested_count_are_ignored(self):
        payload = "RELAY1:ON;RELAY2:ON;RELAY3:ON;"
        # Only 2 channels requested, even though the payload has 3.
        assert parse_gpio_status(payload, channels=2) == {1: True, 2: True}

    def test_unparsable_payload_returns_empty_dict(self):
        assert parse_gpio_status("garbage", channels=1) == {}

    def test_empty_payload_returns_empty_dict(self):
        assert parse_gpio_status("", channels=1) == {}

    def test_no_relay_token_returns_empty_dict(self):
        # A hypothetical different-model response with no RELAY field at all.
        assert parse_gpio_status("LED1_R:0;LED1_G:0;LED1_B:0;", channels=1) == {}
