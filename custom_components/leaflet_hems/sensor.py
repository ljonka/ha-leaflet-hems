"""Sensor platform for Leaflet HEMS integration."""

import asyncio
import logging
from typing import Any, Dict, Optional
import homeassistant.exceptions

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfEnergy, PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, NAME, VERSION, CONF_NYMEA_UUID, CONF_NYMEA_NAME, DEVICE_TYPE_BATTERY, DEVICE_TYPE_INVERTER
from .client import NymeaClient

_LOGGER = logging.getLogger(__name__)

# Power balance sensor definitions based on dart client
POWER_BALANCE_SENSORS = [
    {
        "key": "currentPowerConsumption",
        "name": "Current Power Consumption",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    {
        "key": "currentPowerProduction", 
        "name": "Current Power Production",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    {
        "key": "currentPowerAcquisition",
        "name": "Current Power Acquisition",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    {
        "key": "currentPowerStorage",
        "name": "Current Power Storage",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    {
        "key": "totalConsumption",
        "name": "Total Consumption",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "state_class": SensorStateClass.TOTAL,
    },
    {
        "key": "totalProduction", 
        "name": "Total Production",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "state_class": SensorStateClass.TOTAL,
    },
    {
        "key": "totalAcquisition",
        "name": "Total Acquisition", 
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "state_class": SensorStateClass.TOTAL,
    },
    {
        "key": "totalReturn",
        "name": "Total Return",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "state_class": SensorStateClass.TOTAL,
    },
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Leaflet HEMS sensor entities."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = entry_data["coordinator"]
    client: NymeaClient = entry_data["client"]
    nymea_uuid = entry_data["uuid"]
    nymea_name = entry_data["name"]
    notifications_enabled = entry_data.get("notifications_enabled", False)

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Create sensor entities for power balance data
    entities = []
    for sensor_config in POWER_BALANCE_SENSORS:
        entity = LeafletPowerBalanceSensor(
            coordinator=coordinator,
            client=client,
            config_entry=config_entry,
            nymea_uuid=nymea_uuid,
            nymea_name=nymea_name,
            sensor_config=sensor_config,
            notifications_enabled=notifications_enabled,
        )
        entities.append(entity)

    # Create aggregated group sensors
    aggregated_sensors = [
        {
            "type": "inverter_group_current_power",
            "name": "Inverter Group Current Power",
        },
        {
            "type": "inverter_group_total_energy",
            "name": "Inverter Group Total Energy",
        },
        {
            "type": "battery_group_current_power", 
            "name": "Battery Group Current Power",
        },
    ]
    
    for sensor_config in aggregated_sensors:
        entity = LeafletAggregatedSensor(
            coordinator=coordinator,
            config_entry=config_entry,
            nymea_uuid=nymea_uuid,
            nymea_name=nymea_name,
            sensor_type=sensor_config["type"],
            sensor_name=sensor_config["name"],
        )
        entities.append(entity)

    async_add_entities(entities, True)

    # Create fresh tracking set for this setup (don't persist from previous runs)
    # This ensures sensors are recreated if the integration is reloaded
    added_thing_ids = set()

    # Set up dynamic sensors for batteries and inverters
    @callback
    def _create_dynamic_sensors(async_add_entities_callback: AddEntitiesCallback):
        """Create dynamic sensors for batteries and inverters."""
        _LOGGER.debug("Creating dynamic sensors - batteries: %s, PV: %s", 
                     len(coordinator._battery_configs), len(coordinator._pv_configs))
        new_entities_to_add = []

        # Create battery sensors
        for battery_id, battery_config in coordinator._battery_configs.items():
            if battery_id in added_thing_ids:
                continue

            battery_sensors = [
                {"type": "batteryLevel", "name": "Battery Level"},
                {"type": "chargingState", "name": "Charging State"},
                {"type": "currentPower", "name": "Current Power"},
            ]

            for sensor_config in battery_sensors:
                entity = LeafletBatterySensor(
                    coordinator=coordinator,
                    config_entry=config_entry,
                    nymea_uuid=nymea_uuid,
                    nymea_name=nymea_name,
                    battery_thing_id=battery_id,
                    sensor_type=sensor_config["type"],
                    sensor_name=sensor_config["name"],
                )
                new_entities_to_add.append(entity)
            added_thing_ids.add(battery_id)
        
        # Create inverter sensors
        for pv_id, pv_config in coordinator._pv_configs.items():
            if pv_id in added_thing_ids:
                continue

            inverter_sensors = [
                {"type": "currentPower", "name": "Current Power"},
                {"type": "totalEnergyProduced", "name": "Total Energy Produced"},
            ]
            
            for sensor_config in inverter_sensors:
                entity = LeafletInverterSensor(
                    coordinator=coordinator,
                    config_entry=config_entry,
                    nymea_uuid=nymea_uuid,
                    nymea_name=nymea_name,
                    pv_thing_id=pv_id,
                    sensor_type=sensor_config["type"],
                    sensor_name=sensor_config["name"],
                )
                new_entities_to_add.append(entity)
            added_thing_ids.add(pv_id)
        
        if new_entities_to_add:
            async_add_entities_callback(new_entities_to_add)

    # Initial creation of dynamic sensors - only create after initial data
    @callback
    def _create_sensors_after_data():
        if not coordinator.data:
            _LOGGER.warning("Coordinator data not available, delaying sensor creation")
            return
            
        async def _create_when_ready():
            # Verify we have battery/PV configs
            if not coordinator._battery_configs or not coordinator._pv_configs:
                _LOGGER.warning("Battery/PV configs not populated, retrying in 2s")
                await asyncio.sleep(2.0)
            _create_dynamic_sensors(async_add_entities)

        hass.async_create_task(_create_when_ready())

    # Register callback for dynamic sensor creation when coordinator data is updated
    @callback
    def _coordinator_listener():
        """Create sensors when coordinator updates with new battery/PV data."""
        _LOGGER.debug("Coordinator listener triggered - checking for new battery/PV sensors...")
        # Only create new sensors, don't try to refresh existing ones
        _create_dynamic_sensors(async_add_entities)

    # Add listener for future updates
    coordinator.async_add_listener(_coordinator_listener)

    # Wait for initial data to be available before creating sensors
    async def _delayed_sensor_creation():
        """Wait for coordinator to have data, then create sensors."""
        # Wait for up to 30 seconds for data to be available
        for _ in range(60):  # 60 * 0.5s = 30s
            if coordinator.data and (coordinator._battery_configs or coordinator._pv_configs):
                _LOGGER.info("Coordinator data available, creating dynamic sensors")
                _create_dynamic_sensors(async_add_entities)
                break
            await asyncio.sleep(0.5)
        else:
            _LOGGER.warning("Timeout waiting for coordinator data, creating sensors anyway")
            _create_dynamic_sensors(async_add_entities)
    
    # Clean up old voltage sensors from entity registry
    async def _cleanup_old_voltage_sensors():
        """Remove old voltage sensor entities from the entity registry."""
        entity_reg = er.async_get(hass)
        
        # Find and remove voltage sensors
        entities_to_remove = []
        for entity_id, entry in entity_reg.entities.items():
            if entry.config_entry_id == config_entry.entry_id:
                # Check if this is a voltage sensor
                if "_voltage" in entry.unique_id or "voltage" in entity_id:
                    entities_to_remove.append(entity_id)
                    _LOGGER.info("Removing old voltage sensor entity: %s", entity_id)
        
        # Remove the entities
        for entity_id in entities_to_remove:
            entity_reg.async_remove(entity_id)
        
        if entities_to_remove:
            _LOGGER.info("Removed %d old voltage sensor entities", len(entities_to_remove))
    
    # Schedule cleanup and sensor creation
    hass.async_create_task(_cleanup_old_voltage_sensors())
    hass.async_create_task(_delayed_sensor_creation())


class LeafletPowerBalanceSensor(CoordinatorEntity, SensorEntity):
    """Sensor for Leaflet HEMS power balance data."""

    def __init__(
        self,
        coordinator,
        client: NymeaClient,
        config_entry: ConfigEntry,
        nymea_uuid: str,
        nymea_name: str,
        sensor_config: Dict[str, Any],
        notifications_enabled: bool,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._client = client
        self._config_entry = config_entry
        self._nymea_uuid = nymea_uuid
        self._nymea_name = nymea_name
        self._sensor_config = sensor_config
        self._notifications_enabled = notifications_enabled
        self._notification_token: Optional[str] = None
        
        # Sensor attributes
        self._attr_name = f"{nymea_name} {sensor_config['name']}"
        self._attr_unique_id = f"{nymea_uuid}_{sensor_config['key']}"
        self._attr_device_class = sensor_config["device_class"]
        self._attr_native_unit_of_measurement = sensor_config["unit"]
        self._attr_state_class = sensor_config["state_class"]
        self._attr_icon = "mdi:flash" if "Power" in sensor_config["name"] else "mdi:lightning-bolt"
        
        # Device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, nymea_uuid)},
            name=nymea_name,
            manufacturer="Consolinno",
            model="Leaflet HEMS",
            sw_version=VERSION,
        )

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to hass."""
        await super().async_added_to_hass()
        # Force immediate update and write HA state
        await self.async_update()

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity is being removed."""
        # Notifications are handled centrally by the coordinator.
        return None

    async def async_update(self):
        """Force an update and log the current state."""
        # Call super to handle normal coordinator update
        await super().async_update()
        # Force state write to ensure HA gets notified
        try:
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.debug("Error writing state for %s: %s", self.name, e)


    @property
    def native_value(self):
        """Return the native value of the sensor."""
        if self.coordinator.data:
            key = self._sensor_config["key"]
            value = self.coordinator.data.get(key)
            
            # Handle both null and undefined values
            if value is None:
                # Attempt to get alternative casing if available
                alt_value = self.coordinator.data.get(key.lower()) or self.coordinator.data.get(key.upper())
                if alt_value is not None:
                    return alt_value
                    
                # Log missing keys for debugging
                available_keys = list(self.coordinator.data.keys())
                _LOGGER.debug(
                    "Key '%s' not found in coordinator data for sensor '%s'. Available keys: %s",
                    key,
                    self.name,
                    available_keys
                )
                return None
                
            return value
            
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not self.coordinator.last_update_success:
            return False
            
        key = self._sensor_config["key"]
        return key in (self.coordinator.data or {})


class LeafletBatterySensor(CoordinatorEntity, SensorEntity):
    """Sensor for Leaflet HEMS battery data."""

    def __init__(
        self,
        coordinator,
        config_entry: ConfigEntry,
        nymea_uuid: str,
        nymea_name: str,
        battery_thing_id: str,
        sensor_type: str,
        sensor_name: str,
    ) -> None:
        """Initialize the battery sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._nymea_uuid = nymea_uuid
        self._nymea_name = nymea_name
        self._battery_thing_id = battery_thing_id
        self._sensor_type = sensor_type
        self._sensor_name = sensor_name
        
        # Sensor attributes
        self._attr_name = f"{nymea_name} Battery {battery_thing_id[-8:]} {sensor_name}"
        self._attr_unique_id = f"{nymea_uuid}_battery_{battery_thing_id}_{sensor_type}"
        
        # Set device class and unit based on sensor type
        if sensor_type == "batteryLevel":
            self._attr_device_class = SensorDeviceClass.BATTERY
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_icon = "mdi:battery"
        elif sensor_type == "chargingState":
            self._attr_device_class = None
            self._attr_native_unit_of_measurement = None
            self._attr_state_class = None
            self._attr_icon = "mdi:battery-charging"
        elif sensor_type == "currentPower":
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_native_unit_of_measurement = UnitOfPower.WATT
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_icon = "mdi:flash"
        
        # Device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{nymea_uuid}_battery_{battery_thing_id}")},
            name=f"{nymea_name} Battery {battery_thing_id[-8:]}",
            manufacturer="Consolinno",
            model="Battery",
            sw_version=VERSION,
            via_device=(DOMAIN, nymea_uuid),
        )

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        if self.coordinator.data:
            key = f"battery_{self._battery_thing_id}_{self._sensor_type}"
            value = self.coordinator.data.get(key)
            
            # Handle both null and undefined values
            if value is None:
                # Attempt to get alternative casing if available
                alt_value = self.coordinator.data.get(key.lower()) or self.coordinator.data.get(key.upper())
                if alt_value is not None:
                    return alt_value
                    
                _LOGGER.debug(
                    "Battery sensor '%s' value missing for key '%s'",
                    self.name,
                    key
                )
                return None
                
            return value
            
        return None

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to hass."""
        await super().async_added_to_hass()
        # Force immediate state write
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # If coordinator hasn't successfully updated yet, mark as unavailable
        if not self.coordinator.last_update_success:
            return False
        
        # If coordinator has no data at all, mark as unavailable
        if not self.coordinator.data:
            return False
            
        key = f"battery_{self._battery_thing_id}_{self._sensor_type}"
        return key in self.coordinator.data


class LeafletInverterSensor(CoordinatorEntity, SensorEntity):
    """Sensor for Leaflet HEMS inverter data."""

    def __init__(
        self,
        coordinator,
        config_entry: ConfigEntry,
        nymea_uuid: str,
        nymea_name: str,
        pv_thing_id: str,
        sensor_type: str,
        sensor_name: str,
    ) -> None:
        """Initialize the inverter sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._nymea_uuid = nymea_uuid
        self._nymea_name = nymea_name
        self._pv_thing_id = pv_thing_id
        self._sensor_type = sensor_type
        self._sensor_name = sensor_name
        
        # Sensor attributes
        self._attr_name = f"{nymea_name} Inverter {pv_thing_id[-8:]} {sensor_name}"
        self._attr_unique_id = f"{nymea_uuid}_inverter_{pv_thing_id}_{sensor_type}"
        
        # Set device class and unit based on sensor type
        if sensor_type == "currentPower":
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_native_unit_of_measurement = UnitOfPower.WATT
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_icon = "mdi:solar-power"
        elif sensor_type == "totalEnergyProduced":
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
            self._attr_icon = "mdi:solar-panel"
        
        # Device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{nymea_uuid}_inverter_{pv_thing_id}")},
            name=f"{nymea_name} Inverter {pv_thing_id[-8:]}",
            manufacturer="Consolinno",
            model="Inverter",
            sw_version=VERSION,
            via_device=(DOMAIN, nymea_uuid),
        )

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        if self.coordinator.data:
            key = f"pv_{self._pv_thing_id}_{self._sensor_type}"
            return self.coordinator.data.get(key)
        return None

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to hass."""
        await super().async_added_to_hass()
        # Force immediate state write
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # If coordinator hasn't successfully updated yet, mark as unavailable
        if not self.coordinator.last_update_success:
            return False
        
        # If coordinator has no data at all, mark as unavailable
        if not self.coordinator.data:
            return False
        
        key = f"pv_{self._pv_thing_id}_{self._sensor_type}"
        return key in self.coordinator.data


class LeafletAggregatedSensor(CoordinatorEntity, SensorEntity):
    """Sensor for Leaflet HEMS aggregated data."""

    def __init__(
        self,
        coordinator,
        config_entry: ConfigEntry,
        nymea_uuid: str,
        nymea_name: str,
        sensor_type: str,
        sensor_name: str,
    ) -> None:
        """Initialize the aggregated sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._nymea_uuid = nymea_uuid
        self._nymea_name = nymea_name
        self._sensor_type = sensor_type
        self._sensor_name = sensor_name
        
        # Sensor attributes
        self._attr_name = f"{nymea_name} {sensor_name}"
        self._attr_unique_id = f"{nymea_uuid}_{sensor_type}"
        
        # Set device class and unit based on sensor type
        if sensor_type == "inverter_group_current_power":
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_native_unit_of_measurement = UnitOfPower.WATT
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_icon = "mdi:solar-power"
        elif sensor_type == "inverter_group_total_energy":
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
            self._attr_icon = "mdi:solar-panel"
        elif sensor_type == "battery_group_current_power":
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_native_unit_of_measurement = UnitOfPower.WATT
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_icon = "mdi:battery"
        
        # Device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, nymea_uuid)},
            name=nymea_name,
            manufacturer="Consolinno",
            model="Leaflet HEMS",
            sw_version=VERSION,
        )

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        if (self.coordinator._aggregated_data and 
            self._sensor_type in self.coordinator._aggregated_data):
            return self.coordinator._aggregated_data[self._sensor_type]
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # If coordinator hasn't successfully updated yet, mark as unavailable
        if not self.coordinator.last_update_success:
            return False
        
        # If there's no aggregated data at all, mark as unavailable
        if not self.coordinator._aggregated_data:
            return False
        
        # Check if this specific sensor type exists in aggregated data
        return self._sensor_type in self.coordinator._aggregated_data
