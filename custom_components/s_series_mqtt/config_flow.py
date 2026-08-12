"""Config flow for the 4box S Series (MQTT) integration.

Two-step flow: first pick the device family (relay / motor / push /
thermostat) and enter the device ID, then a second, family-specific step
collects the remaining options.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    CONF_CHANNELS,
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_ENERGY_POLL_INTERVAL,
    CONF_HAS_ENERGY,
    CONF_HAS_TILT,
    CONF_NAME,
    CONF_PULSE_DURATION_MS,
    CONF_THERMOSTAT_POLL_INTERVAL,
    DEFAULT_CHANNELS,
    DEFAULT_ENERGY_POLL_INTERVAL,
    DEFAULT_PULSE_DURATION_MS,
    DEFAULT_THERMOSTAT_POLL_INTERVAL,
    DEVICE_TYPE_MOTOR,
    DEVICE_TYPE_PUSH,
    DEVICE_TYPE_RELAY,
    DEVICE_TYPE_THERMOSTAT,
    DEVICE_TYPES,
    DOMAIN,
)


class SSeriesMqttConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the 4box S Series (MQTT) integration."""

    VERSION = 1

    def __init__(self) -> None:
        self._base_data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 1: device family, device ID, friendly name."""
        errors: dict[str, str] = {}

        if user_input is not None:
            device_id = user_input[CONF_DEVICE_ID].strip()

            if not device_id or " " in device_id or "/" in device_id:
                errors["base"] = "invalid_device_id"
            else:
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured()

                self._base_data = {
                    CONF_DEVICE_ID: device_id,
                    CONF_NAME: user_input.get(CONF_NAME) or device_id,
                    CONF_DEVICE_TYPE: user_input[CONF_DEVICE_TYPE],
                }

                next_step = {
                    DEVICE_TYPE_RELAY: self.async_step_relay,
                    DEVICE_TYPE_MOTOR: self.async_step_motor,
                    DEVICE_TYPE_PUSH: self.async_step_push,
                    DEVICE_TYPE_THERMOSTAT: self.async_step_thermostat,
                }[user_input[CONF_DEVICE_TYPE]]
                return await next_step()

        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_TYPE, default=DEVICE_TYPE_RELAY): vol.In(
                    DEVICE_TYPES
                ),
                vol.Required(CONF_DEVICE_ID): str,
                vol.Optional(CONF_NAME, default=""): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_relay(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 2 (relay): channel count, energy metering."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._base_data[CONF_NAME], data={**self._base_data, **user_input}
            )

        schema = vol.Schema(
            {
                vol.Optional(CONF_CHANNELS, default=DEFAULT_CHANNELS): vol.In([1, 2]),
                vol.Optional(CONF_HAS_ENERGY, default=True): bool,
                vol.Optional(
                    CONF_ENERGY_POLL_INTERVAL, default=DEFAULT_ENERGY_POLL_INTERVAL
                ): vol.All(int, vol.Range(min=30)),
            }
        )
        return self.async_show_form(step_id="relay", data_schema=schema)

    async def async_step_motor(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 2 (motor): tilt support."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._base_data[CONF_NAME], data={**self._base_data, **user_input}
            )

        schema = vol.Schema({vol.Optional(CONF_HAS_TILT, default=True): bool})
        return self.async_show_form(step_id="motor", data_schema=schema)

    async def async_step_push(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 2 (push): default pulse duration."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._base_data[CONF_NAME], data={**self._base_data, **user_input}
            )

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_PULSE_DURATION_MS, default=DEFAULT_PULSE_DURATION_MS
                ): vol.All(int, vol.Range(min=100, max=60000))
            }
        )
        return self.async_show_form(step_id="push", data_schema=schema)

    async def async_step_thermostat(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 2 (thermostat): poll interval."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._base_data[CONF_NAME], data={**self._base_data, **user_input}
            )

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_THERMOSTAT_POLL_INTERVAL,
                    default=DEFAULT_THERMOSTAT_POLL_INTERVAL,
                ): vol.All(int, vol.Range(min=30))
            }
        )
        return self.async_show_form(step_id="thermostat", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> SSeriesMqttOptionsFlow:
        """Get the options flow for this handler."""
        return SSeriesMqttOptionsFlow(config_entry)


class SSeriesMqttOptionsFlow(config_entries.OptionsFlow):
    """Options flow: adjust the poll interval / pulse duration after setup."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}
        device_type = current.get(CONF_DEVICE_TYPE, DEVICE_TYPE_RELAY)

        if device_type == DEVICE_TYPE_RELAY:
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
        elif device_type == DEVICE_TYPE_PUSH:
            schema = vol.Schema(
                {
                    vol.Optional(
                        CONF_PULSE_DURATION_MS,
                        default=current.get(
                            CONF_PULSE_DURATION_MS, DEFAULT_PULSE_DURATION_MS
                        ),
                    ): vol.All(int, vol.Range(min=100, max=60000)),
                }
            )
        elif device_type == DEVICE_TYPE_THERMOSTAT:
            schema = vol.Schema(
                {
                    vol.Optional(
                        CONF_THERMOSTAT_POLL_INTERVAL,
                        default=current.get(
                            CONF_THERMOSTAT_POLL_INTERVAL,
                            DEFAULT_THERMOSTAT_POLL_INTERVAL,
                        ),
                    ): vol.All(int, vol.Range(min=30)),
                }
            )
        else:  # motor: nothing to reconfigure post-setup today
            return self.async_create_entry(title="", data={})

        return self.async_show_form(step_id="init", data_schema=schema)
