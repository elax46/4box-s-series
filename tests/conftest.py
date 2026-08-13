"""Pytest configuration for tests that need a real Home Assistant instance.

Requires the `pytest-homeassistant-custom-component` package (see
`requirements-test.txt`). Tests in this file's directory that use the
`hass` fixture exercise the integration against real Home Assistant
core code (config entries, the MQTT component, entity registry, etc.)
rather than just importing modules in isolation.
"""

from __future__ import annotations

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make custom_components/ discoverable, per the framework's docs."""
    yield
