"""Leaflet HEMS integration for Home Assistant."""

import asyncio
import logging
from datetime import timedelta
from typing import Optional, Dict, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .sensor import POWER_BALANCE_SENSORS

from .const import (
    DOMAIN,
    NAME,
    VERSION,
    CONF_HOST,
    CONF_PORT,
    CONF_NYMEA_UUID,
    CONF_NYMEA_NAME,
    CONF_NYMEA_TOKEN,
)
from .client import NymeaClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]  # We will add more platforms later


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Leaflet HEMS from a config entry.

    This creates a persistent NymeaClient and stores it in hass.data[DOMAIN][entry_id]
    so other parts of the integration can reuse the same TCP+TLS connection.
    """
    _LOGGER.info("Setting up Leaflet HEMS integration for %s", entry.title)

    host = entry.data.get(CONF_HOST)
    port = entry.data.get(CONF_PORT)
    nymea_uuid = entry.data.get(CONF_NYMEA_UUID)
    nymea_name = entry.data.get(CONF_NYMEA_NAME)
    token = entry.data.get(CONF_NYMEA_TOKEN)

    if not host or not nymea_uuid:
        _LOGGER.error("Missing host or nymea_uuid in config entry")
        return False

    # Ensure hass.data structure
    hass.data.setdefault(DOMAIN, {})

    # Create and connect a persistent NymeaClient for this entry
    nymea_client = NymeaClient()
    introspection_data = None
    notifications_enabled = False
    
    try:
        await nymea_client.connect(host, port)
        # Perform an initial hello on the persistent connection to warm it up.
        # The earlier handshake during config flow provided details already; this hello
        # will also validate the connection and keep the session ready.
        hello_params = await nymea_client.hello()
        
        # Set token and perform new handshake if token is available
        if token:
            nymea_client.update_token(token)
            # Perform new handshake with token
            hello_params_with_token = await nymea_client.hello()
        
        # Start reader loop for notifications and responses
        await nymea_client.start_reader_loop()
        
        # Skip introspection to avoid buffer overflow issues
        # Just enable notifications directly for Energy namespace
        try:
            notify_response = await nymea_client.send_request_with_response(
                "JSONRPC.SetNotificationStatus", 
                {"namespaces": ["Energy"]},
                timeout=5.0
            )
            if notify_response.get("status") == "success":
                notifications_enabled = True
                _LOGGER.info("Energy notifications enabled successfully")
            else:
                _LOGGER.warning("Failed to enable notifications: %s", notify_response.get("error"))
        except Exception as e:
            _LOGGER.warning("Error enabling notifications: %s", e)
            
    except Exception as exc:
        _LOGGER.warning("Couldn't establish persistent connection to %s:%s: %s", host, port, exc)
        # Still continue — some setups may not need a persistent connection immediately
        # Return False if you prefer to abort setup on connection failure.
        # For now we proceed and store the client (maybe disconnected) so platforms can try later.

    # Create coordinator for managing data updates
    coordinator = LeafletHEMSCoordinator(hass, nymea_client, nymea_name)
    
    # Store the client, coordinator and token in hass.data for reuse
    hass.data[DOMAIN][entry.entry_id] = {
        "client": nymea_client,
        "coordinator": coordinator,
        "token": token,
        "host": host,
        "port": port,
        "uuid": nymea_uuid,
        "name": nymea_name,
        "introspection": introspection_data,
        "notifications_enabled": notifications_enabled,
    }

    # Register coordinator notification callback for event-driven updates
    if notifications_enabled:
        try:
            token_cb = nymea_client.register_notification_callback(coordinator._handle_notification)
            hass.data[DOMAIN][entry.entry_id]["notification_token"] = token_cb
        except Exception as e:
            _LOGGER.warning("Failed to register notification callback: %s", e)


    # Forward entry setups to platforms (sensor)
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception as exc:
        _LOGGER.warning("Failed to forward entry setups for %s: %s", entry.entry_id, exc)

    # Add device to device registry
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, nymea_uuid)},
        manufacturer="Consolinno",
        name=nymea_name or f"{NAME} {nymea_uuid[:8]}",
        model="Leaflet HEMS",
        sw_version=VERSION,
    )

    _LOGGER.info("Leaflet HEMS integration for %s setup complete", entry.title)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and close the persistent NymeaClient."""
    _LOGGER.info("Unloading Leaflet HEMS integration for %s", entry.title)

    entry_data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if entry_data:
        client: Optional[NymeaClient] = entry_data.get("client")
        # Unregister notification callback if present
        token = entry_data.get("notification_token")
        if token and client:
            try:
                client.unregister_notification_callback(token)
            except Exception as exc:
                _LOGGER.warning("Failed to unregister notification callback for %s: %s", entry.title, exc)

        if client:
            try:
                await client.close()
            except Exception as exc:
                _LOGGER.warning("Error closing NymeaClient for %s: %s", entry.title, exc)

    # If you have platforms to unload, do that here (currently none)
    # unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    # if unload_ok:
    #     hass.data[DOMAIN].pop(entry.entry_id, None)
    return True


class LeafletHEMSCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the Leaflet HEMS device."""

    def __init__(self, hass: HomeAssistant, client: NymeaClient, nymea_name: str):
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{NAME} {nymea_name}",
            update_interval=None,
        )
        self.client = client
        self.nymea_name = nymea_name

        # Keys from sensor definitions that we care about for notifications
        self._keys = {s["key"] for s in POWER_BALANCE_SENSORS}
        self._data: Dict[str, Any] = {}

    async def _async_update_data(self):
        """Fetch data from Leaflet HEMS (used for initial fetch/fallback)."""
        try:
            response = await self.client.send_request_with_response("Energy.GetPowerBalance", timeout=10.0)
            
            if response.get("status") == "success" and response.get("params"):
                return response["params"]
            else:
                _LOGGER.warning("Energy.GetPowerBalance failed: %s", response.get("error"))
                return {}
                
        except Exception as e:
            _LOGGER.error("Error fetching power balance data: %s", e)
            raise

    @callback
    def _handle_notification(self, notification: Dict[str, Any]) -> None:
        """Handle notifications from the Nymea client and push updates to the coordinator."""
        method = notification.get("method") or notification.get("notification")
        if not method or ("Energy" not in method and "PowerBalance" not in method and "Power" not in method):
            return

        params = notification.get("params", {}) or notification
        # Merge into existing coordinator data
        new_data = dict(self.data or {})
        changed = False
        for k in self._keys:
            if k in params:
                if new_data.get(k) != params[k]:
                    new_data[k] = params[k]
                    changed = True
        if changed:
            self.async_set_updated_data(new_data)
