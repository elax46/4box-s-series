"""Unit tests for `availability.DebouncedAvailability`, run against a
real (test) Home Assistant core instance (needed for `async_call_later`'s
event-loop scheduling) via `pytest-homeassistant-custom-component`.

Requires `requirements-test.txt` to be installed. Run with:

    pytest tests/test_availability.py
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.fourbox_s_series.availability import DebouncedAvailability


@pytest.fixture
def expected_lingering_timers():
    return True


async def test_true_is_applied_immediately(hass: HomeAssistant) -> None:
    calls: list[bool] = []
    debouncer = DebouncedAvailability(hass, calls.append, debounce_seconds=5)

    debouncer.handle_message("true")
    await hass.async_block_till_done()

    assert calls == [True]


async def test_false_is_delayed_by_debounce_window(hass: HomeAssistant) -> None:
    calls: list[bool] = []
    debouncer = DebouncedAvailability(hass, calls.append, debounce_seconds=5)

    debouncer.handle_message("false")
    await hass.async_block_till_done()
    assert calls == []  # not yet -- still within the window

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done()
    assert calls == [False]


async def test_true_before_window_elapses_cancels_pending_false(
    hass: HomeAssistant,
) -> None:
    """The core behavior this exists for: a brief blip (false then true,
    both within the debounce window) must never call `on_change(False)`
    at all."""
    calls: list[bool] = []
    debouncer = DebouncedAvailability(hass, calls.append, debounce_seconds=5)

    debouncer.handle_message("false")
    debouncer.handle_message("true")
    await hass.async_block_till_done()

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done()

    assert calls == [True]  # the pending False never fired


async def test_payload_is_case_and_whitespace_insensitive(
    hass: HomeAssistant,
) -> None:
    calls: list[bool] = []
    debouncer = DebouncedAvailability(hass, calls.append, debounce_seconds=5)

    debouncer.handle_message("  TRUE  ")
    await hass.async_block_till_done()

    assert calls == [True]


async def test_cancel_prevents_a_pending_false_from_firing(
    hass: HomeAssistant,
) -> None:
    """`cancel()` (called on entity removal) must stop a pending debounce
    timer from calling `on_change` after the entity is gone."""
    calls: list[bool] = []
    debouncer = DebouncedAvailability(hass, calls.append, debounce_seconds=5)

    debouncer.handle_message("false")
    await hass.async_block_till_done()
    debouncer.cancel()

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done()

    assert calls == []


async def test_repeated_false_resets_the_debounce_window(
    hass: HomeAssistant,
) -> None:
    """A second "false" arriving while one is already pending must not
    fire twice or early -- it just re-arms the same single-shot timer."""
    calls: list[bool] = []
    debouncer = DebouncedAvailability(hass, calls.append, debounce_seconds=5)

    debouncer.handle_message("false")
    await hass.async_block_till_done()
    debouncer.handle_message("false")
    await hass.async_block_till_done()

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done()

    assert calls == [False]
