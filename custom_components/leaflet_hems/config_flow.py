"""Config flow for the Leaflet HEMS integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.helpers import config_validation as cv

from .client import (
    NymeaAuthenticationError,
    NymeaClient,
    NymeaConnectionError,
    NymeaError,
    NymeaResponseError,
    NymeaTimeoutError,
)
from .const import (
    CONF_NYMEA_NAME,
    CONF_NYMEA_TOKEN,
    CONF_NYMEA_UUID,
    DEFAULT_PORT,
    DOMAIN,
    TXT_MANUFACTURER,
    TXT_NAME,
    TXT_SERVER_VERSION,
    TXT_UUID,
    ZEROCONF_NYMEA_MANUFACTURER,
)
from .util import normalize_uuid

_LOGGER = logging.getLogger(__name__)

DEVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Optional(CONF_NAME): cv.string,
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


@config_entries.HANDLERS.register(DOMAIN)
class LeafletHEMSFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Leaflet HEMS."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery_info: dict[str, Any] | None = None
        self._reauth_entry: config_entries.ConfigEntry | None = None

    # ------------------------------------------------------------------
    # Manual setup
    # ------------------------------------------------------------------
    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Start with manual setup."""

        return await self.async_step_manual(user_input)

    async def async_step_manual(self, user_input: dict[str, Any] | None = None):
        """Handle manual setup."""

        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = int(user_input.get(CONF_PORT, DEFAULT_PORT))
            name = user_input.get(CONF_NAME) or ""
            username = user_input.get("username") or ""
            password = user_input.get("password") or ""

            try:
                hello, token = await _async_probe(
                    host,
                    port,
                    username=username or None,
                    password=password or None,
                )
            except NymeaAuthenticationError:
                if username or password:
                    errors["base"] = "invalid_auth"
                else:
                    self._discovery_info = {
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_NYMEA_NAME: name or host,
                    }
                    return await self.async_step_auth()
            except (NymeaConnectionError, NymeaTimeoutError, NymeaResponseError):
                errors["base"] = "cannot_connect"
            except NymeaError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during Leaflet HEMS setup")
                errors["base"] = "cannot_connect"
            else:
                uuid = normalize_uuid(hello.get("uuid"))
                if not uuid:
                    errors["base"] = "cannot_get_uuid"
                else:
                    await self.async_set_unique_id(uuid)
                    if self._async_handle_existing_entry(uuid, host, port):
                        return self.async_abort(reason="already_configured")
                    return self._async_create_entry(
                        uuid=uuid,
                        name=name or hello.get("name") or host,
                        host=host,
                        port=port,
                        token=token,
                    )

        return self.async_show_form(
            step_id="manual",
            data_schema=DEVICE_SCHEMA,
            errors=errors,
        )

    async def async_step_auth(self, user_input: dict[str, Any] | None = None):
        """Ask for credentials when the device requires authentication."""

        errors: dict[str, str] = {}
        if not self._discovery_info:
            return self.async_abort(reason="discovery_error")

        host = self._discovery_info[CONF_HOST]
        port = int(self._discovery_info[CONF_PORT])
        name = self._discovery_info.get(CONF_NYMEA_NAME) or host

        if user_input is not None:
            try:
                hello, token = await _async_probe(
                    host,
                    port,
                    username=user_input["username"],
                    password=user_input["password"],
                )
            except NymeaAuthenticationError:
                errors["base"] = "invalid_auth"
            except (NymeaConnectionError, NymeaTimeoutError, NymeaResponseError):
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during Leaflet HEMS authentication")
                errors["base"] = "cannot_connect"
            else:
                uuid = normalize_uuid(hello.get("uuid"))
                if not uuid:
                    errors["base"] = "cannot_get_uuid"
                else:
                    await self.async_set_unique_id(uuid)
                    if self._async_handle_existing_entry(uuid, host, port):
                        return self.async_abort(reason="already_configured")
                    return self._async_create_entry(
                        uuid=uuid,
                        name=self._discovery_info.get(CONF_NYMEA_NAME)
                        or hello.get("name")
                        or name,
                        host=host,
                        port=port,
                        token=token,
                    )

        return self.async_show_form(
            step_id="auth",
            data_schema=AUTH_SCHEMA,
            errors=errors,
            description_placeholders={"host": host, "name": name},
        )

    # ------------------------------------------------------------------
    # Zeroconf discovery
    # ------------------------------------------------------------------
    async def async_step_zeroconf(self, discovery_info: Any):
        """Handle a device discovered through mDNS/Zeroconf."""

        parsed = self._parse_discovery_info(discovery_info)
        if parsed is None:
            return self.async_abort(reason="not_supported")

        self._normalize_existing_entry_ids()

        uuid = parsed[CONF_NYMEA_UUID]
        host = parsed[CONF_HOST]
        port = parsed[CONF_PORT]

        await self.async_set_unique_id(uuid)
        if self._async_handle_existing_entry(uuid, host, port):
            return self.async_abort(reason="already_configured")

        # Only talk to the device when it is not configured yet.  Configured
        # devices are updated from the TXT record without opening another
        # connection.
        try:
            hello = await _async_hello(host, port)
        except (NymeaConnectionError, NymeaTimeoutError, NymeaResponseError):
            _LOGGER.warning("Could not connect to discovered device %s:%s", host, port)
            return self.async_abort(reason="cannot_connect")
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error during Zeroconf validation")
            return self.async_abort(reason="cannot_connect")

        parsed.update(
            {
                CONF_NYMEA_UUID: normalize_uuid(hello.get("uuid")) or uuid,
                CONF_NYMEA_NAME: hello.get("name") or parsed.get(CONF_NYMEA_NAME),
                "server_version": hello.get("version")
                or parsed.get("server_version", ""),
                "authentication_required": bool(
                    hello.get("authenticationRequired")
                ),
            }
        )
        self._discovery_info = parsed

        self.context["title_placeholders"] = {
            "name": parsed[CONF_NYMEA_NAME],
            "hostname": parsed.get("hostname", ""),
            "uuid": uuid[:8],
        }

        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ):
        """Confirm a discovered device."""

        if not self._discovery_info:
            return self.async_abort(reason="discovery_error")

        if user_input is not None:
            if self._discovery_info.get("authentication_required"):
                return await self.async_step_auth()
            return self._async_create_entry(
                uuid=self._discovery_info[CONF_NYMEA_UUID],
                name=self._discovery_info[CONF_NYMEA_NAME],
                host=self._discovery_info[CONF_HOST],
                port=int(self._discovery_info[CONF_PORT]),
                token=None,
            )

        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "name": self._discovery_info[CONF_NYMEA_NAME],
                "host": self._discovery_info[CONF_HOST],
                "port": str(self._discovery_info[CONF_PORT]),
                "uuid": self._discovery_info[CONF_NYMEA_UUID],
                "hostname": self._discovery_info.get("hostname", ""),
                "server_version": self._discovery_info.get("server_version", ""),
            },
        )

    # ------------------------------------------------------------------
    # Reauthentication
    # ------------------------------------------------------------------
    async def async_step_reauth(self, entry_data: dict[str, Any]):
        """Start reauthentication for an existing entry."""

        entry_id = self.context.get("entry_id")
        self._reauth_entry = (
            self.hass.config_entries.async_get_entry(entry_id) if entry_id else None
        )
        if self._reauth_entry is None:
            return self.async_abort(reason="reauth_failed")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ):
        """Ask for new credentials and update the stored token."""

        errors: dict[str, str] = {}
        if self._reauth_entry is None:
            return self.async_abort(reason="reauth_failed")

        host = self._reauth_entry.data[CONF_HOST]
        port = int(self._reauth_entry.data[CONF_PORT])
        name = self._reauth_entry.data.get(CONF_NYMEA_NAME) or self._reauth_entry.title

        if user_input is not None:
            try:
                hello, token = await _async_probe(
                    host,
                    port,
                    username=user_input["username"],
                    password=user_input["password"],
                )
            except NymeaAuthenticationError:
                errors["base"] = "invalid_auth"
            except (NymeaConnectionError, NymeaTimeoutError, NymeaResponseError):
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during Leaflet HEMS reauthentication")
                errors["base"] = "cannot_connect"
            else:
                new_data = {**self._reauth_entry.data, CONF_NYMEA_TOKEN: token}
                if hello.get("uuid"):
                    new_data[CONF_NYMEA_UUID] = normalize_uuid(hello["uuid"])
                return self.async_update_reload_and_abort(
                    self._reauth_entry,
                    data=new_data,
                    reason="reauth_successful",
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=AUTH_SCHEMA,
            errors=errors,
            description_placeholders={"host": host, "name": name},
        )

    # ------------------------------------------------------------------
    # YAML import (also useful for automated tests and advanced setups)
    # ------------------------------------------------------------------
    async def async_step_import(self, user_input: dict[str, Any]):
        """Import a device from YAML."""

        host = user_input[CONF_HOST]
        port = int(user_input.get(CONF_PORT, DEFAULT_PORT))
        token = user_input.get(CONF_NYMEA_TOKEN)
        username = user_input.get("username")
        password = user_input.get("password")

        try:
            hello, new_token = await _async_probe(
                host,
                port,
                token=token,
                username=username,
                password=password,
            )
        except NymeaAuthenticationError:
            return self.async_abort(reason="invalid_auth")
        except (NymeaConnectionError, NymeaTimeoutError, NymeaResponseError):
            return self.async_abort(reason="cannot_connect")
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error during Leaflet HEMS import")
            return self.async_abort(reason="cannot_connect")

        uuid = normalize_uuid(user_input.get(CONF_NYMEA_UUID) or hello.get("uuid"))
        if not uuid:
            return self.async_abort(reason="cannot_get_uuid")

        await self.async_set_unique_id(uuid)
        if self._async_handle_existing_entry(uuid, host, port):
            return self.async_abort(reason="already_configured")
        return self._async_create_entry(
            uuid=uuid,
            name=user_input.get(CONF_NAME)
            or hello.get("name")
            or user_input.get(CONF_NYMEA_NAME)
            or host,
            host=host,
            port=port,
            token=new_token or token,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _async_create_entry(
        self, *, uuid: str, name: str, host: str, port: int, token: str | None
    ):
        data: dict[str, Any] = {
            CONF_HOST: host,
            CONF_PORT: port,
            CONF_NYMEA_UUID: uuid,
            CONF_NYMEA_NAME: name,
        }
        if token:
            data[CONF_NYMEA_TOKEN] = token
        return self.async_create_entry(title=name, data=data)

    def _async_handle_existing_entry(
        self, uuid: str, host: str, port: int
    ) -> config_entries.ConfigEntry | None:
        """Update an existing entry's host/port and reload it when needed."""

        entry = self._async_find_entry_by_uuid(uuid)
        if entry is None:
            return None

        data = dict(entry.data)
        changed = False
        if data.get(CONF_HOST) != host:
            data[CONF_HOST] = host
            changed = True
        if int(data.get(CONF_PORT, DEFAULT_PORT)) != int(port):
            data[CONF_PORT] = port
            changed = True
        if entry.unique_id != uuid:
            changed = True

        if changed:
            _LOGGER.info(
                "Updating Leaflet HEMS entry %s to %s:%s", entry.entry_id, host, port
            )
            self.hass.config_entries.async_update_entry(
                entry, data=data, unique_id=uuid
            )
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return entry

    def _async_find_entry_by_uuid(
        self, uuid: str
    ) -> config_entries.ConfigEntry | None:
        """Find a configured entry by its normalized device UUID."""

        for entry in self.hass.config_entries.async_entries(DOMAIN):
            entry_uuid = normalize_uuid(
                entry.unique_id or entry.data.get(CONF_NYMEA_UUID)
            )
            if entry_uuid == uuid:
                return entry
        return None

    def _normalize_existing_entry_ids(self) -> None:
        """Normalize UUIDs of existing entries before de-duplicating."""

        for entry in self.hass.config_entries.async_entries(DOMAIN):
            normalized = normalize_uuid(
                entry.unique_id or entry.data.get(CONF_NYMEA_UUID)
            )
            if normalized and entry.unique_id != normalized:
                self.hass.config_entries.async_update_entry(entry, unique_id=normalized)

    def _parse_discovery_info(self, discovery_info: Any) -> dict[str, Any] | None:
        """Parse Zeroconf discovery data into a config-entry dict."""

        try:
            host = getattr(discovery_info, "host", None)
            port = int(getattr(discovery_info, "port", None) or DEFAULT_PORT)
            if not host:
                return None

            hostname = getattr(discovery_info, "hostname", "") or getattr(
                discovery_info, "name", ""
            )
            if isinstance(hostname, bytes):
                hostname = hostname.decode("utf-8", errors="ignore")
            hostname = str(hostname)
            if hostname.endswith(".local."):
                hostname = hostname[:-7]
            elif hostname.endswith(".local"):
                hostname = hostname[:-6]

            properties = getattr(discovery_info, "properties", None) or {}

            def prop(key: str) -> str:
                value = properties.get(key, properties.get(key.encode(), ""))
                if isinstance(value, bytes):
                    return value.decode("utf-8", errors="ignore")
                return str(value) if value is not None else ""

            manufacturer = prop(TXT_MANUFACTURER)
            if (
                manufacturer
                and manufacturer.lower() != ZEROCONF_NYMEA_MANUFACTURER.lower()
            ):
                _LOGGER.debug(
                    "Ignoring device with manufacturer %s", manufacturer
                )
                return None

            uuid = normalize_uuid(prop(TXT_UUID))
            if not uuid:
                _LOGGER.warning("Discovered nymea device without UUID: %s", hostname)
                return None

            name = prop(TXT_NAME) or f"Leaflet HEMS ({host})"
            return {
                CONF_HOST: host,
                CONF_PORT: port,
                CONF_NYMEA_UUID: uuid,
                CONF_NYMEA_NAME: name,
                "hostname": hostname,
                "server_version": prop(TXT_SERVER_VERSION),
            }
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Error parsing Zeroconf discovery info")
            return None


async def _async_hello(host: str, port: int) -> dict[str, Any]:
    """Open a short-lived connection and perform JSONRPC.Hello."""

    client = NymeaClient(host, port, device_name=f"HomeAssistant-{host}")
    try:
        await client.async_open()
        return await client.async_hello()
    finally:
        await client.async_close()


async def _async_probe(
    host: str,
    port: int,
    *,
    token: str | None = None,
    username: str | None = None,
    password: str | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Validate the device and authenticate when necessary."""

    client = NymeaClient(
        host,
        port,
        token=token,
        username=username,
        password=password,
        device_name=f"HomeAssistant-{host}",
    )
    try:
        await client.async_open()
        hello = await client.async_hello()
        if hello.get("authenticationRequired") and not hello.get("authenticated"):
            if not username or not password:
                raise NymeaAuthenticationError("Credentials are required")
            await client.async_authenticate(username, password)
            hello = await client.async_hello()
            if not hello.get("authenticated"):
                raise NymeaAuthenticationError("The device rejected the credentials")
        return hello, client.token
    finally:
        await client.async_close()
