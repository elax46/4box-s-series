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
    TOPIC_PUSH_RELAY_STATE,
    TOPIC_RELAY_OVERCURRENT,
)
from .utils import model_from_device_id


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up binary sensor entities for one device.

    Shared by two device families: relay devices (overcurrent/overtemperature
    diagnostics) and Uniko Push devices (a read-only "active" indicator for
    the pulsed relay -- there's nothing to command here, `button.py` handles
    triggering the pulse).
    """
    entry_data = hass.data[DOMAIN][entry.entry_id]
    device_id: str = entry_data["device_id"]
    device_type: str = entry_data["device_type"]

    if device_type == "push":
        async_add_entities([SSeriesPushActiveSensor(device_id)])
        return

    channels: int = entry_data["channels"]

    entities: list[BinarySensorEntity] = [
        SSeriesOvercurrentSensor(device_id, channel)
        for channel in range(1, channels + 1)
    ]
    entities.append(SSeriesOvertemperatureSensor(device_id))

    async_add_entities(entities)


class _SSeriesBinarySensorBase(BinarySensorEntity):
    """Common bits for binary sensors fed by a single `/stat/...` topic."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    # Payload value (lowercased) that means "on". Override per subclass:
    # fault flags use "true"/"false", relay state topics use "on"/"off".
    _on_payload = "true"

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
            self._attr_is_on = msg.payload.strip().lower() == self._on_payload
            self.async_write_ha_state()

        self.async_on_remove(
            await mqtt.async_subscribe(self.hass, self._topic, handler)
        )


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


class SSeriesPushActiveSensor(_SSeriesBinarySensorBase):
    """Read-only relay state for a Uniko Push device.

    Reflects the relay while a pulse triggered via `button.py` is active;
    the firmware turns it back off on its own once the pulse duration
    elapses.
    """

    _attr_entity_category = None  # this is the primary state, not diagnostic
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _on_payload = "on"

    def __init__(self, device_id: str) -> None:
        super().__init__(device_id)
        self._attr_unique_id = f"{device_id}_push_active"
        self._attr_name = "Active"
        self._topic = TOPIC_PUSH_RELAY_STATE.format(id=device_id, channel=1)
