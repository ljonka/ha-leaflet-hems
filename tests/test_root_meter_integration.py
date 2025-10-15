"""Test root meter integration for total acquisition and return sensors."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.leaflet_hems.const import DOMAIN
from custom_components.leaflet_hems import LeafletHEMSCoordinator


@pytest.fixture
def mock_client():
    """Create a mock NymeaClient."""
    client = Mock()
    client.register_notification_callback = Mock(return_value="test_token")
    client.unregister_notification_callback = Mock()
    client.send_request_with_response = AsyncMock()
    return client


@pytest.fixture
def mock_hass():
    """Create a mock HomeAssistant instance."""
    hass = Mock()
    hass.async_create_task = Mock()
    return hass


@pytest.mark.asyncio
async def test_root_meter_data_exclusively_for_total_sensors(mock_hass, mock_client):
    """Test that totalAcquisition and totalReturn come exclusively from root meter."""
    coordinator = LeafletHEMSCoordinator(mock_hass, mock_client, "Test Device")
    
    # Mock root meter ID
    coordinator._root_meter_thing_id = "root_meter_123"
    
    # Mock Energy.GetPowerBalance response (should not include totalAcquisition/totalReturn)
    power_balance_response = {
        "status": "success",
        "params": {
            "currentPowerConsumption": 1500,
            "currentPowerProduction": 2000,
            "currentPowerAcquisition": -500,
            "currentPowerStorage": 0,
            "totalConsumption": 1000,
            "totalProduction": 800,
            # Note: no totalAcquisition or totalReturn here
        }
    }
    
    # Mock Things.GetStateValues response (root meter states)
    root_meter_response = {
        "status": "success",
        "params": {
            "values": [
                {"stateName": "totalEnergyConsumed", "value": 2500.5},
                {"stateName": "totalEnergyProduced", "value": 1800.3},
                {"stateName": "someOtherState", "value": 123}
            ]
        }
    }
    
    # Set up mock to return different responses based on method
    async def mock_send_request_with_response(method, params=None, timeout=10.0):
        if method == "Energy.GetPowerBalance":
            return power_balance_response
        elif method == "Things.GetStateValues":
            return root_meter_response
        return {"status": "error"}
    
    mock_client.send_request_with_response.side_effect = mock_send_request_with_response
    
    # Call the update method
    data = await coordinator._async_update_data()
    
    # Verify that power balance data is included (except totalAcquisition/totalReturn)
    assert data["currentPowerConsumption"] == 1500
    assert data["currentPowerProduction"] == 2000
    assert data["currentPowerAcquisition"] == -500
    assert data["totalConsumption"] == 1000
    assert data["totalProduction"] == 800
    
    # Verify that totalAcquisition and totalReturn come exclusively from root meter
    assert data["totalAcquisition"] == 2500.5
    assert data["totalReturn"] == 1800.3
    
    # Verify that the correct methods were called
    mock_client.send_request_with_response.assert_any_call("Energy.GetPowerBalance", timeout=10.0)
    mock_client.send_request_with_response.assert_any_call(
        "Things.GetStateValues", {"thingId": "root_meter_123"}, timeout=10.0
    )


@pytest.mark.asyncio
async def test_root_meter_data_fallback_when_power_balance_fails(mock_hass, mock_client):
    """Test that root meter data is used when power balance fails."""
    coordinator = LeafletHEMSCoordinator(mock_hass, mock_client, "Test Device")
    
    # Mock root meter ID
    coordinator._root_meter_thing_id = "root_meter_123"
    
    # Mock Energy.GetPowerBalance to fail
    power_balance_response = {
        "status": "error",
        "error": "Method not found"
    }
    
    # Mock Things.GetStateValues response (root meter states)
    root_meter_response = {
        "status": "success",
        "params": {
            "values": [
                {"stateName": "totalEnergyConsumed", "value": 3000.0},
                {"stateName": "totalEnergyProduced", "value": 2200.0}
            ]
        }
    }
    
    # Set up mock to return different responses based on method
    async def mock_send_request_with_response(method, params=None, timeout=10.0):
        if method == "Energy.GetPowerBalance":
            return power_balance_response
        elif method == "Things.GetStateValues":
            return root_meter_response
        return {"status": "error"}
    
    mock_client.send_request_with_response.side_effect = mock_send_request_with_response
    
    # Call the update method
    data = await coordinator._async_update_data()
    
    # Verify that only root meter data is included
    assert data["totalAcquisition"] == 3000.0
    assert data["totalReturn"] == 2200.0
    
    # Verify that no other power balance data is included (since it failed)
    assert "currentPowerConsumption" not in data
    assert "currentPowerProduction" not in data


@pytest.mark.asyncio
async def test_no_root_meter_data_when_no_root_meter(mock_hass, mock_client):
    """Test behavior when no root meter is available."""
    coordinator = LeafletHEMSCoordinator(mock_hass, mock_client, "Test Device")
    
    # No root meter ID set
    coordinator._root_meter_thing_id = None
    
    # Mock Energy.GetPowerBalance response
    power_balance_response = {
        "status": "success",
        "params": {
            "currentPowerConsumption": 1500,
            "currentPowerProduction": 2000,
            "totalConsumption": 1000,
            "totalProduction": 800,
        }
    }
    
    mock_client.send_request_with_response.return_value = power_balance_response
    
    # Call the update method
    data = await coordinator._async_update_data()
    
    # Verify that power balance data is included
    assert data["currentPowerConsumption"] == 1500
    assert data["currentPowerProduction"] == 2000
    assert data["totalConsumption"] == 1000
    assert data["totalProduction"] == 800
    
    # Verify that totalAcquisition and totalReturn are not included (no root meter)
    assert "totalAcquisition" not in data
    assert "totalReturn" not in data


@pytest.mark.asyncio
async def test_root_meter_notification_handling(mock_hass, mock_client):
    """Test that root meter notifications update the data correctly."""
    coordinator = LeafletHEMSCoordinator(mock_hass, mock_client, "Test Device")
    
    # Set initial data
    coordinator._data = {
        "currentPowerConsumption": 1500,
        "totalAcquisition": 2500.0,
        "totalReturn": 1800.0
    }
    
    # Mock root meter ID
    coordinator._root_meter_thing_id = "root_meter_123"
    
    # Simulate root meter notification for totalEnergyConsumed
    notification = {
        "method": "Thing.StateChanged",
        "params": {
            "thingId": "root_meter_123",
            "stateName": "totalEnergyConsumed",
            "value": 2600.0
        }
    }
    
    # Handle the notification
    coordinator._handle_root_meter_notification(notification)
    
    # Verify that totalAcquisition was updated
    assert coordinator.data["totalAcquisition"] == 2600.0
    assert coordinator.data["totalReturn"] == 1800.0  # Unchanged
    assert coordinator.data["currentPowerConsumption"] == 1500  # Unchanged


@pytest.mark.asyncio
async def test_root_meter_notification_ignored_for_wrong_thing(mock_hass, mock_client):
    """Test that notifications for wrong thing ID are ignored."""
    coordinator = LeafletHEMSCoordinator(mock_hass, mock_client, "Test Device")
    
    # Set initial data
    coordinator._data = {
        "totalAcquisition": 2500.0,
        "totalReturn": 1800.0
    }
    
    # Mock root meter ID
    coordinator._root_meter_thing_id = "root_meter_123"
    
    # Simulate notification for different thing
    notification = {
        "method": "Thing.StateChanged",
        "params": {
            "thingId": "different_thing_456",
            "stateName": "totalEnergyConsumed",
            "value": 2600.0
        }
    }
    
    # Handle the notification
    coordinator._handle_root_meter_notification(notification)
    
    # Verify that data was not updated
    assert coordinator.data["totalAcquisition"] == 2500.0
    assert coordinator.data["totalReturn"] == 1800.0
