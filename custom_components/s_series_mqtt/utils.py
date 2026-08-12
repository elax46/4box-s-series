"""Small helpers shared across platforms."""

from __future__ import annotations

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
    """Best-effort parse of a `gpiostatus=GET` response into per-channel on/off.

    The vendor guide's Appendix A only lists the command
    (`gpiostatus=GET` -> "Lettura GPIO") without documenting the exact bit
    layout of the response. This assumes a binary string (e.g.
    "00000001") where the least-significant (rightmost) bit is channel 1
    and the next bit is channel 2 -- the same convention used in the
    combined motor `/stat` message's gpio_status field.

    **This mapping is unverified against real hardware for every model.**
    If it doesn't match what you observe (e.g. via `mosquitto_sub` while
    toggling the relay and running `gpiostatus=GET` manually), please open
    an issue with the raw payload you saw -- see the README.

    Returns an empty dict (meaning "unknown, don't override anything") if
    the response can't be parsed as a binary string at all.
    """
    bits = response.strip()
    if not bits or any(c not in "01" for c in bits):
        return {}

    result: dict[int, bool] = {}
    for channel in range(1, channels + 1):
        bit_index_from_right = channel - 1
        if bit_index_from_right >= len(bits):
            continue
        bit = bits[-(bit_index_from_right + 1)]
        result[channel] = bit == "1"
    return result
