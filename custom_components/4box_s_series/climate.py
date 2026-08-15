"""Climate platform for the 4box S Series (MQTT) integration.

Covers the Morpheos thermostat family: mode (`thermostatMode`), setpoint
(`tS`), and ambient temperature/humidity (`sht4x`). Unlike relay/motor
devices, none of these are pushed spontaneously by the firmware -- they're
all request/response over the shared `/cmnd`-`/info` pair, so this
platform is backed by `SSeriesThermostatCoordinator` (periodic polling)
rather than a live MQTT subscription.

Setting the setpoint by temperature uses the "short form" (`tS=20.5`),
the form the guide recommends for forcing a manual value. The guide also
documents recalling a pre-configured setpoint *profile* by numeric id
(`tS=901`) or by mode+name (`tS=1;Eco`) -- fully valid commands, but this
integration has no way to discover which profiles actually exist on a
given device (that's set up through the vendor's own app, invisible over
MQTT). So instead of guessing, the user can declare their own known
profiles as an option (CONF_THERMOSTAT_PROFILES), which then show up as
selectable Home Assistant climate presets.
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

from .const import (
    DOMAIN,
    MANUFACTURER,
    setpoint_payload,
    thermostat_mode_payload,
    thermostat_profile_payload,
)
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
    profiles: list[tuple[str, str]] = entry_data.get("thermostat_profiles", [])

    async_add_entities([SSeriesThermostat(coordinator, device_id, profiles)])


class SSeriesThermostat(CoordinatorEntity[SSeriesThermostatCoordinator], ClimateEntity):
    """A Morpheos thermostat, polled periodically for mode/setpoint/sensors."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = (HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL)
    _attr_target_temperature_step = 0.5

    def __init__(
        self,
        coordinator: SSeriesThermostatCoordinator,
        device_id: str,
        profiles: list[tuple[str, str]],
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}_thermostat"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer=MANUFACTURER,
            model=model_from_device_id(device_id),
            name=device_id,
        )

        # name -> tS-value lookup for recalling a user-declared profile.
        self._profiles: dict[str, str] = dict(profiles)
        # Optimistic only: the device doesn't report which profile (if
        # any) is currently active, only the resulting numeric setpoint.
        # This reflects the last profile *this integration* selected, and
        # is cleared whenever the setpoint is changed some other way
        # (manual temperature set, or a poll returning a setpoint that
        # doesn't match what the selected profile should have produced).
        self._optimistic_preset: str | None = None

        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if self._profiles:
            features |= ClimateEntityFeature.PRESET_MODE
            self._attr_preset_modes = list(self._profiles.keys())
        self._attr_supported_features = features

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

    @property
    def preset_mode(self) -> str | None:
        """Best-effort only -- see `_optimistic_preset`'s docstring."""
        return self._optimistic_preset

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
        self._optimistic_preset = None
        await self.coordinator.async_send_command(setpoint_payload(temperature))
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        profile_value = self._profiles.get(preset_mode)
        if profile_value is None:
            _LOGGER.warning(
                "Unknown preset %r for %s; configured presets are: %s",
                preset_mode,
                self._device_id,
                list(self._profiles.keys()),
            )
            return
        self._optimistic_preset = preset_mode
        self.async_write_ha_state()
        await self.coordinator.async_send_command(
            thermostat_profile_payload(profile_value)
        )
        await self.coordinator.async_request_refresh()
