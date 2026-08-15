"""Cover platform for the 4box S Series (MQTT) integration.

Covers the M053B configured as a motorized shutter/awning actuator.
Position and tilt use the vendor's native 0-100 scale (0 = fully closed /
down, 100 = fully open / up), which happens to match Home Assistant's own
`current_cover_position` convention directly -- no inversion needed.

State comes from the device's combined `/stat` push message (see
`utils.parse_motor_stat`); no polling is required for position or moving
status. The vendor guide's `motor_status` values aren't fully enumerated
beyond the documented "STOPPED", so `is_opening`/`is_closing` are derived
heuristically (see `_infer_moving_direction`) and may not be perfectly
accurate for every possible firmware status string -- position and
open/closed state are always correct regardless, since those come
straight from the numeric position field.
"""

from __future__ import annotations

import logging

from homeassistant.components import mqtt
from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    MANUFACTURER,
    MOTOR_CMD_DOWN,
    MOTOR_CMD_STATUS,
    MOTOR_CMD_STOP,
    MOTOR_CMD_UP,
    TOPIC_CMND,
    TOPIC_CONNECT,
    TOPIC_MOTOR_STAT,
    motor_move_payload,
    motor_tilt_payload,
)
from .utils import model_from_device_id, parse_motor_stat

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the cover entity for one motor device."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    device_id: str = entry_data["device_id"]
    has_tilt: bool = entry_data.get("has_tilt", True)

    async_add_entities([SSeriesMotorCover(device_id, has_tilt)])


def _infer_moving_direction(
    motor_status: str | None,
) -> tuple[bool | None, bool | None]:
    """Best-effort (is_opening, is_closing) from a motor_status string.

    Returns (None, None) when the status can't be confidently classified,
    which HA treats as "not currently known to be moving" without
    asserting a wrong direction.
    """
    if not motor_status:
        return (None, None)
    status = motor_status.upper()
    if "STOP" in status:
        return (False, False)
    if "UP" in status or "OPEN" in status:
        return (True, False)
    if "DOWN" in status or "CLOS" in status:
        return (False, True)
    return (None, None)


class SSeriesMotorCover(CoverEntity):
    """A motorized shutter/awning driven purely by MQTT push state."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = None  # use the device name directly
    _attr_device_class = CoverDeviceClass.SHUTTER

    def __init__(self, device_id: str, has_tilt: bool) -> None:
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}_motor"
        self._attr_available = False
        self._attr_current_cover_position: int | None = None
        self._attr_current_cover_tilt_position: int | None = None
        self._attr_is_opening: bool | None = None
        self._attr_is_closing: bool | None = None

        features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )
        if has_tilt:
            features |= (
                CoverEntityFeature.OPEN_TILT
                | CoverEntityFeature.CLOSE_TILT
                | CoverEntityFeature.SET_TILT_POSITION
            )
        self._attr_supported_features = features
        self._has_tilt = has_tilt

        self._cmnd_topic = TOPIC_CMND.format(id=device_id)
        self._stat_topic = TOPIC_MOTOR_STAT.format(id=device_id)
        self._connect_topic = TOPIC_CONNECT.format(id=device_id)

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer=MANUFACTURER,
            model=model_from_device_id(device_id),
            name=device_id,
        )

    async def async_added_to_hass(self) -> None:
        @callback
        def stat_received(msg) -> None:
            stat = parse_motor_stat(msg.payload)
            if stat.position is not None:
                self._attr_current_cover_position = int(stat.position)
            is_opening, is_closing = _infer_moving_direction(stat.motor_status)
            self._attr_is_opening = is_opening
            self._attr_is_closing = is_closing
            self.async_write_ha_state()

        @callback
        def connect_received(msg) -> None:
            self._attr_available = msg.payload.strip().lower() == "true"
            self.async_write_ha_state()

        self.async_on_remove(
            await mqtt.async_subscribe(self.hass, self._stat_topic, stat_received)
        )
        self.async_on_remove(
            await mqtt.async_subscribe(self.hass, self._connect_topic, connect_received)
        )

        # Ask the device to (re-)publish its current position/status now,
        # rather than waiting for the next spontaneous /stat push -- the
        # vendor guide documents this exact command for that purpose
        # ("motor=STATUS -> Stato runtime pubblicato su /stat").
        await self._publish(MOTOR_CMD_STATUS)

    @property
    def is_closed(self) -> bool | None:
        if self._attr_current_cover_position is None:
            return None
        return self._attr_current_cover_position <= 0

    async def async_open_cover(self, **kwargs) -> None:
        await self._publish(MOTOR_CMD_UP)

    async def async_close_cover(self, **kwargs) -> None:
        await self._publish(MOTOR_CMD_DOWN)

    async def async_stop_cover(self, **kwargs) -> None:
        await self._publish(MOTOR_CMD_STOP)

    async def async_set_cover_position(self, **kwargs) -> None:
        position = kwargs[ATTR_POSITION]
        await self._publish(motor_move_payload(position))

    async def async_open_cover_tilt(self, **kwargs) -> None:
        await self._publish(motor_tilt_payload(100))

    async def async_close_cover_tilt(self, **kwargs) -> None:
        await self._publish(motor_tilt_payload(0))

    async def async_set_cover_tilt_position(self, **kwargs) -> None:
        position = kwargs[ATTR_TILT_POSITION]
        await self._publish(motor_tilt_payload(position))

    async def _publish(self, payload: str) -> None:
        await mqtt.async_publish(self.hass, self._cmnd_topic, payload)
