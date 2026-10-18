"""Grouped sensor platform for the Leaflet HEMS integration.

The integration deliberately exposes four grouped devices instead of one
device per nymea thing:

* Grid Connection Point: import/export power and energy
* Generators: PV power and energy
* Storage: charge/discharge power and charged/discharged energy
* Household Consumption: calculated power and energy

All energy sensors use ``device_class=energy``, ``state_class=total_increasing``
and ``kWh`` so they can be selected directly in the Home Assistant Energy
Dashboard.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    GROUP_CONSUMPTION,
    GROUP_GENERATION,
    GROUP_GRID,
    GROUP_STORAGE,
    VERSION,
)
from .coordinator import LeafletHEMSCoordinator
from .util import normalize_uuid

_LOGGER = logging.getLogger(__name__)

_VIA_DEVICE_ID_SUPPORTED = "via_device_id" in DeviceInfo.__annotations__

GROUP_DEVICES: dict[str, dict[str, str]] = {
    GROUP_GRID: {
        "name": "Grid Connection Point",
        "model": "Grid Connection Point",
        "icon": "mdi:transmission-tower",
    },
    GROUP_GENERATION: {
        "name": "Generators",
        "model": "Generators",
        "icon": "mdi:solar-power",
    },
    GROUP_STORAGE: {
        "name": "Storage",
        "model": "Storage",
        "icon": "mdi:battery",
    },
    GROUP_CONSUMPTION: {
        "name": "Household Consumption",
        "model": "Household Consumption",
        "icon": "mdi:home-lightning-bolt",
    },
}

SENSOR_DEFINITIONS: list[dict[str, Any]] = [
    {
        "key": "grid_import_power",
        "group": GROUP_GRID,
        "name": "Grid Import Power",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi:transmission-tower-import",
        "precision": 0,
    },
    {
        "key": "grid_export_power",
        "group": GROUP_GRID,
        "name": "Grid Export Power",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi:transmission-tower-export",
        "precision": 0,
    },
    {
        "key": "grid_import_energy",
        "group": GROUP_GRID,
        "name": "Grid Import Energy",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "icon": "mdi:transmission-tower-import",
        "precision": 2,
    },
    {
        "key": "grid_export_energy",
        "group": GROUP_GRID,
        "name": "Grid Export Energy",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "icon": "mdi:transmission-tower-export",
        "precision": 2,
    },
    {
        "key": "generation_power",
        "group": GROUP_GENERATION,
        "name": "Generation Power",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi:solar-power",
        "precision": 0,
    },
    {
        "key": "generation_energy",
        "group": GROUP_GENERATION,
        "name": "Generation Energy",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "icon": "mdi:solar-panel",
        "precision": 2,
    },
    {
        "key": "storage_power",
        "group": GROUP_STORAGE,
        "name": "Storage Power",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi:battery-charging",
        "precision": 0,
    },
    {
        "key": "storage_charging_power",
        "group": GROUP_STORAGE,
        "name": "Storage Charging Power",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi:battery-plus",
        "precision": 0,
    },
    {
        "key": "storage_discharging_power",
        "group": GROUP_STORAGE,
        "name": "Storage Discharging Power",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi:battery-minus",
        "precision": 0,
    },
    {
        "key": "storage_energy_stored",
        "group": GROUP_STORAGE,
        "name": "Storage Energy Stored",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "icon": "mdi:battery-plus",
        "precision": 2,
    },
    {
        "key": "storage_energy_provided",
        "group": GROUP_STORAGE,
        "name": "Storage Energy Provided",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "icon": "mdi:battery-minus",
        "precision": 2,
    },
    {
        "key": "consumption_power",
        "group": GROUP_CONSUMPTION,
        "name": "Household Consumption Power",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi:home-lightning-bolt",
        "precision": 0,
    },
    {
        "key": "consumption_energy",
        "group": GROUP_CONSUMPTION,
        "name": "Household Consumption Energy",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "icon": "mdi:home-lightning-bolt",
        "precision": 2,
    },
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the grouped Leaflet HEMS sensors."""

    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator: LeafletHEMSCoordinator = entry_data["coordinator"]
    nymea_uuid: str = entry_data["uuid"]
    nymea_name: str = entry_data["name"]
    base_device_id: str | None = entry_data.get("device_id")
    if base_device_id is None:
        device = dr.async_get(hass).async_get_device(
            identifiers={(DOMAIN, nymea_uuid)}
        )
        base_device_id = device.id if device else None

    await _async_cleanup_legacy_entries(hass, config_entry, nymea_uuid)

    entities = [
        LeafletGroupSensor(
            coordinator=coordinator,
            config_entry=config_entry,
            nymea_uuid=nymea_uuid,
            nymea_name=nymea_name,
            definition=definition,
            base_device_id=base_device_id,
        )
        for definition in SENSOR_DEFINITIONS
    ]
    async_add_entities(entities)


class LeafletGroupSensor(CoordinatorEntity[LeafletHEMSCoordinator], SensorEntity):
    """A sensor belonging to one of the four grouped devices."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LeafletHEMSCoordinator,
        config_entry: ConfigEntry,
        nymea_uuid: str,
        nymea_name: str,
        definition: dict[str, Any],
        base_device_id: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._nymea_uuid = nymea_uuid
        self._nymea_name = nymea_name
        self._definition = definition
        self._key: str = definition["key"]

        self._attr_name = definition["name"]
        self._attr_unique_id = f"{nymea_uuid}_{self._key}"
        self._attr_device_class = definition["device_class"]
        self._attr_native_unit_of_measurement = definition["unit"]
        self._attr_state_class = definition["state_class"]
        self._attr_icon = definition["icon"]
        self._attr_suggested_display_precision = definition["precision"]

        group = definition["group"]
        group_info = GROUP_DEVICES[group]
        device_info: dict[str, Any] = {
            "identifiers": {(DOMAIN, f"{nymea_uuid}_{group}")},
            "name": f"{nymea_name} {group_info['name']}",
            "manufacturer": "Consolinno Energy GmbH",
            "model": group_info["model"],
            "sw_version": VERSION,
        }
        if base_device_id and _VIA_DEVICE_ID_SUPPORTED:
            device_info["via_device_id"] = base_device_id
        else:
            device_info["via_device"] = (DOMAIN, nymea_uuid)
        self._attr_device_info = DeviceInfo(**device_info)

    @property
    def native_value(self) -> float | None:
        """Return the current grouped value."""

        value = (self.coordinator.data or {}).get(self._key)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @property
    def available(self) -> bool:
        """Only report available when the coordinator has a live value."""

        return (
            self.coordinator.last_update_success
            and (self.coordinator.data or {}).get(self._key) is not None
        )


async def _async_cleanup_legacy_entries(
    hass: HomeAssistant, config_entry: ConfigEntry, nymea_uuid: str
) -> None:
    """Remove per-thing entities and devices created by older versions.

    Older releases created ``battery_<thing>_<state>`` and
    ``inverter_<thing>_<state>`` entities.  They are replaced by the four
    grouped devices.  Removing the stale registry entries avoids hundreds of
    unavailable entities after an upgrade.
    """

    valid_unique_ids = {
        f"{nymea_uuid}_{definition['key']}" for definition in SENSOR_DEFINITIONS
    }
    entity_registry = er.async_get(hass)
    for entity in list(entity_registry.entities.values()):
        if entity.config_entry_id != config_entry.entry_id:
            continue
        if entity.unique_id in valid_unique_ids:
            continue
        uuid_prefix = normalize_uuid(entity.unique_id.split("_", 1)[0])
        if uuid_prefix == nymea_uuid:
            _LOGGER.info("Removing legacy Leaflet HEMS entity %s", entity.entity_id)
            entity_registry.async_remove(entity.entity_id)

    device_registry = dr.async_get(hass)
    legacy_base_identifier = (DOMAIN, f"{{{nymea_uuid}}}")
    for device in dr.async_entries_for_config_entry(
        device_registry, config_entry.entry_id
    ):
        identifiers = {
            identifier
            for identifier in device.identifiers
            if identifier[0] == DOMAIN
        }
        legacy_device = legacy_base_identifier in identifiers
        if not legacy_device:
            for _domain, identifier in identifiers:
                identifier_uuid, _, suffix = identifier.partition("_")
                if normalize_uuid(identifier_uuid) != nymea_uuid:
                    continue
                if suffix.startswith(("battery_", "inverter_")):
                    legacy_device = True
                    break
        if legacy_device:
            _LOGGER.info("Removing legacy Leaflet HEMS device %s", device.name)
            device_registry.async_remove_device(device.id)
