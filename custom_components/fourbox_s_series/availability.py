"""Debounced device availability tracking, shared by switch.py and cover.py.

Both platforms mark an entity unavailable the instant `<ID>/connect`
reports "false". Real hardware can drop off the broker for a second or
two and reconnect on its own -- observed in practice on a P40S switching
a high-current load (an oven): the relay engaging causes a brief
power-supply brownout that resets the device's Wi-Fi/MQTT connection,
which republishes "true" moments later. Reflecting every single "false"
immediately floods entity history with spurious unavailable/available
blips for something that was never really a meaningful outage from the
user's point of view.
"""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

DEFAULT_AVAILABILITY_DEBOUNCE_SECONDS = 5


class DebouncedAvailability:
    """Debounces a device's `<ID>/connect` availability signal.

    "true" is always applied immediately -- there's no reason to delay
    good news. "false" is delayed by `debounce_seconds`: if a "true"
    arrives before the delay elapses, the pending "false" is cancelled
    and `on_change` is never called with `False` at all, so nothing
    user-visible happens for a blip that resolved itself.

    A genuine, sustained outage still ends up unavailable after the
    debounce window -- this only smooths over brief flaps, it doesn't
    hide real connectivity loss.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        on_change: Callable[[bool], None],
        debounce_seconds: float = DEFAULT_AVAILABILITY_DEBOUNCE_SECONDS,
    ) -> None:
        self._hass = hass
        self._on_change = on_change
        self._debounce_seconds = debounce_seconds
        self._cancel_pending: Callable[[], None] | None = None

    @callback
    def handle_message(self, payload: str) -> None:
        """Feed a raw `<ID>/connect` payload ("true"/"false") through
        the debounce, calling `on_change` immediately or after the
        delay as appropriate."""
        is_available = payload.strip().lower() == "true"

        if self._cancel_pending is not None:
            self._cancel_pending()
            self._cancel_pending = None

        if is_available:
            self._on_change(True)
            return

        @callback
        def _apply_unavailable(_now) -> None:
            self._cancel_pending = None
            self._on_change(False)

        self._cancel_pending = async_call_later(
            self._hass, self._debounce_seconds, _apply_unavailable
        )

    def cancel(self) -> None:
        """Cancel any pending debounce timer.

        Call this on entity removal (via `async_on_remove`) so a
        just-arrived "false" doesn't fire after the entity is gone.
        """
        if self._cancel_pending is not None:
            self._cancel_pending()
            self._cancel_pending = None
