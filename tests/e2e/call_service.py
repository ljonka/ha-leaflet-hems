"""Call a Home Assistant service from inside the HA container."""

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
    parser.add_argument("domain")
    parser.add_argument("service")
    parser.add_argument("--data", default="{}")
    args = parser.parse_args()

    token = access_token()
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{BASE}/api/services/{args.domain}/{args.service}",
            headers={"Authorization": f"Bearer {token}"},
            json=json.loads(args.data),
        ) as response:
            print(response.status, await response.text())


asyncio.run(main())
