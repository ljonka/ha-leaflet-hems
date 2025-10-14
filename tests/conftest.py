"""Fixtures for Leaflet HEMS integration tests."""

import pytest

from homeassistant.setup import async_setup_component
from homeassistant.core import HomeAssistant

# This fixture is needed for pytest-homeassistant-custom-component
@pytest.fixture(autouse=True)
def enable_custom_integration(enable_custom_integration):
    """Enable loading of custom integrations."""
    # This is a pytest fixture provided by pytest-homeassistant-custom-component
    # It automatically enables custom integrations for the duration of the test.
    yield


@pytest.fixture
def hass() -> HomeAssistant:
    """Fixture to provide a Home Assistant instance."""
    # The actual HomeAssistant instance will be created by the
    # pytest-homeassistant-custom-component plugin.
    # This fixture here is mostly for type hinting and if we need to do
    # any specific setup that isn't covered by the plugin.
    yield # The plugin will provide the actual instance
