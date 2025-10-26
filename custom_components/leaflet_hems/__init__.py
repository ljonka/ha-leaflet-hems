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
    HEMS_GET_BATTERY_CONFIGS,
    HEMS_GET_PV_CONFIGS,
    HEMS_BATTERY_ADDED,
    HEMS_BATTERY_CHANGED,
    HEMS_BATTERY_REMOVED,
    HEMS_PV_ADDED,
    HEMS_PV_CHANGED,
    HEMS_PV_REMOVED,
    DEVICE_TYPE_BATTERY,
    DEVICE_TYPE_INVERTER,
    BATTERY_STATE_MAPPINGS,
    INVERTER_STATE_MAPPINGS,
)
from .client import NymeaClient

_LOGGER = logging.getLogger(__name__)

# Create a quieter logger for the coordinator to avoid "Manually updated" debug messages
_COORDINATOR_LOGGER = logging.getLogger(f"{__name__}.coordinator")
_COORDINATOR_LOGGER.setLevel(logging.WARNING)

PLATFORMS = ["sensor"]  # We will add more platforms later


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Leaflet HEMS from a config entry.

    This creates a persistent NymeaClient and stores it in hass.data[DOMAIN][entry_id]
    so other parts of the integration can reuse the same TCP+TLS connection.
    """

    host = entry.data.get(CONF_HOST)
    port = entry.data.get(CONF_PORT)
    nymea_uuid = entry.data.get(CONF_NYMEA_UUID)
    nymea_name = entry.data.get(CONF_NYMEA_NAME)
    token = entry.data.get(CONF_NYMEA_TOKEN)

    if not host or not nymea_uuid:
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
        
        # Start keepalive loop to monitor connection health
        await nymea_client.start_keepalive()
        
        # Skip introspection to avoid buffer overflow issues
        # Enable notifications for Energy and Integrations namespaces
        try:
            notify_response = await nymea_client.send_request_with_response(
                "JSONRPC.SetNotificationStatus", 
                {"namespaces": ["Energy", "Integrations"]},
                timeout=5.0
            )
            if notify_response.get("status") == "success":
                notifications_enabled = True
        except Exception:
            pass
            
    except Exception:
        # Still continue — some setups may not need a persistent connection immediately
        # Return False if you prefer to abort setup on connection failure.
        # For now we proceed and store the client (maybe disconnected) so platforms can try later.
        pass

    # Create coordinator for managing data updates
    coordinator = LeafletHEMSCoordinator(hass, nymea_client, nymea_name)
    # Start coordinator background tasks (monitor root meter)
    await coordinator.async_start()
    
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
        except Exception:
            pass

    # Register coordinator reconnection callback to refresh data after reconnect
    try:
        nymea_client.register_reconnection_callback(coordinator._handle_reconnection)
    except Exception:
        pass

    # Register coordinator disconnection callback to mark all sensors as unavailable
    try:
        nymea_client.register_disconnection_callback(coordinator._handle_disconnection)
    except Exception:
        pass


    # Forward entry setups to platforms (sensor)
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        pass

    # Add device to device registry
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, nymea_uuid)},
        manufacturer="Consolinno Energy GmbH",
        name=nymea_name or f"{NAME} {nymea_uuid[:8]}",
        model="Leaflet HEMS",
        sw_version=VERSION,
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and close the persistent NymeaClient."""

    entry_data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if entry_data:
        client: Optional[NymeaClient] = entry_data.get("client")
        # Unregister notification callback if present
        token = entry_data.get("notification_token")
        if token and client:
            try:
                client.unregister_notification_callback(token)
            except Exception:
                pass

        if client:
            try:
                await client.close()
            except Exception:
                pass

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
            _COORDINATOR_LOGGER,
            name=f"{NAME} {nymea_name}",
            update_interval=None,
        )
        self.client = client
        self.nymea_name = nymea_name

        # Keys from sensor definitions that we care about for notifications
        self._keys = {s["key"] for s in POWER_BALANCE_SENSORS}
        self._data: Dict[str, Any] = {}
        # Root meter tracking
        self._root_meter_thing_id: Optional[str] = None
        self._root_meter_thing_class_id: Optional[str] = None
        self._root_meter_notification_token: Optional[str] = None
        self._root_meter_state_keys = {"totalEnergyConsumed", "totalEnergyProduced"}
        self._root_meter_state_types: Dict[str, str] = {}  # state_type_id -> state_name mapping

        # Battery and inverter tracking
        self._battery_configs: Dict[str, Dict[str, Any]] = {}  # battery_thing_id -> config
        self._pv_configs: Dict[str, Dict[str, Any]] = {}  # pv_thing_id -> config
        self._battery_states: Dict[str, Dict[str, Any]] = {}  # battery_thing_id -> states
        self._pv_states: Dict[str, Dict[str, Any]] = {}  # pv_thing_id -> states
        self._battery_state_types: Dict[str, Dict[str, str]] = {}  # battery_thing_id -> state_type_id -> state_name
        self._pv_state_types: Dict[str, Dict[str, str]] = {}  # pv_thing_id -> state_type_id -> state_name
        self._battery_notification_tokens: Dict[str, str] = {}  # battery_thing_id -> token
        self._pv_notification_tokens: Dict[str, str] = {}  # pv_thing_id -> token
        self._aggregated_data: Dict[str, float] = {}  # Aggregated values for groups

        # Hardcoded state name mappings (API returns underscore_separated, sensors expect camelCase)
        self._expected_battery_state_names = {
            "battery_level": "batteryLevel",
            "charging_state": "chargingState",
            "current_power": "currentPower"
        }
        self._expected_pv_state_names = {
            "current_power": "currentPower",
            "total_energy_produced": "totalEnergyProduced"
        }

    async def async_start(self) -> None:
        """Start background tasks: query root meter and subscribe to its changes."""
        await self._update_root_meter()
        await self._update_battery_configs()
        await self._update_pv_configs()
        
        # Force an initial data update to populate values
        try:
            initial_data = await self._async_update_data()
            if initial_data:
                self.async_set_updated_data(initial_data)
        except Exception:
            pass

    async def _update_root_meter(self) -> None:
        """Query Energy.GetRootMeter and subscribe/unsubscribe to its states."""
        new_root_id = None
        
        # Retry logic with exponential backoff
        max_retries = 3
        base_delay = 1.0
        
        for attempt in range(max_retries):
            try:
                timeout = 15.0 + (attempt * 5.0)  # Increase timeout with each retry
                response = await self.client.send_request_with_response("Energy.GetRootMeter", timeout=timeout)
                
                if response.get("status") == "success":
                    params = response.get("params", {})
                    # Try both possible key formats for rootMeterThingId
                    new_root_id = params.get("o:rootMeterThingId") or params.get("rootMeterThingId")
                    break  # Success, exit retry loop
                else:
                    # Don't retry on non-success responses from the server
                    break
                    
            except asyncio.TimeoutError:
                delay = base_delay * (2 ** attempt)  # Exponential backoff
                if attempt < max_retries - 1:
                    await asyncio.sleep(delay)
                    
            except Exception:
                # For non-timeout exceptions, don't retry immediately
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    await asyncio.sleep(delay)

        if new_root_id == self._root_meter_thing_id:
            return  # No change

        # Unsubscribe from previous root meter states
        if self._root_meter_notification_token:
            self.client.unregister_notification_callback(self._root_meter_notification_token)
            self._root_meter_notification_token = None

        self._root_meter_thing_id = new_root_id

        if new_root_id:
            # Subscribe to state changes for the new root meter
            self._root_meter_notification_token = self.client.register_notification_callback(
                self._handle_root_meter_notification
            )
            
            # Fetch root meter thing class ID and state types
            await self._fetch_root_meter_state_types()
            
            # Fetch initial root meter state values
            await self._fetch_root_meter_states()
        else:
            # Clear cached data when no root meter
            self._root_meter_thing_class_id = None
            self._root_meter_state_types.clear()

    async def _fetch_root_meter_state_types(self) -> None:
        """Fetch state types for the root meter thing to enable state type ID to name mapping."""
        if not self._root_meter_thing_id:
            return
            
        try:
            # First, try to get all things to find the root meter thing and its class ID
            things_response = await self.client.send_request_with_response(
                "Integrations.GetThings",
                {},
                timeout=10.0
            )
            
            if things_response.get("status") == "success" and things_response.get("params", {}).get("things"):
                things = things_response["params"]["things"]
                
                # Find our root meter thing to get its thingClassId
                root_meter_thing_class_id = None
                for thing in things:
                    if thing.get("id") == self._root_meter_thing_id:
                        root_meter_thing_class_id = thing.get("thingClassId")
                        break
                
                if not root_meter_thing_class_id:
                    return
                    
                self._root_meter_thing_class_id = root_meter_thing_class_id
                
                # Now get the state types for this thing class
                state_types_response = await self.client.send_request_with_response(
                    "Integrations.GetStateTypes",
                    {"thingClassId": root_meter_thing_class_id},
                    timeout=10.0
                )
                
                if state_types_response.get("status") == "success" and state_types_response.get("params", {}).get("stateTypes"):
                    state_types = state_types_response["params"]["stateTypes"]
                    
                    # Build mapping of state type ID to state name
                    self._root_meter_state_types.clear()
                    for state_type in state_types:
                        # Use the 'r:id' field as the state type ID and 'name' as the state name
                        state_type_id = state_type.get("r:id") or state_type.get("id")
                        state_name = state_type.get("name")
                        if state_type_id and state_name:
                            self._root_meter_state_types[state_type_id] = state_name
                
        except Exception:
            pass

    async def _fetch_specific_root_meter_state(self, thing_id: str, state_type_id: str, new_value: Any) -> None:
        """Fetch a specific root meter state to get its name and update data accordingly."""
        try:
            # Get the specific state value to determine its name
            response = await self.client.send_request_with_response(
                "Integrations.GetStateValue",
                {"thingId": thing_id, "stateTypeId": state_type_id},
                timeout=5.0
            )
            
            if response.get("status") == "success" and response.get("params"):
                # For now, we'll trigger a full state refresh to get all state names
                # This is similar to the C++ approach where we have the stateType and can check its name
                await self._fetch_root_meter_states()
                
        except Exception:
            pass

    async def _fetch_root_meter_states(self) -> None:
        """Fetch current state values for the root meter."""
        if not self._root_meter_thing_id:
            return
            
        try:
            # Fetch states for the root meter using the correct API
            response = await self.client.send_request_with_response(
                "Integrations.GetStateValues", 
                {"thingId": self._root_meter_thing_id},
                timeout=10.0
            )
            
            if response.get("status") == "success" and response.get("params", {}).get("values"):
                values = response["params"]["values"]
                new_data = dict(self.data or {})
                updated = False
                
                # Map root meter states to power balance keys using state type IDs
                for state in values:
                    state_type_id = state.get("stateTypeId")
                    value = state.get("value")
                    
                    if state_type_id and value is not None:
                        # Use state type mapping to get state name
                        state_name = self._root_meter_state_types.get(state_type_id)
                        
                        if state_name == "totalEnergyConsumed":
                            new_data["totalAcquisition"] = value
                            updated = True
                        elif state_name == "totalEnergyProduced":
                            new_data["totalReturn"] = value
                            updated = True
                
                if updated:
                    self.async_set_updated_data(new_data)
                
        except Exception:
            pass

    @callback
    def _handle_root_meter_notification(self, notification: Dict[str, Any]) -> None:
        """Handle state change notifications for the root meter."""
        method = notification.get("method") or notification.get("notification")
        if not method or "Integrations.StateChanged" not in method:
            return
        
        params = notification.get("params", {})
        thing_id = params.get("thingId")
        
        if thing_id != self._root_meter_thing_id:
            return
            
        # Get state type ID to determine if this is a root meter state we care about
        state_type_id = params.get("stateTypeId")
        value = params.get("value")
        
        if value is None:
            return
            
        # Use state type mapping to get state name if available
        state_name = self._root_meter_state_types.get(state_type_id)
        
        if state_name:
            # Handle real-time updates for states we care about
            if state_name in self._root_meter_state_keys:
                new_data = dict(self.data or {})
                updated = False
                
                if state_name == "totalEnergyConsumed":
                    old_value = new_data.get("totalAcquisition")
                    if old_value != value:
                        new_data["totalAcquisition"] = value
                        updated = True
                elif state_name == "totalEnergyProduced":
                    old_value = new_data.get("totalReturn")
                    if old_value != value:
                        new_data["totalReturn"] = value
                        updated = True
                
                if updated:
                    self.async_set_updated_data(new_data)
        else:
            # Fallback: if we don't have state type mapping, fetch the specific state to get its name
            self.hass.async_create_task(self._fetch_specific_root_meter_state(thing_id, state_type_id, value))

    async def _async_update_data(self):
        """Fetch data from Leaflet HEMS (used for initial fetch/fallback)."""
        data = {}
        
        # Ensure we have a healthy connection before attempting data fetch
        if not await self.client.ensure_connected():
            _LOGGER.warning("Cannot fetch data - no healthy connection to Nymea server")
            return data
        
        # First, try to get power balance data (excluding totalAcquisition/totalReturn)
        try:
            response = await self.client.send_request_with_response("Energy.GetPowerBalance", timeout=10.0)
            
            if response.get("status") == "success" and response.get("params"):
                power_balance_data = response["params"]
                
                # Only include non-root-meter data from power balance
                for key in power_balance_data:
                    if key not in ["totalAcquisition", "totalReturn"]:
                        data[key] = power_balance_data[key]
                
        except Exception as e:
            _LOGGER.warning("Failed to fetch power balance data: %s", e)
        
        # Always get totalAcquisition and totalReturn from root meter if available
        if self._root_meter_thing_id:
            # Ensure state type mapping is available
            if not self._root_meter_state_types:
                await self._fetch_root_meter_state_types()
            
            try:
                # Fetch root meter states using correct API
                response = await self.client.send_request_with_response(
                    "Integrations.GetStateValues", 
                    {"thingId": self._root_meter_thing_id},
                    timeout=10.0
                )
                
                if response.get("status") == "success" and response.get("params", {}).get("values"):
                    values = response["params"]["values"]
                    
                    # Map root meter states to power balance keys using state type IDs
                    for state in values:
                        state_type_id = state.get("stateTypeId")
                        value = state.get("value")
                        
                        if state_type_id and value is not None:
                            # Use state type mapping to get state name
                            state_name = self._root_meter_state_types.get(state_type_id)
                            
                            if state_name == "totalEnergyConsumed":
                                data["totalAcquisition"] = value
                            elif state_name == "totalEnergyProduced":
                                data["totalReturn"] = value
                    
            except Exception as e:
                _LOGGER.warning("Failed to fetch root meter states: %s", e)
        
        return data

    @callback
    def _handle_notification(self, notification: Dict[str, Any]) -> None:
        """Handle notifications from the Nymea client and push updates to the coordinator."""
        method = notification.get("method") or notification.get("notification")
        
        if not method:
            return
            
        # Handle root meter change notification
        if method == "Energy.RootMeterChanged":
            # Schedule root meter update in the event loop
            self.hass.async_create_task(self._update_root_meter())
            return
            
        # Handle root meter state changes
        if method == "Integrations.StateChanged":
            params = notification.get("params", {})
            thing_id = params.get("thingId")
            if thing_id and thing_id == self._root_meter_thing_id:
                # Process the state change directly instead of fetching all states
                self._handle_root_meter_notification(notification)
                return
            
        if "Energy" not in method and "PowerBalance" not in method and "Power" not in method:
            return

        params = notification.get("params", {}) or notification
        
        # Periodically check root meter status if we haven't found one yet
        if not self._root_meter_thing_id and method == "Energy.PowerBalanceChanged":
            self.hass.async_create_task(self._update_root_meter())
        
        # Merge into existing coordinator data - EXCLUDE totalAcquisition/totalReturn from PowerBalance notifications
        new_data = dict(self.data or {})
        changed = False
        
        for k in self._keys:
            if k in params:
                # Skip totalAcquisition and totalReturn from PowerBalance notifications - these should only come from root meter
                if k in ["totalAcquisition", "totalReturn"] and "PowerBalance" in method:
                    continue
                    
                old_value = new_data.get(k)
                new_value = params[k]
                if old_value != new_value:
                    new_data[k] = new_value
                    changed = True
        
        if changed:
            self.async_set_updated_data(new_data)

    async def _update_battery_configs(self, force_refresh: bool = False) -> None:
        """Query Hems.GetBatteryConfigurations and subscribe to battery state changes.
        
        Args:
            force_refresh: If True, refetch states for all batteries even if already configured.
        """
        try:
            response = await self.client.send_request_with_response(HEMS_GET_BATTERY_CONFIGS, timeout=10.0)

            if response.get("status") == "success" and response.get("params", {}).get("batteryConfigurations"):
                battery_configs = response["params"]["batteryConfigurations"]
                _LOGGER.info("Found %d battery configurations", len(battery_configs))
                current_battery_ids = set(self._battery_configs.keys())
                new_battery_ids = set()

                for config in battery_configs:
                    battery_thing_id = config.get("batteryThingId")
                    _LOGGER.debug("Processing battery config for thingId: %s", battery_thing_id)
                    if battery_thing_id:
                        new_battery_ids.add(battery_thing_id)
                        is_new = battery_thing_id not in self._battery_configs
                        
                        # Always update the configuration
                        self._battery_configs[battery_thing_id] = config
                        
                        if is_new:
                            # New battery configuration - full setup
                            await self._fetch_battery_state_types(battery_thing_id)
                            await self._subscribe_to_battery_states(battery_thing_id)
                            await self._fetch_battery_states(battery_thing_id)
                            _LOGGER.info("Added battery configuration: %s", battery_thing_id)
                        elif force_refresh:
                            # Existing battery but force refresh requested (e.g., after reconnection)
                            # Refetch state types and states to repopulate cleared data
                            await self._fetch_battery_state_types(battery_thing_id)
                            await self._fetch_battery_states(battery_thing_id)
                            _LOGGER.info("Refreshed battery states after reconnection: %s", battery_thing_id)

                # Remove batteries that are no longer present
                removed_battery_ids = current_battery_ids - new_battery_ids
                for battery_id in removed_battery_ids:
                    await self._unsubscribe_from_battery_states(battery_id)
                    self._battery_configs.pop(battery_id, None)
                    self._battery_states.pop(battery_id, None)
                    self._battery_state_types.pop(battery_id, None)
                    _LOGGER.info("Removed battery configuration: %s", battery_id)

                _LOGGER.info("Battery configurations updated. New batteries: %s, Removed batteries: %s", new_battery_ids, removed_battery_ids)

                # Update aggregated data - this also triggers sensor updates via coordinator data
                await self._update_aggregated_data()

        except Exception as e:
            _LOGGER.warning("Failed to fetch battery configurations: %s", e)

    async def _update_pv_configs(self, force_refresh: bool = False) -> None:
        """Query Hems.GetPvConfigurations and subscribe to PV state changes.
        
        Args:
            force_refresh: If True, refetch states for all PVs even if already configured.
        """
        try:
            response = await self.client.send_request_with_response(HEMS_GET_PV_CONFIGS, timeout=10.0)
            
            if response.get("status") == "success" and response.get("params", {}).get("pvConfigurations"):
                pv_configs = response["params"]["pvConfigurations"]
                current_pv_ids = set(self._pv_configs.keys())
                new_pv_ids = set()
                
                for config in pv_configs:
                    pv_thing_id = config.get("pvThingId")
                    if pv_thing_id:
                        new_pv_ids.add(pv_thing_id)
                        is_new = pv_thing_id not in self._pv_configs
                        
                        # Always update the configuration
                        self._pv_configs[pv_thing_id] = config
                        
                        if is_new:
                            # New PV configuration - full setup
                            await self._fetch_pv_state_types(pv_thing_id)
                            await self._subscribe_to_pv_states(pv_thing_id)
                            await self._fetch_pv_states(pv_thing_id)
                            _LOGGER.info("Added PV configuration: %s", pv_thing_id)
                        elif force_refresh:
                            # Existing PV but force refresh requested (e.g., after reconnection)
                            # Refetch state types and states to repopulate cleared data
                            await self._fetch_pv_state_types(pv_thing_id)
                            await self._fetch_pv_states(pv_thing_id)
                            _LOGGER.info("Refreshed PV states after reconnection: %s", pv_thing_id)
                
                # Remove PVs that are no longer present
                removed_pv_ids = current_pv_ids - new_pv_ids
                for pv_id in removed_pv_ids:
                    await self._unsubscribe_from_pv_states(pv_id)
                    self._pv_configs.pop(pv_id, None)
                    self._pv_states.pop(pv_id, None)
                    self._pv_state_types.pop(pv_id, None)
                    _LOGGER.info("Removed PV configuration: %s", pv_id)
                
                # Update aggregated data - this also triggers sensor updates via coordinator data
                await self._update_aggregated_data()

        except Exception as e:
            _LOGGER.warning("Failed to fetch PV configurations: %s", e)

    async def _fetch_battery_state_types(self, battery_thing_id: str) -> None:
        """Fetch state types for a battery thing."""
        try:
            # Get the battery thing to find its class ID
            things_response = await self.client.send_request_with_response(
                "Integrations.GetThings",
                {},
                timeout=10.0
            )
            
            if things_response.get("status") == "success" and things_response.get("params", {}).get("things"):
                things = things_response["params"]["things"]
                
                # Find our battery thing to get its thingClassId
                battery_thing_class_id = None
                for thing in things:
                    if thing.get("id") == battery_thing_id:
                        battery_thing_class_id = thing.get("thingClassId")
                        break
                
                if not battery_thing_class_id:
                    return
                
                # Get state types for this battery thing class
                state_types_response = await self.client.send_request_with_response(
                    "Integrations.GetStateTypes",
                    {"thingClassId": battery_thing_class_id},
                    timeout=10.0
                )
                
                if state_types_response.get("status") == "success" and state_types_response.get("params", {}).get("stateTypes"):
                    state_types = state_types_response["params"]["stateTypes"]
                    
                    # Build mapping of state type ID to state name
                    self._battery_state_types[battery_thing_id] = {}
                    for state_type in state_types:
                        state_type_id = state_type.get("r:id") or state_type.get("id")
                        state_name = state_type.get("name")
                        if state_type_id and state_name:
                            self._battery_state_types[battery_thing_id][state_type_id] = state_name
                
        except Exception as e:
            _LOGGER.warning("Failed to fetch battery state types for %s: %s", battery_thing_id, e)

    async def _fetch_pv_state_types(self, pv_thing_id: str) -> None:
        """Fetch state types for a PV thing."""
        try:
            # Get the PV thing to find its class ID
            things_response = await self.client.send_request_with_response(
                "Integrations.GetThings",
                {},
                timeout=10.0
            )
            
            if things_response.get("status") == "success" and things_response.get("params", {}).get("things"):
                things = things_response["params"]["things"]
                
                # Find our PV thing to get its thingClassId
                pv_thing_class_id = None
                for thing in things:
                    if thing.get("id") == pv_thing_id:
                        pv_thing_class_id = thing.get("thingClassId")
                        break
                
                if not pv_thing_class_id:
                    return
                
                # Get state types for this PV thing class
                state_types_response = await self.client.send_request_with_response(
                    "Integrations.GetStateTypes",
                    {"thingClassId": pv_thing_class_id},
                    timeout=10.0
                )
                
                if state_types_response.get("status") == "success" and state_types_response.get("params", {}).get("stateTypes"):
                    state_types = state_types_response["params"]["stateTypes"]
                    
                    # Build mapping of state type ID to state name
                    self._pv_state_types[pv_thing_id] = {}
                    for state_type in state_types:
                        state_type_id = state_type.get("r:id") or state_type.get("id")
                        state_name = state_type.get("name")
                        if state_type_id and state_name:
                            self._pv_state_types[pv_thing_id][state_type_id] = state_name
                
        except Exception as e:
            _LOGGER.warning("Failed to fetch PV state types for %s: %s", pv_thing_id, e)

    async def _subscribe_to_battery_states(self, battery_thing_id: str) -> None:
        """Subscribe to state changes for a battery thing."""
        if battery_thing_id not in self._battery_notification_tokens:
            token = self.client.register_notification_callback(
                lambda notification: self._handle_battery_notification(notification, battery_thing_id)
            )
            self._battery_notification_tokens[battery_thing_id] = token

    async def _subscribe_to_pv_states(self, pv_thing_id: str) -> None:
        """Subscribe to state changes for a PV thing."""
        if pv_thing_id not in self._pv_notification_tokens:
            token = self.client.register_notification_callback(
                lambda notification: self._handle_pv_notification(notification, pv_thing_id)
            )
            self._pv_notification_tokens[pv_thing_id] = token

    async def _unsubscribe_from_battery_states(self, battery_thing_id: str) -> None:
        """Unsubscribe from state changes for a battery thing."""
        token = self._battery_notification_tokens.pop(battery_thing_id, None)
        if token:
            self.client.unregister_notification_callback(token)

    async def _unsubscribe_from_pv_states(self, pv_thing_id: str) -> None:
        """Unsubscribe from state changes for a PV thing."""
        token = self._pv_notification_tokens.pop(pv_thing_id, None)
        if token:
            self.client.unregister_notification_callback(token)

    async def _fetch_battery_states(self, battery_thing_id: str) -> None:
        """Fetch current state values for a battery thing."""
        try:
            _LOGGER.debug("Fetching battery states for %s", battery_thing_id)
            response = await self.client.send_request_with_response(
                "Integrations.GetStateValues",
                {"thingId": battery_thing_id},
                timeout=10.0
            )

            if response.get("status") == "success" and response.get("params", {}).get("values"):
                values = response["params"]["values"]
                _LOGGER.debug("Retrieved %d raw state values for battery %s", len(values), battery_thing_id)

                # Initialize battery states if not exists
                if battery_thing_id not in self._battery_states:
                    self._battery_states[battery_thing_id] = {}

                # Update battery states - use constant mappings directly
                for state in values:
                    state_type_id = state.get("stateTypeId")
                    value = state.get("value")

                    if state_type_id and value is not None:
                        # Get state type name from state type mapping
                        state_type_name = self._battery_state_types.get(battery_thing_id, {}).get(state_type_id)
                        if state_type_name and state_type_name in BATTERY_STATE_MAPPINGS:
                            # Use the constant mapping value as the sensor name (already correct)
                            sensor_name = BATTERY_STATE_MAPPINGS[state_type_name]
                            old_value = self._battery_states[battery_thing_id].get(sensor_name)
                            self._battery_states[battery_thing_id][sensor_name] = value
                            _LOGGER.debug("Mapped battery state for %s: '%s' = %s (was: %s)",
                                        battery_thing_id, sensor_name, value, old_value)
                        else:
                            _LOGGER.debug("Ignoring battery state for %s: state_type_id=%s, name=%s, value=%s",
                                        battery_thing_id, state_type_id, state_type_name, value)

                _LOGGER.debug("Battery states after mapping for %s: %s", battery_thing_id, self._battery_states[battery_thing_id])
                # Update aggregated data
                await self._update_aggregated_data()

        except Exception as e:
            _LOGGER.warning("Failed to fetch battery states for %s: %s", battery_thing_id, e)

    async def _fetch_pv_states(self, pv_thing_id: str) -> None:
        """Fetch current state values for a PV thing."""
        try:
            _LOGGER.debug("Fetching PV states for %s", pv_thing_id)
            response = await self.client.send_request_with_response(
                "Integrations.GetStateValues",
                {"thingId": pv_thing_id},
                timeout=10.0
            )

            if response.get("status") == "success" and response.get("params", {}).get("values"):
                values = response["params"]["values"]
                _LOGGER.debug("Retrieved %d raw state values for PV %s", len(values), pv_thing_id)

                # Initialize PV states if not exists
                if pv_thing_id not in self._pv_states:
                    self._pv_states[pv_thing_id] = {}

                # Update PV states - use constant mappings directly
                for state in values:
                    state_type_id = state.get("stateTypeId")
                    value = state.get("value")

                    if state_type_id and value is not None:
                        # Get state type name from state type mapping
                        state_type_name = self._pv_state_types.get(pv_thing_id, {}).get(state_type_id)
                        if state_type_name and state_type_name in INVERTER_STATE_MAPPINGS:
                            # Use the constant mapping value as the sensor name (already correct)
                            sensor_name = INVERTER_STATE_MAPPINGS[state_type_name]
                            old_value = self._pv_states[pv_thing_id].get(sensor_name)
                            self._pv_states[pv_thing_id][sensor_name] = value
                            _LOGGER.debug("Mapped PV state for %s: '%s' = %s (was: %s)",
                                        pv_thing_id, sensor_name, value, old_value)
                        else:
                            _LOGGER.debug("Ignoring PV state for %s: state_type_id=%s, name=%s, value=%s",
                                        pv_thing_id, state_type_id, state_type_name, value)

                _LOGGER.debug("PV states after mapping for %s: %s", pv_thing_id, self._pv_states[pv_thing_id])
                # Update aggregated data
                await self._update_aggregated_data()

        except Exception as e:
            _LOGGER.warning("Failed to fetch PV states for %s: %s", pv_thing_id, e)

    @callback
    def _handle_battery_notification(self, notification: Dict[str, Any], battery_thing_id: str) -> None:
        """Handle state change notifications for a battery thing."""
        method = notification.get("method") or notification.get("notification")
        if not method or "Integrations.StateChanged" not in method:
            return

        params = notification.get("params", {})
        _LOGGER.debug("Battery notification received: %s", notification)
        thing_id = params.get("thingId")

        if thing_id != battery_thing_id:
            return

        state_type_id = params.get("stateTypeId")
        value = params.get("value")

        if value is None:
            return

        # Use state type mapping to get state name, then check if we want this state
        state_type_name = self._battery_state_types.get(battery_thing_id, {}).get(state_type_id)
        _LOGGER.debug("Battery state change for %s: state_type_id=%s, state_type_name='%s', value=%s",
                     battery_thing_id, state_type_id, state_type_name, value)

        if state_type_name and state_type_name in BATTERY_STATE_MAPPINGS:
            sensor_name = BATTERY_STATE_MAPPINGS[state_type_name]

            # Update battery state
            if battery_thing_id not in self._battery_states:
                self._battery_states[battery_thing_id] = {}

            old_value = self._battery_states[battery_thing_id].get(sensor_name)
            if old_value != value:
                self._battery_states[battery_thing_id][sensor_name] = value
                _LOGGER.debug("Updated battery state '%s' for %s: %s -> %s",
                             sensor_name, battery_thing_id, old_value, value)

                # Update aggregated data
                self.hass.async_create_task(self._update_aggregated_data())
        else:
            # Fetch state types if we don't have mapping
            _LOGGER.debug("Unknown battery state type %s for %s, refetching state types", state_type_id, battery_thing_id)
            self.hass.async_create_task(self._fetch_battery_state_types(battery_thing_id))

    @callback
    def _handle_pv_notification(self, notification: Dict[str, Any], pv_thing_id: str) -> None:
        """Handle state change notifications for a PV thing."""
        method = notification.get("method") or notification.get("notification")
        if not method or "Integrations.StateChanged" not in method:
            return

        params = notification.get("params", {})
        thing_id = params.get("thingId")

        if thing_id != pv_thing_id:
            return

        state_type_id = params.get("stateTypeId")
        value = params.get("value")

        if value is None:
            return

        # Use state type mapping to get state name, then check if we want this state
        state_type_name = self._pv_state_types.get(pv_thing_id, {}).get(state_type_id)

        if state_type_name and state_type_name in INVERTER_STATE_MAPPINGS:
            sensor_name = INVERTER_STATE_MAPPINGS[state_type_name]

            # Update PV state
            if pv_thing_id not in self._pv_states:
                self._pv_states[pv_thing_id] = {}

            old_value = self._pv_states[pv_thing_id].get(sensor_name)
            if old_value != value:
                self._pv_states[pv_thing_id][sensor_name] = value
                _LOGGER.debug("Updated PV state '%s' for %s: %s -> %s",
                             sensor_name, pv_thing_id, old_value, value)

                # Update aggregated data
                self.hass.async_create_task(self._update_aggregated_data())
        else:
            # Fetch state types if we don't have mapping
            _LOGGER.debug("Unknown PV state type %s for %s, refetching state types", state_type_id, pv_thing_id)
            self.hass.async_create_task(self._fetch_pv_state_types(pv_thing_id))

    async def _update_aggregated_data(self) -> None:
        """Update aggregated data for inverter group and battery group."""
        aggregated_data = {}
        
        # Aggregate inverter data
        total_inverter_power = 0.0
        total_inverter_energy = 0.0
        
        for pv_id, states in self._pv_states.items():
            current_power = states.get("currentPower")
            total_energy = states.get("totalEnergyProduced")
            
            if current_power is not None:
                total_inverter_power += float(current_power)
            if total_energy is not None:
                total_inverter_energy += float(total_energy)
        
        aggregated_data["inverter_group_current_power"] = total_inverter_power
        aggregated_data["inverter_group_total_energy"] = total_inverter_energy
        
        # Aggregate battery data
        total_battery_power = 0.0
        
        for battery_id, states in self._battery_states.items():
            current_power = states.get("currentPower")
            
            if current_power is not None:
                total_battery_power += float(current_power)
        
        aggregated_data["battery_group_current_power"] = total_battery_power
        
        # Update coordinator data with aggregated values and individual device states
        device_states = {}
        for battery_id, states in self._battery_states.items():
            for state_type, value in states.items():
                device_states[f"battery_{battery_id}_{state_type}"] = value

        for pv_id, states in self._pv_states.items():
            for state_type, value in states.items():
                device_states[f"pv_{pv_id}_{state_type}"] = value

        self._aggregated_data = aggregated_data
        new_data = {**self.data, **aggregated_data, **device_states}
        self.async_set_updated_data(new_data)

    @callback
    def _handle_disconnection(self) -> None:
        """Handle client disconnection by clearing all data to mark sensors as unavailable."""
        _LOGGER.warning("Connection to Nymea lost - marking all sensors as unavailable")
        
        # Clear all coordinator data immediately
        # This will cause all sensors to become unavailable as they check for data presence
        self._data.clear()
        
        # Clear aggregated data
        self._aggregated_data.clear()
        
        # Clear battery and PV states (but keep configs for when reconnection happens)
        for battery_id in self._battery_states:
            self._battery_states[battery_id].clear()
        
        for pv_id in self._pv_states:
            self._pv_states[pv_id].clear()
        
        # Mark coordinator update as failed to ensure sensors show unavailable
        self.last_update_success = False
        
        # Update coordinator with empty data to trigger sensor state updates
        self.async_set_updated_data({})
        
        _LOGGER.info("All sensor data cleared - sensors will show as unavailable until reconnection")

    @callback
    def _handle_reconnection(self) -> None:
        """Handle client reconnection by refreshing all data."""
        _LOGGER.info("Client reconnected, refreshing all data")
        # Schedule a full refresh of all data after reconnection
        self.hass.async_create_task(self._refresh_after_reconnection())

    async def _refresh_after_reconnection(self) -> None:
        """Refresh all data after reconnection."""
        max_retries = 3
        
        for retry in range(max_retries):
            try:
                if retry > 0:
                    _LOGGER.info("Retrying reconnection refresh (attempt %d/%d)", retry + 1, max_retries)
                    await asyncio.sleep(2.0 * retry)  # Progressive delay
                
                # Give the connection a moment to stabilize
                await asyncio.sleep(2.0)
                
                # Verify connection is healthy before proceeding
                try:
                    health_check = await asyncio.wait_for(
                        self.client.verify_connection_health(), 
                        timeout=10.0
                    )
                    if not health_check:
                        _LOGGER.warning("Connection not healthy during reconnection refresh (attempt %d)", retry + 1)
                        if retry < max_retries - 1:
                            continue
                        return
                except asyncio.TimeoutError:
                    _LOGGER.warning("Connection health check timeout (attempt %d)", retry + 1)
                    if retry < max_retries - 1:
                        continue
                    return
                
                _LOGGER.info("Starting full data refresh after reconnection (attempt %d)", retry + 1)
                
                # Re-enable notifications at JSONRPC level with retry
                try:
                    notify_response = await asyncio.wait_for(
                        self.client.send_request_with_response(
                            "JSONRPC.SetNotificationStatus", 
                            {"namespaces": ["Energy", "Integrations"]},
                            timeout=15.0
                        ),
                        timeout=20.0
                    )
                    if notify_response.get("status") == "success":
                        _LOGGER.info("Notifications re-enabled after reconnection")
                    else:
                        _LOGGER.warning("Failed to re-enable notifications: %s", notify_response)
                except asyncio.TimeoutError:
                    _LOGGER.warning("Timeout re-enabling notifications (attempt %d)", retry + 1)
                    if retry < max_retries - 1:
                        continue
                except Exception as e:
                    _LOGGER.warning("Failed to re-enable notifications (attempt %d): %s", retry + 1, e)
                    if retry < max_retries - 1:
                        continue
                
                # Clear old subscriptions - they're no longer valid after reconnection
                # The notification callbacks are still registered in the client, but we need
                # to re-fetch configurations which will re-subscribe to the correct things
                _LOGGER.debug("Clearing old notification tokens")
                self._root_meter_notification_token = None
                self._battery_notification_tokens.clear()
                self._pv_notification_tokens.clear()
                
                # Refresh root meter configuration and re-subscribe
                try:
                    _LOGGER.debug("Re-fetching root meter configuration")
                    await asyncio.wait_for(self._update_root_meter(), timeout=30.0)
                    _LOGGER.info("Root meter configuration refreshed after reconnection")
                except asyncio.TimeoutError:
                    _LOGGER.warning("Timeout updating root meter after reconnection (attempt %d)", retry + 1)
                    if retry < max_retries - 1:
                        continue
                except Exception as e:
                    _LOGGER.warning("Failed to update root meter after reconnection (attempt %d): %s", retry + 1, e)
                
                # Refresh battery configurations and re-subscribe
                # Use force_refresh=True to ensure states are refetched for existing batteries
                try:
                    _LOGGER.debug("Re-fetching battery configurations with force refresh")
                    await asyncio.wait_for(self._update_battery_configs(force_refresh=True), timeout=30.0)
                    _LOGGER.info("Battery configurations refreshed after reconnection (%d batteries)", len(self._battery_configs))
                except asyncio.TimeoutError:
                    _LOGGER.warning("Timeout updating battery configs after reconnection (attempt %d)", retry + 1)
                except Exception as e:
                    _LOGGER.warning("Failed to update battery configs after reconnection (attempt %d): %s", retry + 1, e)
                
                # Refresh PV configurations and re-subscribe
                # Use force_refresh=True to ensure states are refetched for existing PVs
                try:
                    _LOGGER.debug("Re-fetching PV configurations with force refresh")
                    await asyncio.wait_for(self._update_pv_configs(force_refresh=True), timeout=30.0)
                    _LOGGER.info("PV configurations refreshed after reconnection (%d inverters)", len(self._pv_configs))
                except asyncio.TimeoutError:
                    _LOGGER.warning("Timeout updating PV configs after reconnection (attempt %d)", retry + 1)
                except Exception as e:
                    _LOGGER.warning("Failed to update PV configs after reconnection (attempt %d): %s", retry + 1, e)
                
                # Force a full data update to populate all sensor values
                try:
                    _LOGGER.debug("Fetching initial data after reconnection")
                    data = await asyncio.wait_for(self._async_update_data(), timeout=20.0)
                    if data:
                        self.async_set_updated_data(data)
                        _LOGGER.info("Data refresh after reconnection completed successfully - %d data points", len(data))
                        break  # Success, exit retry loop
                    else:
                        _LOGGER.warning("No data received during reconnection refresh (attempt %d)", retry + 1)
                        if retry < max_retries - 1:
                            continue
                except asyncio.TimeoutError:
                    _LOGGER.warning("Timeout fetching data after reconnection (attempt %d)", retry + 1)
                    if retry < max_retries - 1:
                        continue
                except Exception as e:
                    _LOGGER.warning("Failed to update data after reconnection (attempt %d): %s", retry + 1, e)
                    if retry < max_retries - 1:
                        continue
                    
            except Exception as e:
                _LOGGER.error("Critical error during reconnection refresh (attempt %d): %s", retry + 1, e)
                if retry < max_retries - 1:
                    continue
                
        _LOGGER.info("Reconnection refresh completed")
