"""Tests for the grouped Leaflet HEMS sensor platform."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfEnergy
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.leaflet_hems.const import DOMAIN
from custom_components.leaflet_hems.sensor import (
    SENSOR_DEFINITIONS,
    LeafletGroupSensor,
    _async_cleanup_legacy_entries,
    async_setup_entry,
)


def make_coordinator(data=None):
    coordinator = Mock()
    coordinator.data = data or {}
    coordinator.last_update_success = True
    coordinator.async_add_listener = Mock()
    return coordinator


def make_entry():
    return MockConfigEntry(
        domain=DOMAIN,
        entry_id="test-entry",
        unique_id="test-uuid",
        data={
            "host": "192.168.1.10",
            "port": 2222,
            "nymea_uuid": "test-uuid",
            "nymea_name": "Test HEMS",
        },
    )


@pytest.mark.asyncio
async def test_setup_creates_grouped_sensors(hass):
    """All sensors are grouped; no per-thing entities are created."""

    entry = make_entry()
    coordinator = make_coordinator()
    hass.data[DOMAIN] = {entry.entry_id: {
        "coordinator": coordinator,
        "uuid": "test-uuid",
        "name": "Test HEMS",
    }}

    added = []
    await async_setup_entry(hass, entry, added.extend)

    assert len(added) == len(SENSOR_DEFINITIONS) == 13
    assert all(isinstance(sensor, LeafletGroupSensor) for sensor in added)
    groups = set().union(*(sensor.device_info["identifiers"] for sensor in added))
    assert (DOMAIN, "test-uuid_grid") in groups
    assert (DOMAIN, "test-uuid_generation") in groups
    assert (DOMAIN, "test-uuid_storage") in groups
    assert (DOMAIN, "test-uuid_consumption") in groups


def test_energy_sensors_are_energy_dashboard_compatible():
    """Energy sensors can be selected directly in the HA Energy Dashboard."""

    energy_sensors = [
        definition
        for definition in SENSOR_DEFINITIONS
        if definition["device_class"] == SensorDeviceClass.ENERGY
    ]
    assert len(energy_sensors) == 6
    for definition in energy_sensors:
        assert definition["unit"] == UnitOfEnergy.KILO_WATT_HOUR
        assert definition["state_class"] == SensorStateClass.TOTAL_INCREASING


def test_sensor_unique_ids_and_names():
    """Unique IDs stay stable and entity names are device-scoped."""

    coordinator = make_coordinator()
    entry = make_entry()
    for definition in SENSOR_DEFINITIONS:
        sensor = LeafletGroupSensor(
            coordinator,
            entry,
            "test-uuid",
            "Test HEMS",
            definition,
        )
        assert sensor.unique_id == f"test-uuid_{definition['key']}"
        assert sensor.name == definition["name"]
        assert sensor.has_entity_name is True
        assert sensor.device_info["via_device"] == (DOMAIN, "test-uuid")


def test_sensor_value_and_availability():
    """Values come from the coordinator and missing values are unavailable."""

    definition = next(
        item for item in SENSOR_DEFINITIONS if item["key"] == "grid_import_power"
    )
    coordinator = make_coordinator({"grid_import_power": 123.4})
    sensor = LeafletGroupSensor(
        coordinator, make_entry(), "test-uuid", "Test HEMS", definition
    )
    assert sensor.native_value == 123.4
    assert sensor.available is True

    coordinator.data = {"grid_import_power": None}
    assert sensor.native_value is None
    assert sensor.available is False

    coordinator.last_update_success = False
    coordinator.data = {"grid_import_power": 123.4}
    assert sensor.available is False


@pytest.mark.asyncio
async def test_legacy_per_thing_entities_are_removed(hass):
    """Upgrading removes the old battery/inverter entity registry entries."""

    entry = make_entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    legacy = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "{test-uuid}_battery_battery-id_currentPower",
        config_entry=entry,
        suggested_object_id="legacy_battery_power",
    )
    keep = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "test-uuid_grid_import_power",
        config_entry=entry,
        suggested_object_id="grid_import_power",
    )
    devices = dr.async_get(hass)
    legacy_base = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "{test-uuid}")},
        name="Legacy base",
    )
    legacy_battery = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "{test-uuid}_battery_battery-id")},
        name="Legacy battery",
    )

    await _async_cleanup_legacy_entries(hass, entry, "test-uuid")

    assert registry.async_get(legacy.entity_id) is None
    assert registry.async_get(keep.entity_id) is not None
    assert devices.async_get(legacy_base.id) is None
    assert devices.async_get(legacy_battery.id) is None
