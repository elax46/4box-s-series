"""Sensor platform for the 4box S Series (MQTT) integration."""

from __future__ import annotations

import logging
from typing import ClassVar

from homeassistant.components import mqtt
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    MANUFACTURER,
    TOPIC_LED_B,
    TOPIC_LED_G,
    TOPIC_LED_R,
    TOPIC_MOTOR_STAT,
    TOPIC_RELAY_CURRENT,
    TOPIC_RELAY_POWER,
    TOPIC_TEMPERATURE,
    TOPIC_VOLTAGE,
)
from .coordinator import SSeriesEnergyCoordinator
from .utils import model_from_device_id, parse_motor_stat

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up sensor entities for one device.

    This platform is shared by two device families: relay devices (power,
    current, voltage, temperature, energy) and motor devices (a single
    instantaneous power sensor derived from the combined `/stat` message).
    """
    entry_data = hass.data[DOMAIN][entry.entry_id]
    device_id: str = entry_data["device_id"]
    device_type: str = entry_data["device_type"]

    if device_type == "motor":
        async_add_entities([SSeriesMotorPowerSensor(device_id)])
        return

    channels: int = entry_data["channels"]
    coordinator: SSeriesEnergyCoordinator | None = entry_data.get("energy_coordinator")

    entities: list[SensorEntity] = []
    for channel in range(1, channels + 1):
        entities.append(SSeriesPowerSensor(device_id, channel, coordinator))
        entities.append(SSeriesCurrentSensor(device_id, channel, coordinator))
        if coordinator is not None:
            entities.append(SSeriesEnergySensor(coordinator, device_id, channel))

    entities.append(SSeriesVoltageSensor(device_id))
    entities.append(SSeriesTemperatureSensor(device_id))

    if entry_data.get("has_led"):
        entities.append(SSeriesLedChannelSensor(device_id, "r", "Red"))
        entities.append(SSeriesLedChannelSensor(device_id, "g", "Green"))
        entities.append(SSeriesLedChannelSensor(device_id, "b", "Blue"))

    async_add_entities(entities)


class _SSeriesPushSensorBase(SensorEntity):
    """Common bits for sensors fed by spontaneous `/stat/...` messages."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, device_id: str) -> None:
        self._device_id = device_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer=MANUFACTURER,
            model=model_from_device_id(device_id),
            name=device_id,
        )
        self._topic: str = ""

    async def _subscribe(self) -> None:
        @callback
        def handler(msg) -> None:
            try:
                self._attr_native_value = float(msg.payload)
            except ValueError:
                _LOGGER.debug(
                    "Ignoring non-numeric payload on %s: %r", self._topic, msg.payload
                )
                return
            self.async_write_ha_state()

        self.async_on_remove(
            await mqtt.async_subscribe(self.hass, self._topic, handler)
        )

    async def async_added_to_hass(self) -> None:
        await self._subscribe()


class SSeriesPowerSensor(SensorEntity):
    """Instantaneous active power for one relay channel.

    Combines two update sources for freshness: the firmware's own
    spontaneous MQTT push (`/stat/relay/<n>/power/w`) plus periodic
    active polling via `power=RELAY<n>` (piggybacked on
    `SSeriesEnergyCoordinator`'s same poll cycle as the energy counter).
    Push updates alone could go quiet for a long time in practice; the
    poll acts as a floor so the value is never stuck at "unknown" for
    longer than one poll interval, while still taking whichever of the
    two arrives most recently.

    `coordinator` is `None` on devices without energy metering enabled
    (`has_energy` off) -- in that case this falls back to push-only,
    exactly like before.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _metric_key = "power"

    def __init__(
        self,
        device_id: str,
        channel: int,
        coordinator: SSeriesEnergyCoordinator | None,
    ) -> None:
        self._device_id = device_id
        self._channel = channel
        self._coordinator = coordinator
        self._attr_unique_id = f"{device_id}_relay_{channel}_power"
        self._attr_name = "Power" if channel == 1 else f"Power channel {channel}"
        self._topic = TOPIC_RELAY_POWER.format(id=device_id, channel=channel)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer=MANUFACTURER,
            model=model_from_device_id(device_id),
            name=device_id,
        )

    async def async_added_to_hass(self) -> None:
        @callback
        def push_handler(msg) -> None:
            try:
                self._attr_native_value = float(msg.payload)
            except ValueError:
                _LOGGER.debug(
                    "Ignoring non-numeric payload on %s: %r", self._topic, msg.payload
                )
                return
            self.async_write_ha_state()

        self.async_on_remove(
            await mqtt.async_subscribe(self.hass, self._topic, push_handler)
        )

        if self._coordinator is not None:

            @callback
            def coordinator_updated() -> None:
                channel_data = (self._coordinator.data or {}).get(self._channel, {})
                if self._metric_key in channel_data:
                    self._attr_native_value = channel_data[self._metric_key]
                    self.async_write_ha_state()

            self.async_on_remove(
                self._coordinator.async_add_listener(coordinator_updated)
            )
            # Seed immediately from whatever the coordinator already has
            # (e.g. after a reload, where a poll may have already run
            # before this entity was even added).
            coordinator_updated()


class SSeriesCurrentSensor(SSeriesPowerSensor):
    """Instantaneous current for one relay channel. See SSeriesPowerSensor
    for the combined push+poll update strategy this shares."""

    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _metric_key = "current"

    def __init__(
        self,
        device_id: str,
        channel: int,
        coordinator: SSeriesEnergyCoordinator | None,
    ) -> None:
        super().__init__(device_id, channel, coordinator)
        self._attr_unique_id = f"{device_id}_relay_{channel}_current"
        self._attr_name = "Current" if channel == 1 else f"Current channel {channel}"
        self._topic = TOPIC_RELAY_CURRENT.format(id=device_id, channel=channel)


class SSeriesVoltageSensor(_SSeriesPushSensorBase):
    """Mains voltage, published roughly every 15 minutes by the firmware."""

    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, device_id: str) -> None:
        super().__init__(device_id)
        self._attr_unique_id = f"{device_id}_voltage"
        self._attr_name = "Voltage"
        self._topic = TOPIC_VOLTAGE.format(id=device_id)


class SSeriesTemperatureSensor(_SSeriesPushSensorBase):
    """Internal temperature, published roughly every 15 minutes."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, device_id: str) -> None:
        super().__init__(device_id)
        self._attr_unique_id = f"{device_id}_temperature"
        self._attr_name = "Temperature"
        self._topic = TOPIC_TEMPERATURE.format(id=device_id)


class SSeriesEnergySensor(CoordinatorEntity[SSeriesEnergyCoordinator], SensorEntity):
    """Cumulative active energy, polled via `energyActive=RELAY<n>`.

    Compatible with the Home Assistant Energy dashboard
    (device_class=energy, state_class=total_increasing).
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self, coordinator: SSeriesEnergyCoordinator, device_id: str, channel: int
    ) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._attr_unique_id = f"{device_id}_relay_{channel}_energy"
        self._attr_name = "Energy" if channel == 1 else f"Energy channel {channel}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer=MANUFACTURER,
            model=model_from_device_id(device_id),
            name=device_id,
        )

    @property
    def native_value(self) -> float | None:
        """Return the last polled energy value for this channel."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._channel, {}).get("energy")


class SSeriesMotorPowerSensor(SensorEntity):
    """Instantaneous motor power, parsed from the combined `/stat` message.

    Motor devices publish a single concatenated status string on `/stat`
    (see `utils.parse_motor_stat`); this entity subscribes to it and
    extracts just the power field.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, device_id: str) -> None:
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}_motor_power"
        self._attr_name = "Power"
        self._topic = TOPIC_MOTOR_STAT.format(id=device_id)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer=MANUFACTURER,
            model=model_from_device_id(device_id),
            name=device_id,
        )

    async def async_added_to_hass(self) -> None:
        @callback
        def handler(msg) -> None:
            stat = parse_motor_stat(msg.payload)
            if stat.power_w is not None:
                self._attr_native_value = stat.power_w
                self.async_write_ha_state()

        self.async_on_remove(
            await mqtt.async_subscribe(self.hass, self._topic, handler)
        )


class SSeriesLedChannelSensor(SensorEntity):
    """One channel (R, G, or B) of the device's indicator LED, read-only.

    This is a reverse-engineered, undocumented feature (see const.py's
    CONF_HAS_LED docstring): the vendor guide never mentions an LED, and
    no write command has been found to control it -- only observed
    values via `gpiostatus=GET`'s LED1_R/G/B fields and the spontaneous
    `/stat/led/1/{r,g,b}` pushes this class subscribes to. Because there's
    no way to control it, this is a plain diagnostic sensor rather than a
    `light` entity.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = None
    _attr_icon = "mdi:palette"

    _TOPIC_BY_CHANNEL: ClassVar[dict[str, str]] = {
        "r": TOPIC_LED_R,
        "g": TOPIC_LED_G,
        "b": TOPIC_LED_B,
    }

    def __init__(self, device_id: str, channel: str, label: str) -> None:
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}_led_1_{channel}"
        self._attr_name = f"LED {label}"
        self._topic = self._TOPIC_BY_CHANNEL[channel].format(id=device_id)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer=MANUFACTURER,
            model=model_from_device_id(device_id),
            name=device_id,
        )

    async def async_added_to_hass(self) -> None:
        @callback
        def handler(msg) -> None:
            try:
                self._attr_native_value = int(msg.payload)
            except ValueError:
                return
            self.async_write_ha_state()

        self.async_on_remove(
            await mqtt.async_subscribe(self.hass, self._topic, handler)
        )
