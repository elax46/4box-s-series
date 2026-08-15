"""Unit tests for the command payload builder functions in `const.py`.

These build the exact strings published to `<ID>/cmnd`, so a typo here
would silently break commands against real hardware -- worth locking down
even though they're one-liners. No Home Assistant dependency needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent
        / "custom_components"
        / "fourbox_s_series"
    ),
)

from const import (  # noqa: E402
    motor_move_payload,
    motor_tilt_payload,
    pulse_payload,
    setpoint_payload,
    thermostat_mode_payload,
)


def test_motor_move_payload():
    assert motor_move_payload(50) == "motor=MOVE&perc=50"
    assert motor_move_payload(0) == "motor=MOVE&perc=0"
    assert motor_move_payload(100) == "motor=MOVE&perc=100"


def test_motor_tilt_payload():
    assert motor_tilt_payload(30) == "motor=TILT&perc=30"


def test_pulse_payload_default_relay():
    assert pulse_payload(1000) == "pulsetime=PULSETIME<ron>:1000&&RELAY1:ON"


def test_pulse_payload_custom_duration():
    assert pulse_payload(500) == "pulsetime=PULSETIME<ron>:500&&RELAY1:ON"


def test_pulse_payload_explicit_relay():
    assert (
        pulse_payload(2000, relay="RELAY2")
        == "pulsetime=PULSETIME<ron>:2000&&RELAY2:ON"
    )


def test_thermostat_mode_payload_off():
    assert thermostat_mode_payload(0) == "thermostatMode=0"


def test_thermostat_mode_payload_heating():
    assert thermostat_mode_payload(1) == "thermostatMode=1"


def test_thermostat_mode_payload_cooling():
    assert thermostat_mode_payload(2) == "thermostatMode=2"


def test_setpoint_payload():
    assert setpoint_payload(20.5) == "tS=20.5"
    assert setpoint_payload(18) == "tS=18"
