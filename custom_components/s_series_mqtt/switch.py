"""Switch platform for the 4box S Series (MQTT) integration."""

from __future__ import annotations

import logging

from homeassistant.components import mqtt
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MANUFACTURER, TOPIC_CMND, TOPIC_CONNECT, TOPIC_RELAY_STATE
from .utils import build_action_payload, model_from_device_id

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up switch entities for one device."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    device_id: str = entry_data["device_id"]
    channels: int = entry_data["channels"]

    entities = [
        SSeriesRelaySwitch(device_id, channels, channel)
        for channel in range(1, channels + 1)
    ]
    async_add_entities(entities)


class SSeriesRelaySwitch(SwitchEntity):
    """A single relay channel, driven purely by MQTT push state."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, device_id: str, total_channels: int, channel: int) -> None:
        self._device_id = device_id
        self._total_channels = total_channels
        self._channel = channel

        self._attr_unique_id = f"{device_id}_relay_{channel}"
        self._attr_name = "Socket" if total_channels == 1 else f"Channel {channel}"
        # Unknown until the first /stat or /connect message arrives.
        self._attr_is_on = False
        self._attr_available = False

        self._cmnd_topic = TOPIC_CMND.format(id=device_id)
        self._state_topic = TOPIC_RELAY_STATE.format(id=device_id, channel=channel)
        self._connect_topic = TOPIC_CONNECT.format(id=device_id)

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer=MANUFACTURER,
            model=model_from_device_id(device_id),
            name=device_id,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to relay state and device availability topics."""

        @callback
        def state_received(msg) -> None:
            self._attr_is_on = msg.payload.strip().lower() == "on"
            self.async_write_ha_state()

        @callback
        def connect_received(msg) -> None:
            self._attr_available = msg.payload.strip().lower() == "true"
            self.async_write_ha_state()

        self.async_on_remove(
            await mqtt.async_subscribe(self.hass, self._state_topic, state_received)
        )
        self.async_on_remove(
            await mqtt.async_subscribe(
                self.hass, self._connect_topic, connect_received
            )
        )

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the relay on."""
        await self._async_send("ON")

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the relay off."""
        await self._async_send("OFF")

    async def async_toggle(self, **kwargs) -> None:
        """Toggle the relay."""
        await self._async_send("TOGGLE")

    async def _async_send(self, action: str) -> None:
        payload = build_action_payload(action, self._channel, self._total_channels)
        await mqtt.async_publish(self.hass, self._cmnd_topic, payload)
