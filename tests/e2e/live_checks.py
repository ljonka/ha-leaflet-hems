"""Live checks for a Leaflet HEMS config entry.

Run inside the Home Assistant container:

    docker exec homeassistant python /config/e2e/live_checks.py [--title NAME]

The script resolves the config entry and all entity ids dynamically, so it
contains no device serial numbers, IP addresses or hardcoded entity ids.
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import time

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

from ha_helpers import DOMAIN, ENERGY_KEYS, POWER_KEYS, HA, access_token


class MDNSListener(ServiceListener):
    def __init__(self) -> None:
        self.found: list[tuple[str, dict]] = []

    def add_service(self, zc, type_, name):
        info = zc.get_service_info(type_, name)
        if info:
            properties = {
                (key.decode() if isinstance(key, bytes) else key): (
                    value.decode(errors="ignore")
                    if isinstance(value, bytes)
                    else value
                )
                for key, value in (info.properties or {}).items()
            }
            self.found.append((name, properties))

    def update_service(self, zc, type_, name):
        self.add_service(zc, type_, name)

    def remove_service(self, zc, type_, name):
        pass


async def check_mdns(expected_uuid: str) -> None:
    zc = Zeroconf()
    listener = MDNSListener()
    browser = ServiceBrowser(zc, "_jsonrpc._tcp.local.", listener)
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            await asyncio.sleep(0.5)
            if any(
                properties.get("uuid", "").strip("{}").lower()
                == expected_uuid.lower()
                for _, properties in listener.found
            ):
                break
    finally:
        browser.cancel()
        zc.close()

    matches = [
        (name, properties)
        for name, properties in listener.found
        if properties.get("uuid", "").strip("{}").lower() == expected_uuid.lower()
    ]
    assert matches, f"mDNS advertisement for {expected_uuid} not found"
    name, properties = matches[0]
    assert properties.get("manufacturer") == "nymea GmbH", properties
    assert properties.get("sslEnabled") == "true", properties
    assert int(properties.get("jsonrpcVersion", "0").split(".")[0]) >= 4, properties
    print("mDNS OK:", name, properties)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", default=None)
    parser.add_argument("--skip-mdns", action="store_true")
    args = parser.parse_args()

    async with HA(access_token()) as ha:
        entry = await ha.config_entry(args.title)
        print("config entry:", entry["entry_id"], entry["title"], entry.get("state"))
        assert entry.get("state") == "loaded", entry

        uuid_prefix, mapping = await ha.entity_map(entry["entry_id"])
        registry = await ha.ws_command("config/entity_registry/list")
        entry_entities = [
            item for item in registry if item.get("config_entry_id") == entry["entry_id"]
        ]
        assert len(entry_entities) == len(mapping) == 13, (
            f"expected 13 grouped entities, got {len(entry_entities)}"
        )
        print("entity registry OK:", len(entry_entities), "grouped entities")

        devices = await ha.ws_command("config/device_registry/list")
        device_ids = {
            item["id"]
            for item in devices
            if entry["entry_id"] in item.get("config_entries", [])
        }
        assert len(device_ids) == 5, (
            f"expected base + 4 group devices, got {len(device_ids)}"
        )
        print("device registry OK:", len(device_ids), "devices")

        states = await ha.states()
        values = {}
        for key, entity_id in mapping.items():
            state = states.get(entity_id)
            assert state is not None, f"missing state for {entity_id}"
            values[key] = state
            assert state["state"] not in ("unknown", "unavailable"), state
            numeric = float(state["state"])
            attributes = state["attributes"]
            if key in ENERGY_KEYS:
                assert attributes.get("device_class") == "energy", attributes
                assert attributes.get("state_class") == "total_increasing", attributes
                assert attributes.get("unit_of_measurement") == "kWh", attributes
            elif key in POWER_KEYS:
                assert attributes.get("device_class") == "power", attributes
                assert attributes.get("state_class") == "measurement", attributes
                assert attributes.get("unit_of_measurement") == "W", attributes
            print(entity_id, "=", numeric, attributes.get("unit_of_measurement"))

        # Vehicle/heat-pump style consumers are not part of this integration;
        # only the grouped electricity points are expected.
        assert set(values) == set(mapping)

        logs = await ha.ws_command("system_log/list")
        now = time.time()
        relevant = [
            item
            for item in logs
            if (
                DOMAIN in (item.get("name") or "")
                or DOMAIN in (item.get("message") or "")
            )
            and (item.get("timestamp") is None or item["timestamp"] > now - 600)
        ]
        errors = [
            item
            for item in relevant
            if item.get("level") in ("ERROR", "CRITICAL")
        ]
        assert not errors, errors[-3:]
        print("system log OK:", len(relevant), "recent entries, no errors")

        if not args.skip_mdns:
            await check_mdns(uuid_prefix)

    print("LIVE CHECKS PASSED")


asyncio.run(main())
