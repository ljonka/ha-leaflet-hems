"""Leaflet HEMS integration for Home Assistant.

The integration keeps one persistent JSON-RPC/TLS connection to the nymea
device.  Data is pushed through notifications; the only request bursts are
the compact snapshot taken after each (re)connect.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .client import NymeaClient
from .const import (
    CONF_HOST,
    CONF_NYMEA_NAME,
    CONF_NYMEA_TOKEN,
    CONF_NYMEA_UUID,
    CONF_PORT,
    DOMAIN,
    VERSION,
)
from .coordinator import LeafletHEMSCoordinator
from .util import normalize_uuid

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Leaflet HEMS from a config entry."""

    host = entry.data.get(CONF_HOST)
    port = entry.data.get(CONF_PORT)
    token = entry.data.get(CONF_NYMEA_TOKEN)
    nymea_name = entry.data.get(CONF_NYMEA_NAME) or entry.title or host
    nymea_uuid = normalize_uuid(entry.data.get(CONF_NYMEA_UUID))

    if not host or not nymea_uuid:
        _LOGGER.error("Leaflet HEMS entry %s is missing host or UUID", entry.entry_id)
        return False

    _async_migrate_entry(hass, entry, nymea_uuid)

    hass.data.setdefault(DOMAIN, {})

    client = NymeaClient(
        host,
        int(port),
        token=token,
        device_name=f"HomeAssistant-{host}",
    )
    coordinator = LeafletHEMSCoordinator(
        hass, client, nymea_uuid=nymea_uuid, nymea_name=nymea_name
    )

    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
        "uuid": nymea_uuid,
        "name": nymea_name,
    }

    # If the stored token is revoked, ask the user for credentials again
    # instead of reconnecting with a bad token forever.
    client.register_auth_failed_callback(
        lambda: _async_start_reauth(hass, entry)
    )

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, nymea_uuid)},
        manufacturer="Consolinno Energy GmbH",
        name=nymea_name,
        model="Leaflet HEMS",
        sw_version=VERSION,
    )
    hass.data[DOMAIN][entry.entry_id]["device_id"] = device_entry.id

    try:
        await coordinator.async_start()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        _LOGGER.exception("Failed to set up Leaflet HEMS entry %s", entry.entry_id)
        await coordinator.async_stop()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        return False

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and close the persistent client."""

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    entry_data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if entry_data is not None:
        coordinator: LeafletHEMSCoordinator = entry_data["coordinator"]
        await coordinator.async_stop()
    return unload_ok


def _async_migrate_entry(
    hass: HomeAssistant, entry: ConfigEntry, normalized_uuid: str
) -> None:
    """Normalize legacy entries that stored UUIDs with braces."""

    updates: dict[str, Any] = {}
    if entry.unique_id != normalized_uuid:
        updates["unique_id"] = normalized_uuid
    if entry.data.get(CONF_NYMEA_UUID) != normalized_uuid:
        updates["data"] = {**entry.data, CONF_NYMEA_UUID: normalized_uuid}
    if updates:
        _LOGGER.info(
            "Normalizing Leaflet HEMS entry %s UUID to %s",
            entry.entry_id,
            normalized_uuid,
        )
        hass.config_entries.async_update_entry(entry, **updates)


def _async_start_reauth(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Start the reauth flow if the Home Assistant version supports it."""

    start_reauth = getattr(entry, "async_start_reauth", None)
    if start_reauth is None:
        _LOGGER.error(
            "Leaflet HEMS token for %s is invalid; please remove and re-add the integration",
            entry.title,
        )
        return
    hass.async_create_task(start_reauth(hass))
