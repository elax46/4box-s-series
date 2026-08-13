"""Config flow for the 4box S Series (MQTT) integration.

Two-step flow: first pick the device family (relay / motor / push /
thermostat) and enter the device ID, then a second, family-specific step
collects the remaining options.

The device ID field is backed by a short MQTT scan (see
`_async_scan_for_devices`) that offers currently-online devices as
selectable suggestions, with manual entry always available too --
there's no vendor-documented discovery protocol to hook into, so this is
best-effort convenience, not authoritative discovery.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import mqtt
from homeassistant.core import callback
from homeassistant.helpers import selector

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
    DISCOVERY_SCAN_SECONDS,
    DOMAIN,
    TOPIC_CONNECT_WILDCARD,
)

_LOGGER = logging.getLogger(__name__)


class SSeriesMqttConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the 4box S Series (MQTT) integration."""

    VERSION = 1

    def __init__(self) -> None:
        self._base_data: dict[str, Any] = {}
        self._discovered_device_ids: list[str] | None = None

    async def _async_scan_for_devices(self) -> list[str]:
        """Passively collect device IDs currently online via their LWT.

        Subscribes to `+/connect` for a few seconds and records every
        device ID whose birth message ("true") arrives during that
        window, excluding devices already configured. Best-effort only:
        a device that's offline, or one whose retained "true" message
        doesn't get redelivered promptly, simply won't show up -- manual
        entry always remains available regardless.
        """
        found: set[str] = set()

        @callback
        def _handle_connect(msg) -> None:
            if msg.payload.strip().lower() == "true":
                device_id = msg.topic.rsplit("/connect", 1)[0]
                if device_id:
                    found.add(device_id)

        unsub = await mqtt.async_subscribe(
            self.hass, TOPIC_CONNECT_WILDCARD, _handle_connect
        )
        try:
            await asyncio.sleep(DISCOVERY_SCAN_SECONDS)
        finally:
            unsub()

        already_configured = self._async_current_ids(include_ignore=False)
        return sorted(d for d in found if d not in already_configured)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 1: device family, device ID, friendly name."""
        errors: dict[str, str] = {}

        # Only scan once per flow instance, on the very first display --
        # not on every re-render after a validation error.
        if self._discovered_device_ids is None:
            try:
                self._discovered_device_ids = await self._async_scan_for_devices()
            except Exception:  # noqa: BLE001 - discovery is best-effort
                _LOGGER.debug("MQTT device scan failed; falling back to manual entry")
                self._discovered_device_ids = []

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

        if self._discovered_device_ids:
            # Dropdown of devices seen online, but still freely editable
            # (custom_value=True) in case the one you want isn't listed.
            device_id_selector = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=self._discovered_device_ids,
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        else:
            device_id_selector = str

        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_TYPE, default=DEVICE_TYPE_RELAY): vol.In(
                    DEVICE_TYPES
                ),
                vol.Required(CONF_DEVICE_ID): device_id_selector,
                vol.Optional(CONF_NAME, default=""): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "discovered_count": str(len(self._discovered_device_ids or []))
            },
        )

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
