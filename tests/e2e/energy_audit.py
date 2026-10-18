"""Audit Home Assistant Energy preferences and Leaflet statistics.

Run inside the Home Assistant container:

    docker exec homeassistant python /config/e2e/energy_audit.py [--title NAME]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone

from ha_helpers import ENERGY_KEYS, HA, access_token


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", default=None)
    args = parser.parse_args()

    async with HA(access_token()) as ha:
        entry = await ha.config_entry(args.title)
        _uuid, mapping = await ha.entity_map(entry["entry_id"])

        prefs = await ha.ws_command("energy/get_prefs")
        print("ENERGY PREFS")
        print(json.dumps(prefs, indent=2))

        validation = await ha.ws_command("energy/validate")
        print("ENERGY VALIDATION")
        print(json.dumps(validation, indent=2))

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
        print("STATISTICS (last 7 days)")
        for statistic_id in energy_entity_ids:
            rows = statistics.get(statistic_id) or []
            if not rows:
                print(statistic_id, "no rows")
                continue
            first = rows[0]
            last = rows[-1]
            print(
                statistic_id,
                "first_state=",
                first.get("state"),
                "last_state=",
                last.get("state"),
                "last_sum=",
                last.get("sum"),
                "last_change=",
                last.get("change"),
            )


asyncio.run(main())
