"""TDD checks for the Leaflet HEMS electricity data and Energy Dashboard setup.

Run inside the Home Assistant container:

    docker exec homeassistant python /config/e2e/energy_checks.py [--title NAME]

All entity ids are resolved dynamically from the config entry's entity
registry.  No device serial number or entity id is hardcoded.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from datetime import datetime, timedelta, timezone

from ha_helpers import ENERGY_KEYS, POWER_KEYS, HA, access_token

# A single-family house with an 8 kWp PV system cannot accumulate GWh.
MAX_ENERGY_KWH = 200_000.0
MAX_CONSUMPTION_KWH = 300_000.0
MAX_DAILY_CHANGE_KWH = 1_000.0
MAX_POWER_W = 100_000.0


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", default=None)
    parser.add_argument("--validation-wait", type=float, default=0)
    args = parser.parse_args()

    async with HA(access_token()) as ha:
        entry = await ha.config_entry(args.title)
        uuid_prefix, mapping = await ha.entity_map(entry["entry_id"])
        states = await ha.states()

        for key in sorted(ENERGY_KEYS):
            entity_id = mapping[key]
            state = states.get(entity_id)
            assert state is not None, f"missing {entity_id}"
            attributes = state["attributes"]
            assert state["state"] not in ("unknown", "unavailable"), state
            value = float(state["state"])
            assert attributes.get("device_class") == "energy", attributes
            assert attributes.get("state_class") == "total_increasing", attributes
            assert attributes.get("unit_of_measurement") == "kWh", attributes
            limit = (
                MAX_CONSUMPTION_KWH
                if key == "consumption_energy"
                else MAX_ENERGY_KWH
            )
            assert 0 <= value < limit, f"{entity_id} looks implausible: {value}"
            print(f"{entity_id} = {value} kWh")

        for key in sorted(POWER_KEYS):
            entity_id = mapping[key]
            state = states.get(entity_id)
            assert state is not None, f"missing {entity_id}"
            attributes = state["attributes"]
            assert state["state"] not in ("unknown", "unavailable"), state
            value = float(state["state"])
            assert attributes.get("device_class") == "power", attributes
            assert attributes.get("state_class") == "measurement", attributes
            assert attributes.get("unit_of_measurement") == "W", attributes
            assert abs(value) < MAX_POWER_W, f"{entity_id} looks implausible: {value}"
            print(f"{entity_id} = {value} W")

        prefs = await ha.ws_command("energy/get_prefs")
        sources = {source["type"]: source for source in prefs["energy_sources"]}
        grid = sources["grid"]
        assert grid["stat_energy_from"] == mapping["grid_import_energy"], grid
        assert grid["stat_energy_to"] == mapping["grid_export_energy"], grid
        assert (
            grid["power_config"]["stat_rate_from"] == mapping["grid_import_power"]
        ), grid
        assert (
            grid["power_config"]["stat_rate_to"] == mapping["grid_export_power"]
        ), grid

        solar = sources["solar"]
        assert solar["stat_energy_from"] == mapping["generation_energy"], solar
        assert solar["stat_rate"] == mapping["generation_power"], solar

        battery = sources["battery"]
        assert (
            battery["stat_energy_from"] == mapping["storage_energy_provided"]
        ), battery
        assert battery["stat_energy_to"] == mapping["storage_energy_stored"], battery
        assert (
            battery["power_config"]["stat_rate_from"]
            == mapping["storage_discharging_power"]
        ), battery
        assert (
            battery["power_config"]["stat_rate_to"]
            == mapping["storage_charging_power"]
        ), battery
        print("Energy preferences reference the grouped sensors")

        for generated in (grid["stat_rate"], battery["stat_rate"]):
            state = states.get(generated)
            assert state is not None, f"missing generated power sensor {generated}"
            float(state["state"])
            print(f"{generated} = {state['state']} W")

        energy_entity_ids = sorted(mapping[key] for key in ENERGY_KEYS)
        start = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        end = datetime.now(timezone.utc).isoformat()
        statistics = await ha.ws_command(
            "recorder/statistics_during_period",
            start_time=start,
            end_time=end,
            statistic_ids=energy_entity_ids,
            period="day",
            types=["change", "state", "sum"],
        )
        for statistic_id in energy_entity_ids:
            rows = statistics.get(statistic_id) or []
            assert rows, f"no long-term statistics for {statistic_id}"
            last = rows[-1]
            state_value = last.get("state")
            assert state_value is not None, (statistic_id, last)
            assert state_value < MAX_CONSUMPTION_KWH, (statistic_id, state_value)
            assert (last.get("sum") or 0) < MAX_CONSUMPTION_KWH, (
                statistic_id,
                last.get("sum"),
            )
            for row in rows:
                change = row.get("change")
                if change is None:
                    continue
                assert change >= -0.001, (statistic_id, row)
                assert change < MAX_DAILY_CHANGE_KWH, (statistic_id, row)
            print(
                f"stats {statistic_id}: state={state_value} sum={last.get('sum')} "
                f"last_change={last.get('change')}"
            )

        deadline = time.time() + args.validation_wait
        while True:
            validation = await ha.ws_command("energy/validate")
            issues = [
                issue
                for source in validation.get("energy_sources", [])
                for issue in source
            ]
            issues += validation.get("device_consumption", [])
            if not issues:
                print("Energy validation: OK")
                break
            if time.time() >= deadline:
                raise AssertionError(f"Energy validation issues: {issues}")
            await asyncio.sleep(10)

    print("ENERGY CHECKS PASSED")


asyncio.run(main())
