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
