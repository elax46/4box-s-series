"""The 4box S Series (MQTT) integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CHANNELS,
    CONF_DEVICE_ID,
    CONF_ENERGY_POLL_INTERVAL,
    CONF_HAS_ENERGY,
    DEFAULT_CHANNELS,
    DEFAULT_ENERGY_POLL_INTERVAL,
    DOMAIN,
)
from .coordinator import SSeriesEnergyCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SWITCH, Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a P40S device from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    options = {**entry.data, **entry.options}
    device_id: str = options[CONF_DEVICE_ID]
    channels: int = options.get(CONF_CHANNELS, DEFAULT_CHANNELS)
    has_energy: bool = options.get(CONF_HAS_ENERGY, True)
    poll_interval: int = options.get(
        CONF_ENERGY_POLL_INTERVAL, DEFAULT_ENERGY_POLL_INTERVAL
    )

    entry_data: dict = {"device_id": device_id, "channels": channels}

    if has_energy:
        coordinator = SSeriesEnergyCoordinator(hass, device_id, channels, poll_interval)
        await coordinator.async_start()
        # Raises ConfigEntryNotReady automatically on failure.
        await coordinator.async_config_entry_first_refresh()
        entry_data["energy_coordinator"] = coordinator

    hass.data[DOMAIN][entry.entry_id] = entry_data

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change (e.g. poll interval)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        coordinator: SSeriesEnergyCoordinator | None = entry_data.get(
            "energy_coordinator"
        )
        if coordinator is not None:
            await coordinator.async_stop()
    return unload_ok
