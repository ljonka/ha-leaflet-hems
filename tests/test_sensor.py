"""Test the sensor platform of Leaflet HEMS integration."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfPower, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.leaflet_hems.const import DOMAIN
from custom_components.leaflet_hems.sensor import LeafletPowerBalanceSensor, async_setup_entry


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""
    config_entry = Mock()
    config_entry.entry_id = "test_entry_id"
    return config_entry


@pytest.fixture
def mock_client():
    """Create a mock NymeaClient."""
    client = Mock()
    client.register_notification_callback = Mock(return_value="test_token")
    client.unregister_notification_callback = Mock()
    client.send_request_with_response = AsyncMock()
    return client


@pytest.fixture
def mock_entry_data(mock_client):
    """Create mock entry data."""
    return {
        "client": mock_client,
        "uuid": "test_uuid",
        "name": "Test Device",
        "notifications_enabled": True,
    }


@pytest.mark.asyncio
async def test_async_setup_entry(hass: HomeAssistant, mock_config_entry, mock_entry_data):
    """Test setting up sensor platform."""
    # Mock hass.data
    hass.data[DOMAIN] = {mock_config_entry.entry_id: mock_entry_data}
    
    # Mock async_add_entities
    add_entities_mock = Mock()
    
    # Run setup
    await async_setup_entry(hass, mock_config_entry, add_entities_mock)
    
    # Verify entities were added
    add_entities_mock.assert_called_once()
    entities = add_entities_mock.call_args[0][0]
    
    # Should create 8 entities for all power balance sensors
    assert len(entities) == 8
    
    # Verify entity types
    for entity in entities:
        assert isinstance(entity, LeafletPowerBalanceSensor)


@pytest.mark.asyncio
async def test_sensor_initialization(mock_client, mock_config_entry):
    """Test sensor entity initialization."""
    sensor_config = {
        "key": "currentPowerConsumption",
        "name": "Current Power Consumption",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": "measurement",
    }
    
    sensor = LeafletPowerBalanceSensor(
        client=mock_client,
        config_entry=mock_config_entry,
        nymea_uuid="test_uuid",
        nymea_name="Test Device",
        sensor_config=sensor_config,
        notifications_enabled=True,
    )
    
    # Verify attributes
    assert sensor._attr_name == "Test Device Current Power Consumption"
    assert sensor._attr_unique_id == "test_uuid_currentPowerConsumption"
    assert sensor._attr_device_class == SensorDeviceClass.POWER
    assert sensor._attr_native_unit_of_measurement == UnitOfPower.WATT
    assert sensor._attr_state_class == "measurement"


@pytest.mark.asyncio
async def test_sensor_async_update(mock_client, mock_config_entry):
    """Test sensor update via polling."""
    # Configure mock response
    mock_response = {
        "status": "success",
        "params": {
            "currentPowerConsumption": 1500,
            "currentPowerProduction": 2000,
            "currentPowerAcquisition": -500,
        }
    }
    mock_client.send_request_with_response.return_value = mock_response
    
    sensor_config = {
        "key": "currentPowerConsumption",
        "name": "Current Power Consumption",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": "measurement",
    }
    
    sensor = LeafletPowerBalanceSensor(
        client=mock_client,
        config_entry=mock_config_entry,
        nymea_uuid="test_uuid",
        nymea_name="Test Device",
        sensor_config=sensor_config,
        notifications_enabled=False,  # Test polling mode
    )
    
    # Run update
    await sensor.async_update()
    
    # Verify API call
    mock_client.send_request_with_response.assert_called_once_with(
        "Energy.GetPowerBalance", timeout=10.0
    )
    
    # Verify state was updated
    assert sensor._attr_native_value == 1500
    assert sensor._attr_available is True


@pytest.mark.asyncio
async def test_sensor_async_update_error(mock_client, mock_config_entry):
    """Test sensor update error handling."""
    # Configure mock to raise exception
    mock_client.send_request_with_response.side_effect = Exception("Connection error")
    
    sensor_config = {
        "key": "currentPowerConsumption",
        "name": "Current Power Consumption",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": "measurement",
    }
    
    sensor = LeafletPowerBalanceSensor(
        client=mock_client,
        config_entry=mock_config_entry,
        nymea_uuid="test_uuid",
        nymea_name="Test Device",
        sensor_config=sensor_config,
        notifications_enabled=False,
    )
    
    # Run update
    await sensor.async_update()
    
    # Verify availability was set to False
    assert sensor._attr_available is False


@pytest.mark.asyncio
async def test_sensor_async_update_api_error(mock_client, mock_config_entry):
    """Test sensor update with API error response."""
    # Configure mock response with error
    mock_response = {
        "status": "error",
        "error": "Method not found"
    }
    mock_client.send_request_with_response.return_value = mock_response
    
    sensor_config = {
        "key": "currentPowerConsumption",
        "name": "Current Power Consumption",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": "measurement",
    }
    
    sensor = LeafletPowerBalanceSensor(
        client=mock_client,
        config_entry=mock_config_entry,
        nymea_uuid="test_uuid",
        nymea_name="Test Device",
        sensor_config=sensor_config,
        notifications_enabled=False,
    )
    
    # Run update
    await sensor.async_update()
    
    # Verify availability remains True (API error is different from connection error)
    assert sensor._attr_available is True


@pytest.mark.asyncio
async def test_sensor_notification_handling(mock_client, mock_config_entry):
    """Test sensor notification callback."""
    sensor_config = {
        "key": "currentPowerConsumption",
        "name": "Current Power Consumption",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": "measurement",
    }
    
    sensor = LeafletPowerBalanceSensor(
        client=mock_client,
        config_entry=mock_config_entry,
        nymea_uuid="test_uuid",
        nymea_name="Test Device",
        sensor_config=sensor_config,
        notifications_enabled=True,
    )
    
    # Mock async_write_ha_state
    with patch.object(sensor, 'async_write_ha_state') as mock_write_state:
        # Simulate notification
        notification = {
            "method": "Energy.PowerBalanceChanged",
            "params": {
                "currentPowerConsumption": 1800,
                "currentPowerProduction": 2200,
            }
        }
        
        sensor._handle_notification(notification)
        
        # Verify state was updated
        assert sensor._attr_native_value == 1800
        mock_write_state.assert_called_once()


@pytest.mark.asyncio
async def test_sensor_notification_no_matching_key(mock_client, mock_config_entry):
    """Test notification handling when key is not present."""
    sensor_config = {
        "key": "currentPowerConsumption",
        "name": "Current Power Consumption",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": "measurement",
    }
    
    sensor = LeafletPowerBalanceSensor(
        client=mock_client,
        config_entry=mock_config_entry,
        nymea_uuid="test_uuid",
        nymea_name="Test Device",
        sensor_config=sensor_config,
        notifications_enabled=True,
    )
    
    # Set initial value
    sensor._attr_native_value = 1500
    
    # Mock async_write_ha_state
    with patch.object(sensor, 'async_write_ha_state') as mock_write_state:
        # Simulate notification without our key
        notification = {
            "method": "Energy.PowerBalanceChanged",
            "params": {
                "currentPowerProduction": 2200,  # Different key
            }
        }
        
        sensor._handle_notification(notification)
        
        # Verify state was not changed
        assert sensor._attr_native_value == 1500
        mock_write_state.assert_not_called()


@pytest.mark.asyncio
async def test_sensor_notification_same_value(mock_client, mock_config_entry):
    """Test notification handling when value hasn't changed."""
    sensor_config = {
        "key": "currentPowerConsumption",
        "name": "Current Power Consumption",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": "measurement",
    }
    
    sensor = LeafletPowerBalanceSensor(
        client=mock_client,
        config_entry=mock_config_entry,
        nymea_uuid="test_uuid",
        nymea_name="Test Device",
        sensor_config=sensor_config,
        notifications_enabled=True,
    )
    
    # Set initial value
    sensor._attr_native_value = 1800
    
    # Mock async_write_ha_state
    with patch.object(sensor, 'async_write_ha_state') as mock_write_state:
        # Simulate notification with same value
        notification = {
            "method": "Energy.PowerBalanceChanged",
            "params": {
                "currentPowerConsumption": 1800,  # Same value
            }
        }
        
        sensor._handle_notification(notification)
        
        # Verify state write was not called since value didn't change
        mock_write_state.assert_not_called()


@pytest.mark.asyncio
async def test_sensor_added_to_hass_with_notifications(mock_client, mock_config_entry):
    """Test sensor registration when added to hass with notifications enabled."""
    sensor_config = {
        "key": "currentPowerConsumption",
        "name": "Current Power Consumption",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": "measurement",
    }
    
    sensor = LeafletPowerBalanceSensor(
        client=mock_client,
        config_entry=mock_config_entry,
        nymea_uuid="test_uuid",
        nymea_name="Test Device",
        sensor_config=sensor_config,
        notifications_enabled=True,
    )
    
    # Mock async_update
    with patch.object(sensor, 'async_update', new_callable=AsyncMock) as mock_update:
        await sensor.async_added_to_hass()
        
        # Verify notification callback was registered
        mock_client.register_notification_callback.assert_called_once()
        
        # Verify initial update was called
        mock_update.assert_called_once()


@pytest.mark.asyncio
async def test_sensor_removed_from_hass(mock_client, mock_config_entry):
    """Test sensor cleanup when removed from hass."""
    sensor_config = {
        "key": "currentPowerConsumption",
        "name": "Current Power Consumption",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": "measurement",
    }
    
    sensor = LeafletPowerBalanceSensor(
        client=mock_client,
        config_entry=mock_config_entry,
        nymea_uuid="test_uuid",
        nymea_name="Test Device",
        sensor_config=sensor_config,
        notifications_enabled=True,
    )
    
    # Set notification token
    sensor._notification_token = "test_token"
    
    await sensor.async_will_remove_from_hass()
    
    # Verify notification callback was unregistered
    mock_client.unregister_notification_callback.assert_called_once_with("test_token")
