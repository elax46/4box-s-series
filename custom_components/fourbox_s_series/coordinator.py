"""Polling coordinators for values the firmware only reports on request.

Most state (relay on/off, power, current, motor position, fault flags) is
pushed spontaneously by the firmware on `<ID>/stat/...` topics, so those
entities just subscribe once and need no coordinator at all.

A few values are different: the firmware only reports them when asked, by
publishing a command on `<ID>/cmnd` and replying once on `<ID>/info`:
- the cumulative energy counter (`energyActive=RELAY<n>`)
- the thermostat's mode, setpoint, and SHT4x temperature/humidity readings

Because `<ID>/info` is a single shared topic used for *every* command
response, `RequestResponsePoller` serializes its own requests and only
trusts the next `/info` message that arrives after it published one, with
a timeout. If you also send commands to the same device manually while a
poll is in flight, you may see a stale/mismatched value for that one
cycle -- see the README for details.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta
from typing import Any

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CMD_GPIOSTATUS_GET,
    CMD_HUMIDITY_GET,
    CMD_SETPOINT_GET,
    CMD_TEMPERATURE_GET,
    CMD_THERMOSTAT_MODE_GET,
    DEFAULT_MQTT_RESPONSE_TIMEOUT,
    DOMAIN,
    TOPIC_CMND,
    TOPIC_CONNECT,
    TOPIC_INFO,
)
from .utils import parse_gpio_status

_LOGGER = logging.getLogger(__name__)

_NUMBER_RE = re.compile(r"-?\d+(\.\d+)?")

# Small delay between two sequential queries on the same device so their
# /info responses cannot be confused with one another.
_INTER_QUERY_DELAY = 0.5


def _extract_number(response: str) -> float | None:
    match = _NUMBER_RE.search(response)
    return float(match.group()) if match else None


class RequestResponsePoller:
    """Shared plumbing for `/cmnd` request -> `/info` response polling."""

    def __init__(self, hass: HomeAssistant, device_id: str) -> None:
        self.hass = hass
        self._device_id = device_id
        self._cmnd_topic = TOPIC_CMND.format(id=device_id)
        self._info_topic = TOPIC_INFO.format(id=device_id)
        self._pending: asyncio.Future[str] | None = None
        self._unsub_info = None

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def cmnd_topic(self) -> str:
        return self._cmnd_topic

    async def async_start(self) -> None:
        """Subscribe to `<ID>/info` to catch command responses."""
        self._unsub_info = await mqtt.async_subscribe(
            self.hass, self._info_topic, self._handle_info_message
        )

    async def async_stop(self) -> None:
        """Unsubscribe from MQTT."""
        if self._unsub_info is not None:
            self._unsub_info()
            self._unsub_info = None

    @callback
    def _handle_info_message(self, msg) -> None:
        if self._pending is not None and not self._pending.done():
            self._pending.set_result(msg.payload)

    async def async_request(self, payload: str) -> str:
        """Publish `payload` on `/cmnd` and return the next `/info` reply."""
        self._pending = self.hass.loop.create_future()
        await mqtt.async_publish(self.hass, self._cmnd_topic, payload)

        try:
            return await asyncio.wait_for(
                self._pending, timeout=DEFAULT_MQTT_RESPONSE_TIMEOUT
            )
        except asyncio.TimeoutError as err:
            raise UpdateFailed(
                f"No response from {self._device_id} for {payload!r} "
                f"within {DEFAULT_MQTT_RESPONSE_TIMEOUT}s"
            ) from err
        finally:
            self._pending = None


class SSeriesEnergyCoordinator(DataUpdateCoordinator[dict[int, float]]):
    """Polls energyActive=RELAYn for every channel of one relay device."""

    def __init__(
        self,
        hass: HomeAssistant,
        device_id: str,
        channels: int,
        poll_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"fourbox_s_series_energy_{device_id}",
            update_interval=timedelta(seconds=poll_interval),
        )
        self._channels = channels
        self._poller = RequestResponsePoller(hass, device_id)

    async def async_start(self) -> None:
        await self._poller.async_start()

    async def async_stop(self) -> None:
        await self._poller.async_stop()

    async def _async_query_channel(self, channel: int) -> float:
        payload = f"energyActive=RELAY{channel}"
        response = await self._poller.async_request(payload)
        value = _extract_number(response)
        if value is None:
            raise UpdateFailed(
                f"Unexpected energy payload from {self._poller.device_id}: {response!r}"
            )
        return value

    async def _async_update_data(self) -> dict[int, float]:
        result: dict[int, float] = {}
        for channel in range(1, self._channels + 1):
            result[channel] = await self._async_query_channel(channel)
            await asyncio.sleep(_INTER_QUERY_DELAY)
        return result


class SSeriesThermostatCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls mode, setpoint, temperature and humidity for one thermostat.

    Returns a dict with keys: ``mode`` (int 0/1/2), ``setpoint`` (float),
    ``current_temperature`` (float), ``current_humidity`` (float). Any key
    whose query failed to parse is omitted rather than aborting the whole
    update, so one bad reading doesn't blank out the others.
    """

    def __init__(self, hass: HomeAssistant, device_id: str, poll_interval: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"fourbox_s_series_thermostat_{device_id}",
            update_interval=timedelta(seconds=poll_interval),
        )
        self._poller = RequestResponsePoller(hass, device_id)

    async def async_start(self) -> None:
        await self._poller.async_start()

    async def async_stop(self) -> None:
        await self._poller.async_stop()

    async def _async_update_data(self) -> dict[str, Any]:
        result: dict[str, Any] = {}

        queries = [
            ("mode", CMD_THERMOSTAT_MODE_GET),
            ("setpoint", CMD_SETPOINT_GET),
            ("current_temperature", CMD_TEMPERATURE_GET),
            ("current_humidity", CMD_HUMIDITY_GET),
        ]

        for key, command in queries:
            response = await self._poller.async_request(command)
            value = _extract_number(response)
            if value is not None:
                result[key] = int(value) if key == "mode" else value
            else:
                _LOGGER.debug(
                    "Could not parse %s response for %s: %r",
                    command,
                    self._poller.device_id,
                    response,
                )
            await asyncio.sleep(_INTER_QUERY_DELAY)

        if not result:
            raise UpdateFailed("None of the thermostat queries returned a value")

        return result

    async def async_send_command(self, payload: str) -> None:
        """Fire-and-forget a write command (mode/setpoint change)."""
        await mqtt.async_publish(self.hass, self._poller.cmnd_topic, payload)


def relay_states_signal(device_id: str) -> str:
    """Dispatcher signal name carrying a device's freshly re-fetched
    relay states (see SSeriesRelayStateRefresher)."""
    return f"{DOMAIN}_relay_states_{device_id}"


class SSeriesRelayStateRefresher:
    """Keeps relay switch state correct by re-querying `gpiostatus=GET`
    every time the device announces itself online via its `<ID>/connect`
    birth message.

    That birth message is published retained, so subscribing to it here
    fires immediately with whatever the device's current online/offline
    status already is -- including right at Home Assistant startup, no
    separate "initial fetch" step needed. It also fires again on every
    *future* reconnect (Wi-Fi drop, power cycle, or a brand-new device
    that's still finishing its own MQTT connection when this integration
    is first set up), which a one-shot fetch with a fixed timeout could
    never do.

    This replaces an earlier design where the initial state was fetched
    exactly once, synchronously, during `async_setup_entry`, with a fixed
    10s timeout: if the device wasn't actually online yet within that
    window (very plausible for a device that was *just* configured with
    MQTT settings in the vendor app), the fetch silently failed and the
    switch stayed wrong until the user manually reloaded the integration.
    """

    def __init__(self, hass: HomeAssistant, device_id: str, channels: int) -> None:
        self.hass = hass
        self._device_id = device_id
        self._channels = channels
        self._connect_topic = TOPIC_CONNECT.format(id=device_id)
        self._unsub_connect = None

    async def async_start(self) -> None:
        """Subscribe to `<ID>/connect`.

        If MQTT itself isn't ready yet (a startup race, not specific to
        this device), log and skip rather than raise: the switch will
        simply stay in its default "unknown until first push" state for
        this device, a graceful degradation rather than aborting setup
        of the whole entry over it.
        """
        try:
            self._unsub_connect = await mqtt.async_subscribe(
                self.hass, self._connect_topic, self._handle_connect
            )
        except HomeAssistantError as err:
            _LOGGER.warning(
                "Could not subscribe for relay state refresh on %s (%s); "
                "switches will show as off until the device's next state "
                "change or an integration reload",
                self._device_id,
                err,
            )

    async def async_stop(self) -> None:
        if self._unsub_connect is not None:
            self._unsub_connect()
            self._unsub_connect = None

    @callback
    def _handle_connect(self, msg) -> None:
        if msg.payload.strip().lower() != "true":
            return
        self.hass.async_create_task(self._async_refresh())

    async def _async_refresh(self) -> None:
        poller = RequestResponsePoller(self.hass, self._device_id)
        await poller.async_start()
        try:
            response = await poller.async_request(CMD_GPIOSTATUS_GET)
        except UpdateFailed as err:
            _LOGGER.debug(
                "gpiostatus=GET refresh failed for %s (%s)", self._device_id, err
            )
            return
        finally:
            await poller.async_stop()

        states = parse_gpio_status(response, self._channels)
        if states:
            async_dispatcher_send(
                self.hass, relay_states_signal(self._device_id), states
            )
        else:
            _LOGGER.debug(
                "Unrecognized gpiostatus=GET response for %s: %r",
                self._device_id,
                response,
            )
