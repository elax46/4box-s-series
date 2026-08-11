"""Binary sensor platform for the 4box S Series (MQTT) integration."""

from __future__ import annotations

from homeassistant.components import mqtt
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    MANUFACTURER,
    TOPIC_OVERTEMPERATURE,
    TOPIC_RELAY_OVERCURRENT,
)
from .utils import model_from_device_id


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up binary sensor entities for one device."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    device_id: str = entry_data["device_id"]
    channels: int = entry_data["channels"]

    entities: list[BinarySensorEntity] = [
        SSeriesOvercurrentSensor(device_id, channel) for channel in range(1, channels + 1)
    ]
    entities.append(SSeriesOvertemperatureSensor(device_id))

    async_add_entities(entities)


class _SSeriesBinarySensorBase(BinarySensorEntity):
    """Common bits for diagnostic problem sensors."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, device_id: str) -> None:
        self._device_id = device_id
        self._topic: str = ""
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer=MANUFACTURER,
            model=model_from_device_id(device_id),
            name=device_id,
        )

    async def async_added_to_hass(self) -> None:
        @callback
        def handler(msg) -> None:
            self._attr_is_on = msg.payload.strip().lower() == "true"
            self.async_write_ha_state()

        self.async_on_remove(await mqtt.async_subscribe(self.hass, self._topic, handler))


class SSeriesOvercurrentSensor(_SSeriesBinarySensorBase):
    """Overcurrent flag for one relay channel."""

    def __init__(self, device_id: str, channel: int) -> None:
        super().__init__(device_id)
        self._attr_unique_id = f"{device_id}_relay_{channel}_overcurrent"
        self._attr_name = (
            "Overcurrent" if channel == 1 else f"Overcurrent channel {channel}"
        )
        self._topic = TOPIC_RELAY_OVERCURRENT.format(id=device_id, channel=channel)


class SSeriesOvertemperatureSensor(_SSeriesBinarySensorBase):
    """Device-wide overtemperature flag."""

    def __init__(self, device_id: str) -> None:
        super().__init__(device_id)
        self._attr_unique_id = f"{device_id}_overtemperature"
        self._attr_name = "Overtemperature"
        self._topic = TOPIC_OVERTEMPERATURE.format(id=device_id)
