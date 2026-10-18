"""List or remove a Leaflet HEMS config entry by device UUID."""

from __future__ import annotations

import argparse
import asyncio
import json
import time

import aiohttp
import jwt

BASE = "http://127.0.0.1:8123"


def access_token() -> str:
    data = json.load(open("/config/.storage/auth"))["data"]
    owner = next(user["id"] for user in data["users"] if user.get("is_owner"))
    tokens = [
        token
        for token in data["refresh_tokens"]
        if token["user_id"] == owner and token["token_type"] == "normal"
    ]
    token = max(tokens, key=lambda item: item["created_at"])
    now = int(time.time())
    return jwt.encode(
        {"iss": token["id"], "iat": now, "exp": now + 1800},
        token["jwt_key"],
        algorithm="HS256",
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uuid", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()

    token = access_token()
    target = args.uuid.strip("{}").lower()
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"{BASE}/api/websocket") as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": token})
            auth = await ws.receive_json()
            assert auth["type"] == "auth_ok", auth
            await ws.send_json(
                {
                    "id": 1,
                    "type": "config_entries/get",
                    "domain": "leaflet_hems",
                }
            )
            while True:
                message = await ws.receive_json()
                if message.get("id") == 1:
                    assert message.get("success"), message
                    entries = message["result"]
                    break

        if args.title:
            matches = [entry for entry in entries if entry.get("title") == args.title]
        else:
            matches = [
                entry
                for entry in entries
                if (entry.get("unique_id") or "").strip("{}").lower() == target
            ]
        if not matches:
            print("no entry for", target)
            print("entries:", [(e.get("title"), e.get("unique_id")) for e in entries])
            return
        entry = matches[0]
        print(
            "entry",
            entry["entry_id"],
            entry["title"],
            entry.get("state"),
            entry.get("unique_id"),
        )
        if args.remove:
            async with session.delete(
                f"{BASE}/api/config/config_entries/entry/{entry['entry_id']}",
                headers=headers,
            ) as response:
                body = await response.text()
                print("remove", response.status, body)


asyncio.run(main())
