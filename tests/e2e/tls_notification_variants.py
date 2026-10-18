"""Test JSONRPC.SetNotificationStatus variants on a Leaflet TLS connection."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

sys.path.insert(0, "/config")

from custom_components.leaflet_hems.client import NymeaClient


def entry_data(title: str | None) -> dict:
    entries = json.load(open("/config/.storage/core.config_entries"))["data"][
        "entries"
    ]
    leaflet = [entry for entry in entries if entry["domain"] == "leaflet_hems"]
    if title:
        leaflet = [entry for entry in leaflet if entry["title"] == title]
    if len(leaflet) != 1:
        raise SystemExit(
            f"Expected one Leaflet entry, got {[entry['title'] for entry in leaflet]}"
        )
    return leaflet[0]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", default=None)
    parser.add_argument("--seconds", type=float, default=6)
    args = parser.parse_args()

    entry = entry_data(args.title)
    data = entry["data"]
    client = NymeaClient(
        data["host"],
        int(data["port"]),
        token=data["nymea_token"],
        device_name="ha-e2e-notification-variant-probe",
    )
    counts: dict[str, int] = {}

    def on_notification(notification: dict) -> None:
        method = notification.get("notification") or notification.get("method") or "?"
        counts[method] = counts.get(method, 0) + 1

    client.register_notification_callback(on_notification)
    variants = [
        ("namespaces", {"namespaces": ["Energy", "Hems", "Integrations"]}),
        (
            "enabled+namespaces",
            {"enabled": True, "namespaces": ["Energy", "Hems", "Integrations"]},
        ),
        ("enabled", {"enabled": True}),
        ("energy-only", {"namespaces": ["Energy"]}),
        ("integrations-only", {"namespaces": ["Integrations"]}),
    ]
    try:
        await client.async_connect()
        print("connected and initial subscription done")
        for name, params in variants:
            before = sum(counts.values())
            response = await client.request("JSONRPC.SetNotificationStatus", params)
            print(
                "variant",
                name,
                "response",
                json.dumps(response.get("params")),
            )
            await asyncio.sleep(args.seconds)
            after = sum(counts.values())
            print("  notifications in window:", after - before)
        print("counts", json.dumps(counts, indent=2))
    finally:
        await client.async_close()


asyncio.run(main())
