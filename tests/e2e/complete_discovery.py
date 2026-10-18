"""Complete the Leaflet HEMS Zeroconf discovery flow for a given device UUID."""

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


async def ws_command(session, token, type_, **kwargs):
    async with session.ws_connect(f"{BASE}/api/websocket") as ws:
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": token})
        auth = await ws.receive_json()
        assert auth["type"] == "auth_ok", auth
        await ws.send_json({"id": 1, "type": type_, **kwargs})
        while True:
            message = await ws.receive_json()
            if message.get("id") == 1:
                assert message.get("success"), message
                return message["result"]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uuid", required=True)
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--wait", type=float, default=30)
    args = parser.parse_args()

    token = access_token()
    target = args.uuid.strip("{}").lower()
    async with aiohttp.ClientSession() as session:
        flow = None
        deadline = time.time() + args.wait
        while time.time() < deadline:
            flows = await ws_command(
                session, token, "config_entries/flow/progress"
            )
            for item in flows:
                if item.get("handler") != "leaflet_hems":
                    continue
                unique_id = (item.get("context") or {}).get("unique_id", "")
                if unique_id.strip("{}").lower() == target:
                    flow = item
                    break
            if flow:
                break
            await asyncio.sleep(1)

        if not flow:
            flows = await ws_command(
                session, token, "config_entries/flow/progress"
            )
            print("leaflet flows:")
            for item in flows:
                if item.get("handler") == "leaflet_hems":
                    print(json.dumps(item, indent=2))
            raise SystemExit("discovery flow not found")

        flow_id = flow["flow_id"]
        print("flow", flow_id, "step", flow["step_id"])

        headers = {"Authorization": f"Bearer {token}"}
        async with session.get(
            f"{BASE}/api/config/config_entries/flow/{flow_id}", headers=headers
        ) as response:
            result = await response.json()
        print("current step", result.get("step_id"), result.get("type"))

        if result.get("step_id") == "discovery_confirm":
            async with session.post(
                f"{BASE}/api/config/config_entries/flow/{flow_id}",
                headers=headers,
                json={},
            ) as response:
                result = await response.json()
            print("after confirm", result.get("step_id"), result.get("type"))

        if result.get("step_id") == "auth":
            if not args.username or not args.password:
                raise SystemExit("credentials required for auth step")
            async with session.post(
                f"{BASE}/api/config/config_entries/flow/{flow_id}",
                headers=headers,
                json={
                    "username": args.username,
                    "password": args.password,
                },
            ) as response:
                result = await response.json()
            print("after auth", result.get("type"), result.get("title"))

        print(json.dumps(result, indent=2)[:4000])


asyncio.run(main())
