"""Config flow for the 4box S Series (MQTT) integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    CONF_CHANNELS,
    CONF_DEVICE_ID,
    CONF_ENERGY_POLL_INTERVAL,
    CONF_HAS_ENERGY,
    CONF_NAME,
    DEFAULT_CHANNELS,
    DEFAULT_ENERGY_POLL_INTERVAL,
    DOMAIN,
)


class P40SMqttConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the 4box S Series (MQTT) integration."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step: ask for the device ID and options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            device_id = user_input[CONF_DEVICE_ID].strip()

            if not device_id or " " in device_id or "/" in device_id:
                errors["base"] = "invalid_device_id"
            else:
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured()
                user_input[CONF_DEVICE_ID] = device_id
                return self.async_create_entry(
                    title=user_input.get(CONF_NAME) or device_id,
                    data=user_input,
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_ID): str,
                vol.Optional(CONF_NAME, default=""): str,
                vol.Optional(CONF_CHANNELS, default=DEFAULT_CHANNELS): vol.In([1, 2]),
                vol.Optional(CONF_HAS_ENERGY, default=True): bool,
                vol.Optional(
                    CONF_ENERGY_POLL_INTERVAL, default=DEFAULT_ENERGY_POLL_INTERVAL
                ): vol.All(int, vol.Range(min=30)),
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> P40SMqttOptionsFlow:
        """Get the options flow for this handler."""
        return P40SMqttOptionsFlow(config_entry)


class P40SMqttOptionsFlow(config_entries.OptionsFlow):
    """Handle options (currently just the energy poll interval)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_ENERGY_POLL_INTERVAL,
                    default=current.get(
                        CONF_ENERGY_POLL_INTERVAL, DEFAULT_ENERGY_POLL_INTERVAL
                    ),
                ): vol.All(int, vol.Range(min=30)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
