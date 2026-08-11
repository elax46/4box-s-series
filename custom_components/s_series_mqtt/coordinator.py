"""Polling coordinator for 4box S Series cumulative energy counters.

Unlike relay state, power and current (which the firmware publishes
spontaneously on `<ID>/stat/...`), the cumulative energy counter is only
available on request: you publish `energyActive=RELAY<n>` on `<ID>/cmnd`
and the firmware replies once on `<ID>/info`.

Because `<ID>/info` is a single shared topic used for *every* command
response, this coordinator serializes its requests (one channel at a
time, with a short pause in between) and only trusts the next message
that arrives after it published a request. If you also query the device
manually while the coordinator is mid-poll, you may see a stale/mismatched
value for that cycle -- see the README for details.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_ENERGY_RESPONSE_TIMEOUT, TOPIC_CMND, TOPIC_INFO

_LOGGER = logging.getLogger(__name__)

_NUMBER_RE = re.compile(r"-?\d+(\.\d+)?")

# Small delay between two channel queries so their /info responses cannot
# be confused with one another.
_INTER_CHANNEL_DELAY = 0.5


class SSeriesEnergyCoordinator(DataUpdateCoordinator[dict[int, float]]):
    """Polls energyActive=RELAYn for every channel of one device."""

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
            name=f"s_series_mqtt_energy_{device_id}",
            update_interval=timedelta(seconds=poll_interval),
        )
        self._device_id = device_id
        self._channels = channels
        self._cmnd_topic = TOPIC_CMND.format(id=device_id)
        self._info_topic = TOPIC_INFO.format(id=device_id)
        self._pending: asyncio.Future[str] | None = None
        self._unsub_info = None

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

    async def _async_query_channel(self, channel: int) -> float:
        payload = f"energyActive=RELAY{channel}"
        self._pending = self.hass.loop.create_future()
        await mqtt.async_publish(self.hass, self._cmnd_topic, payload)

        try:
            response = await asyncio.wait_for(
                self._pending, timeout=DEFAULT_ENERGY_RESPONSE_TIMEOUT
            )
        except asyncio.TimeoutError as err:
            raise UpdateFailed(
                f"No response from {self._device_id} for '{payload}' "
                f"within {DEFAULT_ENERGY_RESPONSE_TIMEOUT}s"
            ) from err
        finally:
            self._pending = None

        match = _NUMBER_RE.search(response)
        if not match:
            raise UpdateFailed(
                f"Unexpected energy payload from {self._device_id}: {response!r}"
            )
        return float(match.group())

    async def _async_update_data(self) -> dict[int, float]:
        result: dict[int, float] = {}
        for channel in range(1, self._channels + 1):
            result[channel] = await self._async_query_channel(channel)
            await asyncio.sleep(_INTER_CHANNEL_DELAY)
        return result
