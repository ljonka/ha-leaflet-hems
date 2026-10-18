"""Tests for the event-driven Leaflet HEMS coordinator."""

from __future__ import annotations

import pytest

from custom_components.leaflet_hems.const import (
    ENERGY_POWER_BALANCE_CHANGED,
    INTEGRATIONS_STATE_CHANGED,
)
from custom_components.leaflet_hems.coordinator import LeafletHEMSCoordinator

from .fake_nymea import FakeNymeaClient

ROOT = "root-id"
PV = "pv-id"
PV2 = "pv-2-id"
BATTERY = "battery-id"

CLASS_ROOT = "class-root"
CLASS_PV = "class-pv"
CLASS_BATTERY = "class-battery"

STATE_TYPES = {
    CLASS_ROOT: {
        "root-power": "currentPower",
        "root-consumed": "totalEnergyConsumed",
        "root-produced": "totalEnergyProduced",
    },
    CLASS_PV: {
        "pv-power": "currentPower",
        "pv-produced": "totalEnergyProduced",
    },
    CLASS_BATTERY: {
        "bat-power": "currentPower",
        "bat-consumed": "totalEnergyConsumed",
        "bat-produced": "totalEnergyProduced",
    },
}

THING_CLASSES = {
    ROOT: CLASS_ROOT,
    PV: CLASS_PV,
    PV2: CLASS_PV,
    BATTERY: CLASS_BATTERY,
}

VALUES = {
    ROOT: {
        "root-power": 103.99,
        "root-consumed": 22177.0,
        "root-produced": 27994.0,
    },
    PV: {
        "pv-power": -459.0,
        "pv-produced": 50397.6,
    },
    PV2: {
        "pv-power": -100.0,
        "pv-produced": 1000.0,
    },
    BATTERY: {
        "bat-power": 0.0,
        "bat-consumed": 0.003,
        "bat-produced": 0.304,
    },
}

POWER_BALANCE = {
    "currentPowerAcquisition": 103.99,
    "currentPowerProduction": -459.0,
    "currentPowerStorage": 0.0,
    "currentPowerConsumption": 562.99,
    "totalAcquisition": 19729726.74,
    "totalReturn": 8984406.36,
    "totalProduction": 12079.5,
    "totalConsumption": 10757399.9,
}


def make_responses(
    *,
    pv_ids: list[str] | None = None,
    battery_ids: list[str] | None = None,
    root_id: str | None = ROOT,
):
    pv_ids = [PV] if pv_ids is None else pv_ids
    battery_ids = [BATTERY] if battery_ids is None else battery_ids

    def get_root_meter(_params):
        return {"rootMeterThingId": root_id} if root_id else {}

    def get_pv(_params):
        return {"pvConfigurations": [{"pvThingId": thing_id} for thing_id in pv_ids]}

    def get_batteries(_params):
        return {
            "batteryConfigurations": [
                {"batteryThingId": thing_id} for thing_id in battery_ids
            ]
        }

    def get_things(params):
        thing_id = params["thingId"]
        return {
            "things": [
                {
                    "id": thing_id,
                    "name": thing_id,
                    "thingClassId": THING_CLASSES[thing_id],
                }
            ]
        }

    def get_thing_classes(params):
        return {
            "thingClasses": [
                {"id": class_id, "name": class_id}
                for class_id in params["thingClassIds"]
            ]
        }

    def get_state_types(params):
        class_id = params["thingClassId"]
        return {
            "stateTypes": [
                {"id": state_id, "name": name}
                for state_id, name in STATE_TYPES[class_id].items()
            ]
        }

    def get_state_values(params):
        thing_id = params["thingId"]
        return {
            "values": [
                {"stateTypeId": state_id, "value": value}
                for state_id, value in VALUES[thing_id].items()
            ]
        }

    def get_power_balance(_params):
        return dict(POWER_BALANCE)

    return {
        "Energy.GetRootMeter": get_root_meter,
        "Hems.GetPvConfigurations": get_pv,
        "Hems.GetBatteryConfigurations": get_batteries,
        "Integrations.GetThings": get_things,
        "Integrations.GetThingClasses": get_thing_classes,
        "Integrations.GetStateTypes": get_state_types,
        "Integrations.GetStateValues": get_state_values,
        "Energy.GetPowerBalance": get_power_balance,
    }


def make_coordinator(hass, responses) -> tuple[LeafletHEMSCoordinator, FakeNymeaClient]:
    client = FakeNymeaClient(responses)
    return LeafletHEMSCoordinator(hass, client, "uuid", "Test HEMS"), client


@pytest.mark.asyncio
async def test_snapshot_uses_filtered_requests(hass):
    """The snapshot never asks for all things or all thing classes."""

    coordinator, client = make_coordinator(hass, make_responses())
    await coordinator.async_start(first_snapshot_timeout=1)

    assert coordinator.data
    for method, params in client.calls:
        if method == "Integrations.GetThings":
            assert params and "thingId" in params
        if method == "Integrations.GetThingClasses":
            assert params and params.get("thingClassIds")
    assert len([c for c in client.calls if c[0] == "Integrations.GetThings"]) == 3
    assert len([c for c in client.calls if c[0] == "Integrations.GetThingClasses"]) == 3


@pytest.mark.asyncio
async def test_grouped_data_mapping(hass):
    """Power, energy, signs and units are mapped to the grouped entities."""

    coordinator, client = make_coordinator(hass, make_responses())
    await coordinator.async_start(first_snapshot_timeout=1)
    data = coordinator.data

    assert data["grid_power"] == pytest.approx(104.0)
    assert data["grid_import_power"] == pytest.approx(104.0)
    assert data["grid_export_power"] == pytest.approx(0.0)
    assert data["grid_import_energy"] == pytest.approx(22177.0)
    assert data["grid_export_energy"] == pytest.approx(27994.0)

    assert data["generation_power"] == pytest.approx(459.0)
    assert data["generation_energy"] == pytest.approx(50397.6)

    assert data["storage_power"] == pytest.approx(0.0)
    assert data["storage_energy_stored"] == pytest.approx(0.003)
    assert data["storage_energy_provided"] == pytest.approx(0.304)

    assert data["consumption_power"] == pytest.approx(563.0)
    assert data["consumption_energy"] == pytest.approx(
        22177.0 + 50397.6 + 0.304 - 0.003 - 27994.0
    )


@pytest.mark.asyncio
async def test_power_balance_units_without_things(hass):
    """Grid totals from the energy balance are converted from Wh to kWh."""

    coordinator, client = make_coordinator(
        hass,
        make_responses(pv_ids=[], battery_ids=[], root_id=None),
    )
    await coordinator.async_start(first_snapshot_timeout=1)
    data = coordinator.data

    assert data["grid_import_energy"] == pytest.approx(19729.72674)
    assert data["grid_export_energy"] == pytest.approx(8984.40636)
    assert data["generation_energy"] == pytest.approx(12079.5)
    assert data["generation_power"] == pytest.approx(459.0)
    assert data["consumption_energy"] == pytest.approx(
        19729.72674 + 12079.5 - 8984.40636
    )


@pytest.mark.asyncio
async def test_power_balance_notification_is_fallback(hass):
    """PowerBalanceChanged supplies values when no per-thing states exist."""

    coordinator, client = make_coordinator(
        hass, make_responses(pv_ids=[], battery_ids=[], root_id=None)
    )
    await coordinator.async_start(first_snapshot_timeout=1)
    calls_before = len(client.calls)

    client.emit(
        {
            "notification": ENERGY_POWER_BALANCE_CHANGED,
            "params": {
                "currentPowerAcquisition": -500.0,
                "currentPowerProduction": -1500.0,
                "currentPowerStorage": 200.0,
                "currentPowerConsumption": 800.0,
            },
        }
    )
    coordinator._cancel_update_handle()
    coordinator._flush_update()

    assert len(client.calls) == calls_before
    assert coordinator.data["grid_import_power"] == 0.0
    assert coordinator.data["grid_export_power"] == 500.0
    assert coordinator.data["generation_power"] == 1500.0
    assert coordinator.data["storage_charging_power"] == 200.0
    assert coordinator.data["consumption_power"] == 800.0


@pytest.mark.asyncio
async def test_state_changed_updates_power_values(hass):
    """Live thing states update the grouped power values immediately."""

    coordinator, client = make_coordinator(hass, make_responses())
    await coordinator.async_start(first_snapshot_timeout=1)

    for thing_id, state_type_id, value in (
        (ROOT, "root-power", -500.0),
        (PV, "pv-power", -1500.0),
        (BATTERY, "bat-power", 200.0),
    ):
        client.emit(
            {
                "notification": INTEGRATIONS_STATE_CHANGED,
                "params": {
                    "thingId": thing_id,
                    "stateTypeId": state_type_id,
                    "value": value,
                },
            }
        )
    coordinator._cancel_update_handle()
    coordinator._flush_update()

    assert coordinator.data["grid_power"] == -500.0
    assert coordinator.data["grid_import_power"] == 0.0
    assert coordinator.data["grid_export_power"] == 500.0
    assert coordinator.data["generation_power"] == 1500.0
    assert coordinator.data["storage_charging_power"] == 200.0
    assert coordinator.data["consumption_power"] == 800.0


@pytest.mark.asyncio
async def test_state_changed_updates_battery_energy(hass):
    """Battery energy counters are updated from StateChanged notifications."""

    coordinator, client = make_coordinator(hass, make_responses())
    await coordinator.async_start(first_snapshot_timeout=1)

    client.emit(
        {
            "notification": INTEGRATIONS_STATE_CHANGED,
            "params": {
                "thingId": BATTERY,
                "stateTypeId": "bat-consumed",
                "value": 12.5,
            },
        }
    )
    coordinator._cancel_update_handle()
    coordinator._flush_update()

    assert coordinator.data["storage_energy_stored"] == pytest.approx(12.5)
    assert coordinator.data["consumption_energy"] == pytest.approx(
        22177.0 + 50397.6 + 0.304 - 12.5 - 27994.0
    )


@pytest.mark.asyncio
async def test_irrelevant_state_is_ignored(hass):
    """Voltage/frequency style states do not trigger data updates."""

    coordinator, client = make_coordinator(hass, make_responses())
    await coordinator.async_start(first_snapshot_timeout=1)
    data_before = dict(coordinator.data)

    client.emit(
        {
            "notification": INTEGRATIONS_STATE_CHANGED,
            "params": {
                "thingId": BATTERY,
                "stateTypeId": "unknown-state",
                "value": 230.0,
            },
        }
    )
    coordinator._cancel_update_handle()
    coordinator._flush_update()

    assert coordinator.data == data_before


@pytest.mark.asyncio
async def test_pv_configuration_added_and_removed(hass):
    """PV config notifications add/remove only the affected thing."""

    responses = make_responses()
    coordinator, client = make_coordinator(hass, responses)
    await coordinator.async_start(first_snapshot_timeout=1)
    assert coordinator.data["generation_energy"] == pytest.approx(50397.6)

    responses["Hems.GetPvConfigurations"] = lambda _params: {
        "pvConfigurations": [
            {"pvThingId": PV},
            {"pvThingId": PV2},
        ]
    }
    await coordinator._async_refresh_pv_configs()
    coordinator._cancel_update_handle()
    coordinator._flush_update()
    assert coordinator.data["generation_energy"] == pytest.approx(51397.6)
    assert coordinator.data["generation_power"] == pytest.approx(559.0)

    responses["Hems.GetPvConfigurations"] = lambda _params: {
        "pvConfigurations": [{"pvThingId": PV2}]
    }
    await coordinator._async_refresh_pv_configs()
    coordinator._cancel_update_handle()
    coordinator._flush_update()
    assert coordinator.data["generation_energy"] == pytest.approx(1000.0)


@pytest.mark.asyncio
async def test_disconnect_marks_data_unavailable(hass):
    """A lost connection clears live data and marks the coordinator failed."""

    coordinator, client = make_coordinator(hass, make_responses())
    await coordinator.async_start(first_snapshot_timeout=1)
    assert coordinator.last_update_success

    await coordinator._async_on_disconnected(client, Exception("connection lost"))

    assert coordinator.last_update_success is False
    assert coordinator.data == {}
    assert coordinator._states == {}


@pytest.mark.asyncio
async def test_update_data_does_no_io(hass):
    """DataUpdateCoordinator refreshes must not poll the device."""

    coordinator, client = make_coordinator(hass, make_responses())
    await coordinator.async_start(first_snapshot_timeout=1)
    calls_before = len(client.calls)

    data = await coordinator._async_update_data()

    assert data == coordinator.data
    assert len(client.calls) == calls_before


@pytest.mark.asyncio
async def test_energy_and_power_values_are_rounded(hass):
    """Energy is rounded to Wh, power to 0.1 W for clean HA states."""

    responses = make_responses()
    original_values = dict(VALUES[ROOT])
    VALUES[ROOT] = {
        "root-power": 103.98765,
        "root-consumed": 22177.123456,
        "root-produced": 27994.987654,
    }
    try:
        coordinator, client = make_coordinator(hass, responses)
        await coordinator.async_start(first_snapshot_timeout=1)
    finally:
        VALUES[ROOT] = original_values

    assert coordinator.data["grid_power"] == 104.0
    assert coordinator.data["grid_import_energy"] == 22177.123
    assert coordinator.data["grid_export_energy"] == 27994.988


@pytest.mark.asyncio
async def test_consumption_energy_ignores_tiny_counter_dips(hass):
    """The calculated household counter never dips by a few Wh."""

    coordinator, client = make_coordinator(hass, make_responses())
    await coordinator.async_start(first_snapshot_timeout=1)
    initial = coordinator.data["consumption_energy"]

    coordinator._states[BATTERY]["totalEnergyConsumed"] = 0.103
    coordinator._publish(force=True)
    assert coordinator.data["consumption_energy"] == pytest.approx(initial)

    coordinator._states[BATTERY]["totalEnergyConsumed"] = 10.0
    coordinator._publish(force=True)
    assert coordinator.data["consumption_energy"] < initial - 5
