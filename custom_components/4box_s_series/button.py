"""Button platform for the 4box S Series (MQTT) integration.

Two unrelated button entities live here because both are simple
"publish one command, no state to track" actions:

- Motor devices: a "Calibrate" button (`motor=CALIBRATION`), needed once
  before percentage-based positioning works, per the vendor guide.
- Push (Uniko Push) devices: a "Pulse" button that triggers a timed relay
  pulse (`pulsetime=PULSETIME<ron>:<ms>&&RELAY1:ON`) -- the primary way
  to operate a gate opener, electric lock, doorbell, etc. configured as
  Uniko Push.
"""

from __future__ import annotations

import logging

from homeassistant.components import mqtt
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    MANUFACTURER,
    MOTOR_CMD_CALIBRATION,
    TOPIC_CMND,
    pulse_payload,
)
from .utils import model_from_device_id

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up button entities for one device."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    device_id: str = entry_data["device_id"]
    device_type: str = entry_data["device_type"]

    if device_type == "motor":
        async_add_entities([SSeriesCalibrateButton(device_id)])
    elif device_type == "push":
        duration_ms: int = entry_data.get("pulse_duration_ms", 1000)
        async_add_entities([SSeriesPulseButton(device_id, duration_ms)])


class _SSeriesButtonBase(ButtonEntity):
    """Common bits for the buttons in this platform."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, device_id: str) -> None:
        self._device_id = device_id
        self._cmnd_topic = TOPIC_CMND.format(id=device_id)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer=MANUFACTURER,
            model=model_from_device_id(device_id),
            name=device_id,
        )


class SSeriesCalibrateButton(_SSeriesButtonBase):
    """Triggers `motor=CALIBRATION` on a motor device."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, device_id: str) -> None:
        super().__init__(device_id)
        self._attr_unique_id = f"{device_id}_calibrate"
        self._attr_name = "Calibrate"

    async def async_press(self) -> None:
        await mqtt.async_publish(self.hass, self._cmnd_topic, MOTOR_CMD_CALIBRATION)


class SSeriesPulseButton(_SSeriesButtonBase):
    """Triggers a timed relay pulse on a Uniko Push device."""

    def __init__(self, device_id: str, duration_ms: int) -> None:
        super().__init__(device_id)
        self._duration_ms = duration_ms
        self._attr_unique_id = f"{device_id}_pulse"
        self._attr_name = "Pulse"

    async def async_press(self) -> None:
        payload = pulse_payload(self._duration_ms)
        await mqtt.async_publish(self.hass, self._cmnd_topic, payload)
