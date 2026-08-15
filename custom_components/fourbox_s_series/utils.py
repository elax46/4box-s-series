"""Small helpers shared across platforms."""

from __future__ import annotations

import re
from dataclasses import dataclass


def build_action_payload(action: str, channel: int, total_channels: int) -> str:
    """Build the `action=` command payload for a relay channel.

    Single-channel devices (P40S, M048B/C/D, M054F) use e.g. ``action=ON``.
    Two-channel devices (M053B in dual-light mode) use ``action=ON1`` /
    ``action=ON2`` depending on the target channel.
    """
    if total_channels <= 1:
        return f"action={action}"
    return f"action={action}{channel}"


def model_from_device_id(device_id: str) -> str:
    """Extract the model code from a device ID.

    Device IDs follow the vendor's `<model>-<MAC>` format, e.g.
    ``M048B-30AEA4A6D460`` -> ``M048B``. Falls back to the full device ID
    if it doesn't contain a dash (shouldn't happen with real devices, but
    keeps entity/device setup from crashing on a malformed ID).
    """
    model, _, _ = device_id.partition("-")
    return model or device_id


@dataclass
class MotorStat:
    """Parsed contents of a motor `<ID>/stat` message.

    Format documented by the vendor as:
    ``<gpio_status>=><motor_position>=><relay1_watt>=><relay2_watt>=><motor_status>``
    e.g. ``00000011=>50=>0.0=>0.0=>STOPPED``.
    """

    gpio_status: str | None
    position: float | None
    power_w: float | None
    motor_status: str | None


def parse_motor_stat(payload: str) -> MotorStat:
    """Parse a motor `<ID>/stat` payload, tolerating unexpected formats.

    Any field that can't be parsed is returned as None rather than raising,
    since the vendor guide doesn't exhaustively document every possible
    `motor_status` value or edge case.
    """
    parts = payload.split("=>")

    def _get(index: int) -> str | None:
        return parts[index].strip() if index < len(parts) else None

    gpio_status = _get(0)

    position: float | None = None
    if (raw := _get(1)) is not None:
        try:
            position = float(raw)
        except ValueError:
            position = None

    power_w: float | None = None
    if (raw := _get(2)) is not None:
        try:
            power_w = float(raw)
        except ValueError:
            power_w = None

    motor_status = _get(4)

    return MotorStat(
        gpio_status=gpio_status,
        position=position,
        power_w=power_w,
        motor_status=motor_status,
    )


def parse_gpio_status(response: str, channels: int) -> dict[int, bool]:
    """Parse a `gpiostatus=GET` response into per-channel on/off state.

    Confirmed against a real M048D on firmware MO.14.00, the response is
    a semicolon-separated list of `KEY:value` pairs, e.g.:

        LED1_R:0;LED1_G:0;LED1_B:0;RELAY1:ON;SW1_DC:PULL;SW1_AC:PULL;

    This is *not* documented in the vendor guide's Appendix A beyond the
    command name -- the format above was reverse-engineered from a real
    device and may not be identical across every model in the family
    (in particular, other fields like LEDx_R/G/B and SW1_DC/SW1_AC are
    ignored here since they aren't relevant to relay state, but may
    differ or be absent on models without an indicator LED). Only
    `RELAY<n>:ON`/`RELAY<n>:OFF` tokens are extracted.

    Returns an empty dict (meaning "unknown, don't override anything") if
    no `RELAY<n>` token is found at all -- e.g. if a different model
    returns a genuinely different format.
    """
    result: dict[int, bool] = {}
    for token in response.split(";"):
        token = token.strip()
        if not token or ":" not in token:
            continue
        key, _, value = token.partition(":")
        match = re.match(r"^RELAY(\d+)$", key.strip().upper())
        if not match:
            continue
        channel = int(match.group(1))
        if 1 <= channel <= channels:
            result[channel] = value.strip().upper() == "ON"
    return result


def parse_thermostat_profiles(raw: str) -> list[tuple[str, str]]:
    """Parse a user-entered thermostat profile list into (name, value) pairs.

    Expected format, comma-separated `Name:tS-value` pairs, e.g.:

        Eco:901,Comfort:902,Manual 18:-1;Manual;18

    `tS-value` is whatever goes after `tS=` on the wire -- a numeric
    profile id (`901`) or a mode+name recall (`1;Eco`), exactly as
    documented in the vendor guide section 6.2. This integration has no
    way to know which profiles actually exist on a given device, so the
    user must supply this list themselves, matching what they configured
    via the vendor's own app.

    Malformed entries (missing ':', empty name, empty value) are skipped
    rather than raising, so one typo doesn't break the whole list. Order
    is preserved and duplicates by name are not de-duplicated (last one
    wins when building the lookup dict elsewhere), which keeps this
    function a pure, side-effect-free parser.
    """
    profiles: list[tuple[str, str]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        name, _, value = entry.partition(":")
        name = name.strip()
        value = value.strip()
        if name and value:
            profiles.append((name, value))
    return profiles
