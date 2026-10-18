"""Start/monitor the HACS config entry flow.

Run inside the Home Assistant container:

    docker exec homeassistant python /config/e2e/hacs_setup.py start
    docker exec homeassistant python /config/e2e/hacs_setup.py status
    docker exec homeassistant python /config/e2e/hacs_setup.py cancel
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import aiohttp
import jwt

BASE = "http://127.0.0.1:8123"
STATE_FILE = Path("/config/e2e/hacs_flow.json")


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


def short(result: dict) -> dict:
    return {
        key: result.get(key)
        for key in (
            "type",
            "step_id",
            "flow_id",
            "progress_action",
            "description_placeholders",
            "reason",
            "title",
        )
        if result.get(key) is not None
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "status", "cancel"))
    args = parser.parse_args()

    token = access_token()
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession() as session:
        if args.action == "start":
            async with session.post(
                f"{BASE}/api/config/config_entries/flow",
                headers=headers,
                json={"handler": "hacs"},
            ) as response:
                result = await response.json()
            print("initial:", json.dumps(short(result), indent=2))

            if result.get("type") == "form" and result.get("step_id") == "user":
                flow_id = result["flow_id"]
                async with session.post(
                    f"{BASE}/api/config/config_entries/flow/{flow_id}",
                    headers=headers,
                    json={
                        "acc_logs": True,
                        "acc_addons": True,
                        "acc_untested": True,
                        "acc_disable": True,
                    },
                ) as response:
                    result = await response.json()
                print("after user form:", json.dumps(short(result), indent=2))

            if result.get("flow_id"):
                STATE_FILE.write_text(
                    json.dumps({"flow_id": result["flow_id"]}, indent=2)
                )
            if result.get("type") == "create_entry":
                print("HACS ENTRY CREATED:", result.get("result"))
            elif result.get("type") == "progress":
                placeholders = result.get("description_placeholders") or {}
                print("AUTHORIZE HACS")
                print("URL :", placeholders.get("url"))
                print("CODE:", placeholders.get("code"))

        elif args.action == "status":
            state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
            flow_id = state.get("flow_id")
            if flow_id:
                async with session.get(
                    f"{BASE}/api/config/config_entries/flow/{flow_id}",
                    headers=headers,
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        print("flow:", json.dumps(short(result), indent=2))
                    else:
                        print("flow status:", response.status, await response.text())
            async with session.get(
                f"{BASE}/api/config/config_entries/entry",
                headers=headers,
            ) as response:
                entries = await response.json()
            hacs_entries = [
                entry for entry in entries if entry.get("domain") == "hacs"
            ]
            print("hacs entries:", json.dumps(hacs_entries, indent=2))

        elif args.action == "cancel":
            state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
            flow_id = state.get("flow_id")
            if flow_id:
                async with session.delete(
                    f"{BASE}/api/config/config_entries/flow/{flow_id}",
                    headers=headers,
                ) as response:
                    print("cancel:", response.status, await response.text())
                STATE_FILE.unlink(missing_ok=True)


asyncio.run(main())
