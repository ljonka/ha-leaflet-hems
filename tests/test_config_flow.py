"""Test the config flow of Leaflet HEMS integration."""

from unittest.mock import patch, AsyncMock

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.leaflet_hems import config_flow
from custom_components.leaflet_hems.const import (
    CONF_NYMEA_UUID,
    CONF_NYMEA_NAME,
    DOMAIN,
    DEFAULT_PORT,
    RPC_HELLO_METHOD,
    RPC_HELLO_LOCALE,
    RPC_ID,
)


# Mocking the Nymea device response for handshake
MOCK_NYMDA_HELLO_RESPONSE = {
    "id": RPC_ID,
    "status": "success",
    "params": {
        "authenticationRequired": False,
        "initialSetupRequired": False,
        "locale": "de_DE",
        "name": "Test Nymea Device",
        "protocol version": "4.1",
        "pushButtonAuthAvailable": False,
        "server": "nymea",
        "uuid": "12345678-1234-1234-1234-123456789abc",
        "version": "0.18.1+test",
    },
}

@pytest.fixture
def mock_aiohttp_session():
    """Mock aiohttp ClientSession."""
    with patch("aiohttp.ClientSession") as mock_session_class:
        mock_session = AsyncMock()
        mock_session_class.return_value = mock_session
        # Mock the post call within the session
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=MOCK_NYMDA_HELLO_RESPONSE)
        mock_session.post.return_value.__aenter__.return_value = mock_response
        yield mock_session

@pytest.mark.asyncio
async def test_user_step_goes_to_manual(hass: HomeAssistant):
    """Test user step goes directly to manual setup."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"


@pytest.mark.asyncio
async def test_manual_setup_success(hass: HomeAssistant, mock_aiohttp_session):
    """Test successful manual setup."""
    # Mock handshake to return UUID
    mock_aiohttp_session.post.return_value.__aenter__.return_value.json = AsyncMock(
        return_value=MOCK_NYMDA_HELLO_RESPONSE
    )

    flow = config_flow.LeafletHEMSFlowHandler()
    flow.hass = hass

    result = await flow.async_step_manual(
        user_input={
            CONF_HOST: "192.168.1.100",
            CONF_PORT: DEFAULT_PORT,
            CONF_NAME: "My Leaflet HEMS",
        }
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "My Leaflet HEMS"
    assert result["data"][CONF_HOST] == "192.168.1.100"
    assert result["data"][CONF_PORT] == DEFAULT_PORT
    assert result["data"][CONF_NYMEA_UUID] == MOCK_NYMDA_HELLO_RESPONSE["params"]["uuid"]
    assert result["data"][CONF_NYMEA_NAME] == "My Leaflet HEMS"

@pytest.mark.asyncio
async def test_manual_setup_uses_handshake_name(hass: HomeAssistant, mock_aiohttp_session):
    """Test manual setup uses name from handshake when not provided."""
    mock_aiohttp_session.post.return_value.__aenter__.return_value.json = AsyncMock(
        return_value=MOCK_NYMDA_HELLO_RESPONSE
    )
    flow = config_flow.LeafletHEMSFlowHandler()
    flow.hass = hass

    result = await flow.async_step_manual(
        user_input={
            CONF_HOST: "192.168.1.101",
            CONF_PORT: DEFAULT_PORT,
        }
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == MOCK_NYMDA_HELLO_RESPONSE["params"]["name"]
    assert result["data"][CONF_HOST] == "192.168.1.101"
    assert result["data"][CONF_NYMEA_UUID] == MOCK_NYMDA_HELLO_RESPONSE["params"]["uuid"]


@pytest.mark.asyncio
async def test_manual_setup_handshake_failure(hass: HomeAssistant, mock_aiohttp_session):
    """Test handshake failure during manual setup."""
    # Mock handshake to fail
    mock_aiohttp_session.post.return_value.__aenter__.return_value.json = AsyncMock(
        return_value={"status": "error", "error": "connection failed"}
    )
    
    flow = config_flow.LeafletHEMSFlowHandler()
    flow.hass = hass

    result = await flow.async_step_manual(
        user_input={
            CONF_HOST: "192.168.1.103",
            CONF_PORT: DEFAULT_PORT,
            CONF_NAME: "Failing Device",
        }
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"

@pytest.mark.asyncio
async def test_import_flow_success(hass: HomeAssistant, mock_aiohttp_session):
    """Test successful import flow."""
    mock_aiohttp_session.post.return_value.__aenter__.return_value.json = AsyncMock(
        return_value=MOCK_NYMDA_HELLO_RESPONSE
    )
    flow = config_flow.LeafletHEMSFlowHandler()
    flow.hass = hass

    result = await flow.async_step_import(
        user_input={
            CONF_HOST: "192.168.1.110",
            CONF_PORT: DEFAULT_PORT,
            CONF_NAME: "Imported HEMS",
        }
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Imported HEMS"
    assert result["data"][CONF_HOST] == "192.168.1.110"
    assert result["data"][CONF_NYMEA_UUID] == MOCK_NYMDA_HELLO_RESPONSE["params"]["uuid"]
