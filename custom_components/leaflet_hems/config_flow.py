"""Config flow for Leaflet HEMS integration."""

import asyncio
import logging
import time
from typing import Any, Dict, Optional

import voluptuous as vol
from .client import NymeaClient

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_NYMEA_UUID,
    CONF_NYMEA_NAME,
    DEFAULT_PORT,
    RPC_HELLO_METHOD,
    RPC_HELLO_LOCALE,
    RPC_ID,
    DOMAIN,
    CONF_NYMEA_TOKEN,
    ZEROCONF_NYMEA_MANUFACTURER,
    TXT_UUID,
    TXT_NAME,
    TXT_MANUFACTURER,
    TXT_SERVER_VERSION,
    TXT_SSL_ENABLED,
)

_LOGGER = logging.getLogger(__name__)

DEVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Optional(CONF_NAME): cv.string,
        # Allow optional credentials in the initial form so login can happen immediately
        vol.Optional("username"): cv.string,
        vol.Optional("password"): cv.string,
    }
)

AUTH_SCHEMA = vol.Schema(
    {
        vol.Required("username"): cv.string,
        vol.Required("password"): cv.string,
    }
)

DISCOVERY_CONFIRM_SCHEMA = vol.Schema({})


@config_entries.HANDLERS.register(DOMAIN)
class LeafletHEMSFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Leaflet HEMS."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    def __init__(self):
        """Initialize the config flow."""
        self._discovery_info: Optional[Dict[str, Any]] = None

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        """Handle the initial step - goes directly to manual setup."""
        return await self.async_step_manual(user_input)

    async def async_step_manual(self, user_input: Optional[Dict[str, Any]] = None):
        """Handle manual setup."""
        errors = {}
        if user_input is not None:
            # Get UUID from handshake (required)
            handshake_data = await self._async_perform_handshake(
                user_input[CONF_HOST], user_input[CONF_PORT]
            )

            if not handshake_data or not handshake_data.get("uuid"):
                errors["base"] = "cannot_connect"
                return self.async_show_form(
                    step_id="manual",
                    data_schema=DEVICE_SCHEMA,
                    errors=errors,
                    description_placeholders=user_input,
                )

            # If authentication is required, try authenticating immediately when credentials were provided.
            # Use a single persistent connection for hello + authenticate (server requires same session).
            if handshake_data.get("authentication_required"):
                # Store discovery info for use if auth succeeds or for the separate auth step
                self._discovery_info = {
                    CONF_HOST: user_input[CONF_HOST],
                    CONF_PORT: user_input[CONF_PORT],
                    CONF_NYMEA_UUID: handshake_data["uuid"],
                    CONF_NYMEA_NAME: user_input.get(CONF_NAME) or handshake_data.get("name") or user_input[CONF_HOST],
                }

                username = user_input.get("username") or ""
                password = user_input.get("password") or ""

                # If user provided credentials in the initial form, attempt authentication now
                if username or password:
                    nymea = NymeaClient()
                    try:
                        await nymea.connect(user_input[CONF_HOST], user_input[CONF_PORT])
                        # Perform hello on the same connection to satisfy server requirements
                        hello_params = await nymea.hello(RPC_HELLO_LOCALE)
                        if not hello_params:
                            _LOGGER.warning("Hello on persistent connection failed")
                        else:
                            device_name = f"HomeAssistant-{user_input[CONF_HOST]}"
                            token = await nymea.authenticate(username, password, device_name)
                            if token:
                                # store token and create entry
                                self._discovery_info[CONF_NYMEA_TOKEN] = token
                                await self.async_set_unique_id(self._discovery_info[CONF_NYMEA_UUID])
                                self._abort_if_unique_id_configured()
                                # Create entry
                                result = self.async_create_entry(
                                    title=self._discovery_info[CONF_NYMEA_NAME],
                                    data=self._discovery_info,
                                )
                                # Persist token into the created config entry using async_update_entry
                                try:
                                    for entry in self.hass.config_entries.async_entries(DOMAIN):
                                        if entry.data.get(CONF_NYMEA_UUID) == self._discovery_info[CONF_NYMEA_UUID]:
                                            new_data = dict(entry.data)
                                            new_data[CONF_NYMEA_TOKEN] = token
                                            self.hass.config_entries.async_update_entry(entry, data=new_data)
                                            _LOGGER.info("Persisted token to config entry %s", entry.entry_id)
                                            break
                                except Exception as e:
                                            _LOGGER.warning("Failed to persist token to config entry: %s", e)
                                return result
                            # Authentication failed — fall through to show auth form with error
                            errors["base"] = "invalid_auth"
                    finally:
                        # Close persistent client without blocking
                        try:
                            await nymea.close()
                        except Exception:
                            pass

                # Show the auth form (fallback) so user can enter credentials
                return self.async_show_form(
                    step_id="auth",
                    data_schema=AUTH_SCHEMA,
                    errors=errors,
                    description_placeholders={
                        "host": self._discovery_info.get(CONF_HOST, ""),
                        "name": self._discovery_info.get(CONF_NYMEA_NAME, ""),
                    },
                )

            # Create discovery info with data from handshake
            self._discovery_info = {
                CONF_HOST: user_input[CONF_HOST],
                CONF_PORT: user_input[CONF_PORT],
                CONF_NYMEA_UUID: handshake_data["uuid"],
                CONF_NYMEA_NAME: user_input.get(CONF_NAME) or handshake_data.get("name") or user_input[CONF_HOST],
            }

            # Check if already configured
            await self.async_set_unique_id(self._discovery_info[CONF_NYMEA_UUID])
            self._abort_if_unique_id_configured()

            # Validate connection
            if not await self._async_test_connection_and_handshake():
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=self._discovery_info[CONF_NYMEA_NAME],
                    data=self._discovery_info,
                )

        return self.async_show_form(
            step_id="manual",
            data_schema=DEVICE_SCHEMA,
            errors=errors,
            description_placeholders=user_input or {},
        )

    async def async_step_zeroconf(self, discovery_info):
        """Handle zeroconf discovery."""
        _LOGGER.info("Zeroconf discovery received: %s", discovery_info)
        
        # Parse discovery information
        parsed_info = self._parse_discovery_info(discovery_info)
        if not parsed_info:
            _LOGGER.warning("Failed to parse discovery info or device not supported")
            return self.async_abort(reason="not_supported")

        # Extract device information
        host = parsed_info[CONF_HOST]
        port = parsed_info[CONF_PORT]
        uuid = parsed_info[CONF_NYMEA_UUID]
        name = parsed_info[CONF_NYMEA_NAME]
        hostname = parsed_info.get("hostname", "")

        # Set unique_id and check if already configured
        await self.async_set_unique_id(uuid)
        self._abort_if_unique_id_configured()

        # Store discovery info for potential use in subsequent steps
        self._discovery_info = parsed_info

        # Perform handshake to validate the device and get additional info
        handshake_data = await self._async_perform_handshake(host, port)
        if not handshake_data:
            _LOGGER.warning("Could not connect to discovered device %s:%s", host, port)
            return self.async_abort(reason="cannot_connect")

        # Update discovery info with handshake data
        self._discovery_info.update({
            CONF_NYMEA_UUID: handshake_data.get("uuid", uuid),
            CONF_NYMEA_NAME: handshake_data.get("name", name),
        })

        # Set title placeholders to show unique identifying information in the discovery list
        # This displays in the integration overview before the user clicks on the device
        self.context["title_placeholders"] = {
            "name": name,
            "hostname": hostname,
            "uuid": uuid[:8],  # Show first 8 characters of UUID for brevity
        }

        # Show confirmation form to user
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(self, user_input: Optional[Dict[str, Any]] = None):
        """Handle discovery confirmation."""
        if not self._discovery_info:
            _LOGGER.error("Discovery info missing in discovery_confirm step")
            return self.async_abort(reason="discovery_error")

        if user_input is not None:
            # User confirmed - check if authentication is needed
            try:
                # Perform handshake to get current authentication status
                handshake_data = await self._async_perform_handshake(
                    self._discovery_info[CONF_HOST], 
                    self._discovery_info[CONF_PORT]
                )
                
                if handshake_data:
                    # Update discovery info with latest handshake data
                    self._discovery_info.update({
                        CONF_NYMEA_UUID: handshake_data.get("uuid", self._discovery_info[CONF_NYMEA_UUID]),
                        CONF_NYMEA_NAME: handshake_data.get("name", self._discovery_info[CONF_NYMEA_NAME]),
                    })
                    
                    if handshake_data.get("authentication_required"):
                        _LOGGER.info("Authentication required for device %s, proceeding to auth step", 
                                   self._discovery_info[CONF_NYMEA_NAME])
                        # Authentication required - go to auth step
                        return await self.async_step_auth()
                    else:
                        # No authentication required - create entry directly
                        _LOGGER.info("Creating config entry for device %s without authentication", 
                                   self._discovery_info[CONF_NYMEA_NAME])
                        await self.async_set_unique_id(self._discovery_info[CONF_NYMEA_UUID])
                        self._abort_if_unique_id_configured()
                        return self.async_create_entry(
                            title=self._discovery_info[CONF_NYMEA_NAME],
                            data=self._discovery_info,
                        )
                else:
                    _LOGGER.error("Failed to connect to device during discovery confirmation")
                    return self.async_abort(reason="cannot_connect")
                    
            except Exception as e:
                _LOGGER.error("Error during discovery confirmation: %s", e, exc_info=True)
                return self.async_abort(reason="cannot_connect")

        # Show confirmation form
        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=DISCOVERY_CONFIRM_SCHEMA,
            description_placeholders={
                "name": self._discovery_info[CONF_NYMEA_NAME],
                "host": self._discovery_info[CONF_HOST],
                "port": str(self._discovery_info[CONF_PORT]),
                "uuid": self._discovery_info[CONF_NYMEA_UUID],
                "hostname": self._discovery_info.get("hostname", ""),
                "server_version": self._discovery_info.get("server_version", ""),
            },
        )

    async def async_step_auth(self, user_input: Optional[Dict[str, Any]] = None):
        """Handle authentication step."""
        errors = {}

        # Ensure discovery info exists
        if not self._discovery_info:
            _LOGGER.error("Discovery info missing in auth step")
            errors["base"] = "cannot_connect"
            return self.async_show_form(
                step_id="manual",
                data_schema=DEVICE_SCHEMA,
                errors=errors,
                description_placeholders={},
            )

        host = self._discovery_info[CONF_HOST]
        port = self._discovery_info[CONF_PORT]

        if user_input is not None:
            username = user_input.get("username", "")
            password = user_input.get("password", "")

            if not username or not password:
                errors["base"] = "invalid_auth"
                _LOGGER.warning("Authentication attempt with empty username or password")
            else:
                # Try to authenticate with provided credentials using stored host/port
                auth_token = await self._async_perform_authentication(
                    host,
                    port,
                    username,
                    password,
                )

                if auth_token:
                    # Store the token in discovery info
                    self._discovery_info[CONF_NYMEA_TOKEN] = auth_token
                    await self.async_set_unique_id(self._discovery_info[CONF_NYMEA_UUID])
                    self._abort_if_unique_id_configured()
                    # Authentication succeeded — create the config entry immediately
                    _LOGGER.info("Authentication successful, creating config entry for %s", 
                               self._discovery_info[CONF_NYMEA_NAME])
                    return self.async_create_entry(
                        title=self._discovery_info[CONF_NYMEA_NAME],
                        data=self._discovery_info,
                    )
                else:
                    errors["base"] = "invalid_auth"
                    _LOGGER.warning("Authentication failed for device %s", 
                                  self._discovery_info[CONF_NYMEA_NAME])

        # Show authentication form
        return self.async_show_form(
            step_id="auth",
            data_schema=AUTH_SCHEMA,
            errors=errors,
            description_placeholders={
                "host": host,
                "name": self._discovery_info.get(CONF_NYMEA_NAME, ""),
            },
        )

    async def _async_test_connection_and_handshake(self) -> bool:
        """Test connection to the device and perform handshake."""
        if not self._discovery_info:
            return False
        host = self._discovery_info[CONF_HOST]
        port = self._discovery_info[CONF_PORT]

        # Attempt handshake to confirm connection and get details
        handshake_data = await self._async_perform_handshake(host, port)
        if handshake_data:
            # Update discovery info with name from handshake if not already set
            if CONF_NYMEA_NAME not in self._discovery_info or not self._discovery_info[CONF_NYMEA_NAME]:
                self._discovery_info[CONF_NYMEA_NAME] = handshake_data.get("name", self._discovery_info[CONF_HOST])
            # Ensure UUID is set from handshake
            if handshake_data.get("uuid"):
                self._discovery_info[CONF_NYMEA_UUID] = handshake_data["uuid"]
            _LOGGER.info(
                "Successfully connected and performed handshake with %s:%s. Name: %s, UUID: %s",
                host,
                port,
                self._discovery_info.get(CONF_NYMEA_NAME),
                self._discovery_info.get(CONF_NYMEA_UUID),
            )
            return True
        _LOGGER.warning("Failed to connect or handshake with %s:%s", host, port)
        return False

    async def _async_perform_handshake(self, host: str, port: int) -> Optional[Dict[str, Any]]:
        """Perform the initial handshake with the Nymea device using TCP+TLS JSON-RPC."""
        import ssl
        import json

        reader = None
        writer = None
        
        try:
            # Create SSL context for self-signed certificates in executor to avoid blocking
            loop = asyncio.get_event_loop()
            ssl_context = await loop.run_in_executor(
                None,
                lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            )
            # Configure SSL context to accept self-signed certificates
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            # Prepare JSON-RPC handshake payload
            handshake_payload = {
                "id": RPC_ID,
                "method": RPC_HELLO_METHOD,
                "params": {"locale": RPC_HELLO_LOCALE},
            }
            
            # Convert to JSON string and add newline (JSON-RPC line protocol)
            message = json.dumps(handshake_payload) + "\n"
            
            _LOGGER.info("Connecting to %s:%s via TCP+TLS", host, port)
            
            # Open TCP+TLS connection
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, ssl=ssl_context),
                timeout=10
            )
            
            # Send the handshake message
            writer.write(message.encode('utf-8'))
            await writer.drain()
            
            # Read the response (assuming line-based protocol)
            response_line = await asyncio.wait_for(reader.readline(), timeout=10)
            response_text = response_line.decode('utf-8').strip()
            
            # Parse JSON response
            result = json.loads(response_text)
            
            if result.get("status") == "success":
                params = result.get("params", {})
                handshake_data = {
                    "name": params.get("name"),
                    "uuid": params.get("uuid"),
                    "version": params.get("version"),
                    "protocol_version": params.get("protocol version"),
                    "authentication_required": params.get("authenticationRequired", False),
                    "initial_setup_required": params.get("initialSetupRequired", False),
                }
                _LOGGER.info(
                    "Handshake successful with %s:%s - Name: %s, UUID: %s, Version: %s",
                    host, port, 
                    handshake_data.get("name"),
                    handshake_data.get("uuid"),
                    handshake_data.get("version")
                )
                return handshake_data
            else:
                _LOGGER.warning("Handshake failed with status %s: %s", result.get("status"), result)
                
        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout during handshake with %s:%s", host, port)
        except json.JSONDecodeError as e:
            _LOGGER.error("Failed to parse JSON response from %s:%s: %s", host, port, e)
        except ssl.SSLError as e:
            _LOGGER.error("SSL error during handshake with %s:%s: %s", host, port, e)
        except Exception as e:
            _LOGGER.error("Unexpected error during handshake with %s:%s: %s", host, port, e, exc_info=True)
        finally:
            # Close the connection without blocking the event loop.
            # Schedule wait_closed() as a background task and attach a done callback
            # to consume exceptions so they don't become unhandled.
            if writer:
                try:
                    writer.close()
                    try:
                        task = asyncio.create_task(writer.wait_closed())
                        def _on_wait_closed(t: asyncio.Task) -> None:
                            try:
                                _ = t.result()
                            except:
                                # Ignore all exceptions during cleanup - this is best effort
                                # and failures here are not critical since we're shutting down
                                pass
                        try:
                            task.add_done_callback(_on_wait_closed)
                        except Exception:
                            pass
                    except Exception:
                        pass
                except Exception as e:
                    _LOGGER.warning("Error closing connection: %s", e)
                    
        return None

    async def _async_perform_authentication(self, host: str, port: int, username: str, password: str) -> Optional[str]:
        """Perform authentication with the Nymea device and return token on success.

        Uses the same approach as the working manual authentication: creates a NymeaClient
        connection, performs the required handshake, then authenticates using JSONRPC.Authenticate.
        """
        nymea = NymeaClient()
        try:
            _LOGGER.info("Attempting authentication for user '%s' with %s:%s", username, host, port)
            
            # Connect to the device
            await nymea.connect(host, port)
            
            # Perform required handshake first (nymea protocol requirement)
            hello_params = await nymea.hello(RPC_HELLO_LOCALE)
            if not hello_params:
                _LOGGER.warning("Handshake failed during authentication for %s:%s", host, port)
                return None
            
            # Perform authentication using correct method
            device_name = f"HomeAssistant-{host}"
            token = await nymea.authenticate(username, password, device_name)
            
            if token:
                _LOGGER.info("Authentication successful for user '%s' (token received)", username)
                return token
            else:
                _LOGGER.warning("Authentication failed for user '%s'", username)
                return None
                
        except Exception as e:
            _LOGGER.error("Unexpected error during authentication with %s:%s: %s", host, port, e, exc_info=True)
            return None
        finally:
            # Clean up connection
            try:
                await nymea.close()
            except Exception:
                pass

    def _parse_discovery_info(self, discovery_info) -> Optional[Dict[str, Any]]:
        """Parse zeroconf discovery information."""
        try:
            # Extract host and port from discovery info
            host = discovery_info.host
            port = discovery_info.port
            
            # Extract hostname from zeroconf discovery info (the mDNS name)
            hostname = discovery_info.hostname or discovery_info.name or ""
            # Remove .local suffix if present
            if hostname.endswith(".local."):
                hostname = hostname[:-7]
            elif hostname.endswith(".local"):
                hostname = hostname[:-6]
            
            # Get TXT record properties
            properties = discovery_info.properties or {}
            
            def _decode_property(key: str) -> str:
                """Safely decode a property that might be bytes or str."""
                value = properties.get(key, "")
                if isinstance(value, bytes):
                    return value.decode("utf-8", errors="ignore")
                return str(value) if value else ""
            
            # Validate that this is a nymea device
            manufacturer = _decode_property(TXT_MANUFACTURER)
            if manufacturer != ZEROCONF_NYMEA_MANUFACTURER:
                _LOGGER.info("Device manufacturer '%s' is not '%s', skipping", manufacturer, ZEROCONF_NYMEA_MANUFACTURER)
                return None
            
            # Extract device information from TXT records
            uuid = _decode_property(TXT_UUID)
            name = _decode_property(TXT_NAME)
            server_version = _decode_property(TXT_SERVER_VERSION)
            ssl_enabled = _decode_property(TXT_SSL_ENABLED)
            
            # Clean up UUID (remove braces if present)
            if uuid.startswith("{") and uuid.endswith("}"):
                uuid = uuid[1:-1]
            
            # Use fallback values if needed
            if not uuid:
                _LOGGER.warning("No UUID found in discovery info")
                return None
                
            if not name:
                name = f"Leaflet HEMS ({host})"
            
            _LOGGER.info(
                "Parsed discovery info - Host: %s, Port: %s, Hostname: %s, Name: %s, UUID: %s, Version: %s, SSL: %s",
                host, port, hostname, name, uuid, server_version, ssl_enabled
            )
            
            return {
                CONF_HOST: host,
                CONF_PORT: port,
                CONF_NYMEA_UUID: uuid,
                CONF_NYMEA_NAME: name,
                "hostname": hostname,
                "server_version": server_version,
            }
            
        except Exception as e:
            _LOGGER.error("Error parsing discovery info: %s", e, exc_info=True)
            return None

    @callback
    async def async_step_import(self, user_input: Dict[str, Any]) -> Dict[str, Any]:
        """Handle import from YAML."""
        # This is for legacy YAML configuration if any
        # For a new integration, this might not be strictly necessary but good practice
        host = user_input.get(CONF_HOST)
        port = user_input.get(CONF_PORT, DEFAULT_PORT)
        nymea_uuid = user_input.get(CONF_NYMEA_UUID)
        nymea_name = user_input.get(CONF_NYMEA_NAME, host)

        if not nymea_uuid:
            # Try to get UUID via handshake if not provided
            handshake_data = await self._async_perform_handshake(host, port)
            if handshake_data and handshake_data.get("uuid"):
                nymea_uuid = handshake_data["uuid"]
                user_input[CONF_NYMEA_UUID] = nymea_uuid
            else:
                return self.async_abort(reason="cannot_get_uuid")

        await self.async_set_unique_id(nymea_uuid)
        self._abort_if_unique_id_configured()

        # Validate connection
        if not await self._async_test_connection_and_handshake():
            return self.async_abort(reason="cannot_connect")

        return self.async_create_entry(
            title=nymea_name,
            data=user_input,
        )
