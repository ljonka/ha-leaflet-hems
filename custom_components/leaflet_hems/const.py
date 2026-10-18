"""Constants for the Leaflet HEMS integration."""

from __future__ import annotations

# Base constants
DOMAIN = "leaflet_hems"
NAME = "Leaflet HEMS"
VERSION = "0.2.0"

# Configuration
CONF_HOST = "host"
CONF_PORT = "port"
CONF_NYMEA_UUID = "nymea_uuid"
CONF_NYMEA_NAME = "nymea_name"
CONF_NYMEA_TOKEN = "nymea_token"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

# Defaults
DEFAULT_NAME = "Leaflet HEMS"
DEFAULT_PORT = 2222
DEFAULT_LOCALE = "de_DE"

# Nymea JSON-RPC
RPC_HELLO_METHOD = "JSONRPC.Hello"
RPC_AUTHENTICATE_METHOD = "JSONRPC.Authenticate"
RPC_KEEPALIVE_METHOD = "JSONRPC.KeepAlive"
RPC_SET_NOTIFICATION_STATUS = "JSONRPC.SetNotificationStatus"

# Notification namespaces.  The HEMS/energy experience emits the power
# balance events, Hems emits configuration events and Integrations emits
# state changes for the individual things.
NOTIFICATION_NAMESPACES = ["Energy", "Hems", "Integrations"]

# HEMS API methods
HEMS_GET_BATTERY_CONFIGS = "Hems.GetBatteryConfigurations"
HEMS_GET_PV_CONFIGS = "Hems.GetPvConfigurations"

# Energy API methods
ENERGY_GET_POWER_BALANCE = "Energy.GetPowerBalance"
ENERGY_GET_ROOT_METER = "Energy.GetRootMeter"

# Integrations API methods
INTEGRATIONS_GET_THINGS = "Integrations.GetThings"
INTEGRATIONS_GET_THING_CLASSES = "Integrations.GetThingClasses"
INTEGRATIONS_GET_STATE_TYPES = "Integrations.GetStateTypes"
INTEGRATIONS_GET_STATE_VALUES = "Integrations.GetStateValues"

# HEMS events
HEMS_BATTERY_ADDED = "Hems.BatteryConfigurationAdded"
HEMS_BATTERY_CHANGED = "Hems.BatteryConfigurationChanged"
HEMS_BATTERY_REMOVED = "Hems.BatteryConfigurationRemoved"
HEMS_PV_ADDED = "Hems.PvConfigurationAdded"
HEMS_PV_CHANGED = "Hems.PvConfigurationChanged"
HEMS_PV_REMOVED = "Hems.PvConfigurationRemoved"
HEMS_BATTERY_CONFIG_EVENTS = {
    HEMS_BATTERY_ADDED,
    HEMS_BATTERY_CHANGED,
    HEMS_BATTERY_REMOVED,
}
HEMS_PV_CONFIG_EVENTS = {HEMS_PV_ADDED, HEMS_PV_CHANGED, HEMS_PV_REMOVED}

# Energy events
ENERGY_POWER_BALANCE_CHANGED = "Energy.PowerBalanceChanged"
ENERGY_ROOT_METER_CHANGED = "Energy.RootMeterChanged"

# Integrations events
INTEGRATIONS_STATE_CHANGED = "Integrations.StateChanged"
INTEGRATIONS_THING_ADDED = "Integrations.ThingAdded"
INTEGRATIONS_THING_CHANGED = "Integrations.ThingChanged"
INTEGRATIONS_THING_REMOVED = "Integrations.ThingRemoved"

# Thing state names that are relevant for the grouped entities.
STATE_CURRENT_POWER = "currentPower"
STATE_TOTAL_ENERGY_CONSUMED = "totalEnergyConsumed"
STATE_TOTAL_ENERGY_PRODUCED = "totalEnergyProduced"
RELEVANT_STATE_NAMES = {
    STATE_CURRENT_POWER,
    STATE_TOTAL_ENERGY_CONSUMED,
    STATE_TOTAL_ENERGY_PRODUCED,
}

# Discovery constants
ZEROCONF_SERVICE_TYPE = "_jsonrpc._tcp.local."
ZEROCONF_NYMEA_MANUFACTURER = "nymea GmbH"
ZEROCONF_NAME_PATTERN = "*nymea*"

# Discovery TXT record keys
TXT_UUID = "uuid"
TXT_NAME = "name"
TXT_MANUFACTURER = "manufacturer"
TXT_SERVER_VERSION = "serverVersion"
TXT_SSL_ENABLED = "sslEnabled"
TXT_JSONRPC_VERSION = "jsonrpcVersion"

# Grouped device identifiers
GROUP_GRID = "grid"
GROUP_GENERATION = "generation"
GROUP_STORAGE = "storage"
GROUP_CONSUMPTION = "consumption"
