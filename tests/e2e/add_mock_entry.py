"""Add a Leaflet HEMS config entry through the manual config flow.

Run inside the HA container, for example for the local mock server:

    docker exec homeassistant python /config/e2e/add_mock_entry.py \
        --host 127.0.0.1 --port 12222 --name "Mock Leaflet" \
        --username test --password mock-password
"""

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
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=12222)
    parser.add_argument("--name", default="Mock Leaflet")
    parser.add_argument("--username", default="test")
    parser.add_argument("--password", default="mock-password")
    args = parser.parse_args()

    token = access_token()
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{BASE}/api/config/config_entries/flow",
            headers=headers,
            json={"handler": "leaflet_hems"},
        ) as response:
            result = await response.json()
        print("flow", result.get("flow_id"), result.get("step_id"), result.get("type"))

        user_input = {
            "host": args.host,
            "port": args.port,
            "name": args.name,
            "username": args.username,
            "password": args.password,
        }
        flow_id = result["flow_id"]
        async with session.post(
            f"{BASE}/api/config/config_entries/flow/{flow_id}",
            headers=headers,
            json=user_input,
        ) as response:
            result = await response.json()
        print("after manual", result.get("step_id"), result.get("type"))

        if result.get("step_id") == "auth":
            async with session.post(
                f"{BASE}/api/config/config_entries/flow/{flow_id}",
                headers=headers,
                json={
                    "username": args.username,
                    "password": args.password,
                },
            ) as response:
                result = await response.json()
            print("after auth", result.get("step_id"), result.get("type"))

        print(json.dumps(result, indent=2)[:4000])
        if result.get("type") != "create_entry":
            raise SystemExit("config entry was not created")


asyncio.run(main())
