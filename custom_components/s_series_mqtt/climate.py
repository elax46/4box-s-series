"""Climate platform for the 4box S Series (MQTT) integration.

Covers the Morpheos thermostat family: mode (`thermostatMode`), setpoint
(`tS`), and ambient temperature/humidity (`sht4x`). Unlike relay/motor
devices, none of these are pushed spontaneously by the firmware -- they're
all request/response over the shared `/cmnd`-`/info` pair, so this
platform is backed by `SSeriesThermostatCoordinator` (periodic polling)
rather than a live MQTT subscription. Setting the setpoint uses the
"short form" (`tS=20.5`) documented as the quickest way to force a manual
setpoint; the guide's profile-recall forms (`tS=901`, `tS=1;Eco`) aren't
exposed here since they depend on per-device configuration this
integration has no way to introspect -- see the README roadmap.
"""

from __future__ import annotations

import logging

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, setpoint_payload, thermostat_mode_payload
from .coordinator import SSeriesThermostatCoordinator
from .utils import model_from_device_id

_LOGGER = logging.getLogger(__name__)

_MODE_TO_HVAC = {0: HVACMode.OFF, 1: HVACMode.HEAT, 2: HVACMode.COOL}
_HVAC_TO_MODE = {v: k for k, v in _MODE_TO_HVAC.items()}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the climate entity for one thermostat device."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    device_id: str = entry_data["device_id"]
    coordinator: SSeriesThermostatCoordinator = entry_data["thermostat_coordinator"]

    async_add_entities([SSeriesThermostat(coordinator, device_id)])


class SSeriesThermostat(CoordinatorEntity[SSeriesThermostatCoordinator], ClimateEntity):
    """A Morpheos thermostat, polled periodically for mode/setpoint/sensors."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_target_temperature_step = 0.5

    def __init__(self, coordinator: SSeriesThermostatCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}_thermostat"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer=MANUFACTURER,
            model=model_from_device_id(device_id),
            name=device_id,
        )

    @property
    def hvac_mode(self) -> HVACMode | None:
        if not self.coordinator.data:
            return None
        mode = self.coordinator.data.get("mode")
        return _MODE_TO_HVAC.get(mode) if mode is not None else None

    @property
    def current_temperature(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("current_temperature")

    @property
    def current_humidity(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("current_humidity")

    @property
    def target_temperature(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("setpoint")

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        mode = _HVAC_TO_MODE.get(hvac_mode)
        if mode is None:
            _LOGGER.warning("Unsupported HVAC mode requested: %s", hvac_mode)
            return
        await self.coordinator.async_send_command(thermostat_mode_payload(mode))
        await self.coordinator.async_request_refresh()

    async def async_set_temperature(self, **kwargs) -> None:
        temperature = kwargs.get("temperature")
        if temperature is None:
            return
        await self.coordinator.async_send_command(setpoint_payload(temperature))
        await self.coordinator.async_request_refresh()
