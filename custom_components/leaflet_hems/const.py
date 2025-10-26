"""Constants for Leaflet HEMS integration."""

# Base constants
DOMAIN = "leaflet_hems"
NAME = "Leaflet HEMS"
VERSION = "0.1.0"

# Configuration
CONF_HOST = "host"
CONF_PORT = "port"
CONF_NYMEA_UUID = "nymea_uuid"
CONF_NYMEA_NAME = "nymea_name"

# Defaults
DEFAULT_NAME = "Leaflet HEMS"
DEFAULT_PORT = 2222

# Additional config keys
CONF_NYMEA_TOKEN = "nymea_token"

# Nymea JSON-RPC
NYMDA_RPC_VERSION = "4.1" # From nymea documentation
RPC_HELLO_METHOD = "JSONRPC.Hello"
RPC_HELLO_LOCALE = "de_DE"
RPC_ID = 0

# HEMS API Methods
HEMS_GET_BATTERY_CONFIGS = "Hems.GetBatteryConfigurations"
HEMS_GET_PV_CONFIGS = "Hems.GetPvConfigurations"

# HEMS Events
HEMS_BATTERY_ADDED = "Hems.BatteryConfigurationAdded"
HEMS_BATTERY_CHANGED = "Hems.BatteryConfigurationChanged"
HEMS_BATTERY_REMOVED = "Hems.BatteryConfigurationRemoved"
HEMS_PV_ADDED = "Hems.PvConfigurationAdded"
HEMS_PV_CHANGED = "Hems.PvConfigurationChanged"
HEMS_PV_REMOVED = "Hems.PvConfigurationRemoved"

# Device Types
DEVICE_TYPE_BATTERY = "battery"
DEVICE_TYPE_INVERTER = "inverter"

# State type name mappings for sensors (API state type names to sensor property names)
BATTERY_STATE_MAPPINGS = {
    "batteryLevel": "batteryLevel",
    "chargingState": "chargingState",
    "currentPower": "currentPower",
    "soc": "batteryLevel",
    "chargeState": "chargingState",
    "dischargeState": "chargingState"
}

INVERTER_STATE_MAPPINGS = {
    "currentPower": "currentPower",
    "totalEnergyProduced": "totalEnergyProduced"
}

# Discovery constants
ZEROCONF_SERVICE_TYPE = "_jsonrpc._tcp.local."
ZEROCONF_NYMEA_MANUFACTURER = "nymea GmbH"
ZEROCONF_NAME_PATTERN = "nymea"

# Discovery TXT record keys
TXT_UUID = "uuid"
TXT_NAME = "name"
TXT_MANUFACTURER = "manufacturer"
TXT_SERVER_VERSION = "serverVersion"
TXT_SSL_ENABLED = "sslEnabled"
TXT_JSONRPC_VERSION = "jsonrpcVersion"
