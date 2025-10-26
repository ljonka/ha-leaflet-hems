"""Test disconnection handling to ensure sensors become unavailable."""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from custom_components.leaflet_hems import LeafletHEMSCoordinator
from custom_components.leaflet_hems.client import NymeaClient


@pytest.mark.asyncio
async def test_coordinator_clears_data_on_disconnection(hass):
    """Test that coordinator clears all data when disconnection is detected."""
    # Create a mock client
    client = Mock(spec=NymeaClient)
    client.send_request_with_response = AsyncMock()
    client.register_notification_callback = Mock(return_value="test_token")
    client.ensure_connected = AsyncMock(return_value=True)
    
    # Create coordinator
    coordinator = LeafletHEMSCoordinator(hass, client, "Test HEMS")
    
    # Simulate some initial data
    coordinator._data = {
        "currentPowerConsumption": 1000,
        "currentPowerProduction": 500,
        "totalAcquisition": 1000,
        "totalReturn": 500,
    }
    coordinator._aggregated_data = {
        "inverter_group_current_power": 500,
        "battery_group_current_power": 200,
    }
    coordinator._battery_states = {
        "battery-1": {"batteryLevel": 80, "currentPower": 200}
    }
    coordinator._pv_states = {
        "pv-1": {"currentPower": 500, "totalEnergyProduced": 1000}
    }
    coordinator.last_update_success = True
    
    # Verify data is present
    assert len(coordinator._data) > 0
    assert len(coordinator._aggregated_data) > 0
    assert coordinator.last_update_success is True
    
    # Trigger disconnection handler
    coordinator._handle_disconnection()
    
    # Verify all data is cleared
    assert len(coordinator._data) == 0
    assert len(coordinator._aggregated_data) == 0
    assert len(coordinator._battery_states["battery-1"]) == 0
    assert len(coordinator._pv_states["pv-1"]) == 0
    assert coordinator.last_update_success is False
    
    # Verify coordinator.data is empty (this is what sensors check)
    assert coordinator.data == {}


@pytest.mark.asyncio
async def test_client_calls_disconnection_callbacks(hass):
    """Test that client calls registered disconnection callbacks."""
    client = NymeaClient()
    
    # Track callback invocations
    callback_called = False
    
    def disconnection_callback():
        nonlocal callback_called
        callback_called = True
    
    # Register callback
    client.register_disconnection_callback(disconnection_callback)
    
    # Simulate disconnection by calling _handle_disconnect
    # Note: This would normally be triggered by connection errors
    await client._handle_disconnect("Test disconnection")
    
    # Verify callback was called
    assert callback_called is True


@pytest.mark.asyncio
async def test_sensor_availability_after_disconnection(hass):
    """Test that sensors become unavailable after disconnection."""
    from custom_components.leaflet_hems.sensor import LeafletPowerBalanceSensor
    
    # Create a mock coordinator
    coordinator = Mock()
    coordinator.data = {"currentPowerConsumption": 1000}
    coordinator.last_update_success = True
    
    # Create a sensor
    sensor_config = {
        "key": "currentPowerConsumption",
        "name": "Current Power Consumption",
        "device_class": "power",
        "unit": "W",
        "state_class": "measurement",
    }
    
    sensor = LeafletPowerBalanceSensor(
        coordinator=coordinator,
        client=Mock(),
        config_entry=Mock(),
        nymea_uuid="test-uuid",
        nymea_name="Test HEMS",
        sensor_config=sensor_config,
        notifications_enabled=False,
    )
    
    # Verify sensor is available initially
    assert sensor.available is True
    
    # Simulate disconnection - clear data and mark update as failed
    coordinator.data = {}
    coordinator.last_update_success = False
    
    # Verify sensor becomes unavailable
    assert sensor.available is False
