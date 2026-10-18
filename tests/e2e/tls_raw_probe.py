"""Raw TLS probe testing whether JSONRPC.Hello must use id 0 for notifications."""

from __future__ import annotations

import argparse
import asyncio
import json
import ssl


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
    return leaflet[0]["data"]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", default=None)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--hello-id", type=int, default=0)
    args = parser.parse_args()

    data = entry_data(args.title)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    reader, writer = await asyncio.open_connection(data["host"], int(data["port"]), ssl=ctx)
    token = data["nymea_token"]
    counts: dict[str, int] = {}
    samples: dict[str, dict] = {}

    async def send(request_id: int, method: str, params: dict | None = None):
        payload = {"id": request_id, "method": method, "token": token}
        if params is not None:
            payload["params"] = params
        writer.write(json.dumps(payload).encode() + b"\n")
        await writer.drain()

    async def read_message():
        line = await reader.readline()
        return json.loads(line)

    try:
        await send(args.hello_id, "JSONRPC.Hello", {"locale": "de_DE"})
        hello = await read_message()
        print("hello id", args.hello_id, "status", hello.get("status"), "authenticated", hello.get("params", {}).get("authenticated"))
        await send(1, "JSONRPC.SetNotificationStatus", {"namespaces": ["Energy", "Hems", "Integrations"]})
        status = await read_message()
        print("notification status", json.dumps(status.get("params")))
        end = asyncio.get_running_loop().time() + args.seconds
        while True:
            timeout = end - asyncio.get_running_loop().time()
            if timeout <= 0:
                break
            try:
                message = await asyncio.wait_for(read_message(), timeout=timeout)
            except asyncio.TimeoutError:
                break
            method = message.get("notification") or message.get("method") or "?"
            counts[method] = counts.get(method, 0) + 1
            samples.setdefault(method, message)
        print("counts", json.dumps(counts, indent=2))
        for method, sample in samples.items():
            print("sample", method, json.dumps(sample)[:600])
    finally:
        writer.close()
        await writer.wait_closed()


asyncio.run(main())
