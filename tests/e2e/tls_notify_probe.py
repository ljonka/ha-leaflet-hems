"""Connect to a Leaflet entry over TLS+token and print notifications.

Run inside the Home Assistant container:

    docker exec homeassistant python /config/e2e/tls_notify_probe.py \
        [--title NAME] [--seconds 15]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

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
    parser.add_argument("--seconds", type=float, default=15)
    args = parser.parse_args()

    entry = entry_data(args.title)
    data = entry["data"]
    client = NymeaClient(
        data["host"],
        int(data["port"]),
        token=data["nymea_token"],
        device_name="ha-e2e-tls-notify-probe",
    )
    counts: dict[str, int] = {}
    samples: dict[str, dict] = {}

    def on_notification(notification: dict) -> None:
        method = notification.get("notification") or notification.get("method") or "?"
        counts[method] = counts.get(method, 0) + 1
        samples.setdefault(method, notification)

    client.register_notification_callback(on_notification)
    try:
        await client.async_connect()
        print("connected", client.host, client.port)
        print("ready", client._ready, "reader_done", client._reader_task.done(), "callbacks", len(client._notification_callbacks))
        await asyncio.sleep(args.seconds)
    finally:
        await client.async_close()

    print("counts", json.dumps(counts, indent=2))
    for method, sample in samples.items():
        print("sample", method, json.dumps(sample)[:1000])


asyncio.run(main())
