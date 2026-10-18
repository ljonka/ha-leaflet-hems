"""Configure the Home Assistant Energy Dashboard for a Leaflet HEMS entry.

Run inside the Home Assistant container:

    docker exec homeassistant python /config/e2e/setup_energy.py [--title NAME]

The script is idempotent and resolves all entity ids dynamically.
"""

from __future__ import annotations

import argparse
import asyncio

from ha_helpers import HA, access_token


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", default=None)
    args = parser.parse_args()

    async with HA(access_token()) as ha:
        entry = await ha.config_entry(args.title)
        _uuid, mapping = await ha.entity_map(entry["entry_id"])

        energy_sources = [
            {
                "type": "grid",
                "stat_energy_from": mapping["grid_import_energy"],
                "stat_energy_to": mapping["grid_export_energy"],
                "stat_cost": None,
                "stat_compensation": None,
                "entity_energy_price": None,
                "number_energy_price": None,
                "entity_energy_price_export": None,
                "number_energy_price_export": None,
                "power_config": {
                    "stat_rate_from": mapping["grid_import_power"],
                    "stat_rate_to": mapping["grid_export_power"],
                },
                "cost_adjustment_day": 0.0,
            },
            {
                "type": "solar",
                "stat_energy_from": mapping["generation_energy"],
                "stat_rate": mapping["generation_power"],
                "config_entry_solar_forecast": None,
            },
            {
                "type": "battery",
                "stat_energy_from": mapping["storage_energy_provided"],
                "stat_energy_to": mapping["storage_energy_stored"],
                "power_config": {
                    "stat_rate_from": mapping["storage_discharging_power"],
                    "stat_rate_to": mapping["storage_charging_power"],
                },
            },
        ]

        saved = await ha.ws_command(
            "energy/save_prefs",
            energy_sources=energy_sources,
            device_consumption=[],
            device_consumption_water=[],
        )
        print("saved energy sources:")
        for source in saved["energy_sources"]:
            print(" ", source["type"], source.get("stat_energy_from"))

        validation = await ha.ws_command("energy/validate")
        issues = [
            issue
            for source in validation.get("energy_sources", [])
            for issue in source
        ]
        issues += validation.get("device_consumption", [])
        if issues:
            print("validation issues (statistics may need a few minutes):")
            for issue in issues:
                print(" ", issue)
        else:
            print("Energy validation: OK")


asyncio.run(main())
