"""Small helpers for building 4box MQTT command payloads."""

from __future__ import annotations


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
