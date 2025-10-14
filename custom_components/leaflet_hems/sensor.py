"""Sensor platform for Leaflet HEMS integration."""

import logging
from typing import Any, Dict, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME, VERSION, CONF_NYMEA_UUID, CONF_NYMEA_NAME
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
        "state_class": SensorStateClass.TOTAL_INCREASING,
    },
    {
        "key": "totalProduction", 
        "name": "Total Production",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "state_class": SensorStateClass.TOTAL_INCREASING,
    },
    {
        "key": "totalAcquisition",
        "name": "Total Acquisition", 
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "state_class": SensorStateClass.TOTAL_INCREASING,
    },
    {
        "key": "totalReturn",
        "name": "Total Return",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "state_class": SensorStateClass.TOTAL_INCREASING,
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

    async_add_entities(entities, True)
    _LOGGER.info("Added %d Leaflet HEMS sensor entities", len(entities))


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
        
        # Register for notifications if enabled
        if self._notifications_enabled:
            self._notification_token = self._client.register_notification_callback(
                self._handle_notification
            )
            _LOGGER.debug("Registered notification callback for %s", self._attr_name)

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity is being removed."""
        if self._notification_token:
            self._client.unregister_notification_callback(self._notification_token)
            _LOGGER.debug("Unregistered notification callback for %s", self._attr_name)

    @callback
    def _handle_notification(self, notification: Dict[str, Any]) -> None:
        """Handle incoming notifications from nymea."""
        method = notification.get("method") or notification.get("notification")
        
        # Check if this is a power balance related notification
        if method and ("Energy" in method or "PowerBalance" in method or "Power" in method):
            _LOGGER.debug("Power balance notification received for %s: %s", self._attr_name, method)
            
            # Extract params from notification
            params = notification.get("params", {})
            if not params:
                params = notification  # Sometimes params are at root level
            
            # Update the sensor value if our key is present
            if self._sensor_config["key"] in params:
                new_value = params[self._sensor_config["key"]]
                if new_value != self._attr_native_value:
                    self._attr_native_value = new_value
                    self.async_write_ha_state()
                    _LOGGER.debug("Updated %s from notification: %s", self._attr_name, new_value)

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        if self.coordinator.data and self._sensor_config["key"] in self.coordinator.data:
            return self.coordinator.data[self._sensor_config["key"]]
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success
