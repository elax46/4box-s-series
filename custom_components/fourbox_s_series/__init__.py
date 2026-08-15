"""The 4box S Series (MQTT) integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CHANNELS,
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_ENERGY_POLL_INTERVAL,
    CONF_HAS_ENERGY,
    CONF_HAS_LED,
    CONF_HAS_TILT,
    CONF_PULSE_DURATION_MS,
    CONF_THERMOSTAT_POLL_INTERVAL,
    CONF_THERMOSTAT_PROFILES,
    DEFAULT_CHANNELS,
    DEFAULT_ENERGY_POLL_INTERVAL,
    DEFAULT_HAS_LED,
    DEFAULT_PULSE_DURATION_MS,
    DEFAULT_THERMOSTAT_POLL_INTERVAL,
    DEVICE_TYPE_MOTOR,
    DEVICE_TYPE_PUSH,
    DEVICE_TYPE_RELAY,
    DEVICE_TYPE_THERMOSTAT,
    DOMAIN,
)
from .coordinator import (
    SSeriesEnergyCoordinator,
    SSeriesRelayStateRefresher,
    SSeriesThermostatCoordinator,
)
from .utils import parse_thermostat_profiles

_LOGGER = logging.getLogger(__name__)

# Which Home Assistant platforms each device family forwards to.
PLATFORMS_BY_DEVICE_TYPE: dict[str, list[Platform]] = {
    DEVICE_TYPE_RELAY: [Platform.SWITCH, Platform.SENSOR, Platform.BINARY_SENSOR],
    DEVICE_TYPE_MOTOR: [Platform.COVER, Platform.BUTTON, Platform.SENSOR],
    DEVICE_TYPE_PUSH: [Platform.BUTTON, Platform.BINARY_SENSOR],
    DEVICE_TYPE_THERMOSTAT: [Platform.CLIMATE],
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one device from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    options = {**entry.data, **entry.options}
    device_id: str = options[CONF_DEVICE_ID]
    device_type: str = options.get(CONF_DEVICE_TYPE, DEVICE_TYPE_RELAY)

    entry_data: dict = {"device_id": device_id, "device_type": device_type}

    if device_type == DEVICE_TYPE_RELAY:
        entry_data["channels"] = options.get(CONF_CHANNELS, DEFAULT_CHANNELS)
        entry_data["has_led"] = options.get(CONF_HAS_LED, DEFAULT_HAS_LED)

        # Keeps switch state correct by re-querying gpiostatus=GET every
        # time the device announces itself online via its retained
        # <ID>/connect birth message -- including immediately at setup
        # (the retained message fires right away) AND on every future
        # reconnect, not just once with a fixed timeout window. See
        # SSeriesRelayStateRefresher's docstring for why this replaced an
        # earlier one-shot-fetch-at-setup design.
        refresher = SSeriesRelayStateRefresher(hass, device_id, entry_data["channels"])
        await refresher.async_start()
        entry_data["relay_state_refresher"] = refresher

        if options.get(CONF_HAS_ENERGY, True):
            poll_interval = options.get(
                CONF_ENERGY_POLL_INTERVAL, DEFAULT_ENERGY_POLL_INTERVAL
            )
            coordinator = SSeriesEnergyCoordinator(
                hass, device_id, entry_data["channels"], poll_interval
            )
            await coordinator.async_start()
            # Use async_refresh(), NOT async_config_entry_first_refresh():
            # the latter raises ConfigEntryNotReady on failure, which
            # would abort setup of the ENTIRE device (switch included)
            # just because the energy counter's one-shot query timed
            # out. Energy is a supplementary sensor, not something the
            # rest of the entry depends on -- a failed first poll should
            # leave the energy sensor "unknown" and retry on the next
            # scheduled interval, not block everything else.
            await coordinator.async_refresh()
            entry_data["energy_coordinator"] = coordinator

    elif device_type == DEVICE_TYPE_MOTOR:
        entry_data["has_tilt"] = options.get(CONF_HAS_TILT, True)

    elif device_type == DEVICE_TYPE_PUSH:
        entry_data["pulse_duration_ms"] = options.get(
            CONF_PULSE_DURATION_MS, DEFAULT_PULSE_DURATION_MS
        )

    elif device_type == DEVICE_TYPE_THERMOSTAT:
        poll_interval = options.get(
            CONF_THERMOSTAT_POLL_INTERVAL, DEFAULT_THERMOSTAT_POLL_INTERVAL
        )
        coordinator = SSeriesThermostatCoordinator(hass, device_id, poll_interval)
        await coordinator.async_start()
        # Same reasoning as the energy coordinator above: don't let a
        # slow/failed first poll abort setup of the whole entry. For the
        # thermostat this matters even more, since ALL of its state is
        # polled -- a temporary MQTT hiccup on first setup shouldn't make
        # the entire climate entity unavailable-and-retrying.
        await coordinator.async_refresh()
        entry_data["thermostat_coordinator"] = coordinator
        entry_data["thermostat_profiles"] = parse_thermostat_profiles(
            options.get(CONF_THERMOSTAT_PROFILES, "")
        )

    hass.data[DOMAIN][entry.entry_id] = entry_data

    platforms = PLATFORMS_BY_DEVICE_TYPE[device_type]
    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change (e.g. poll interval)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    options = {**entry.data, **entry.options}
    device_type: str = options.get(CONF_DEVICE_TYPE, DEVICE_TYPE_RELAY)
    platforms = PLATFORMS_BY_DEVICE_TYPE[device_type]

    unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unload_ok:
        # `.pop(..., None)`, not `.pop(...)`: if a previous setup attempt
        # failed before reaching `hass.data[DOMAIN][entry.entry_id] = ...`
        # (e.g. it raised before that point), there's nothing to pop --
        # and popping unconditionally would raise KeyError here, which
        # surfaces to the user as an unhandled-exception 500 error the
        # next time they touch this entry (reload, Configure, etc.).
        entry_data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if entry_data is not None:
            refresher = entry_data.get("relay_state_refresher")
            if refresher is not None:
                await refresher.async_stop()
            for key in ("energy_coordinator", "thermostat_coordinator"):
                coordinator = entry_data.get(key)
                if coordinator is not None:
                    await coordinator.async_stop()
    return unload_ok
