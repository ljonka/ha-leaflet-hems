import asyncio
import json
import time

import aiohttp
import jwt


def access_token() -> str:
    data = json.load(open("/config/.storage/auth"))["data"]
    owners = [user for user in data["users"] if user.get("is_owner")]
    owner = owners[0]["id"]
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
    token = access_token()
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect("http://127.0.0.1:8123/api/websocket") as ws:
            hello = await ws.receive_json()
            assert hello["type"] == "auth_required", hello
            await ws.send_json({"type": "auth", "access_token": token})
            auth = await ws.receive_json()
            assert auth["type"] == "auth_ok", auth
            await ws.send_json({"id": 1, "type": "config_entries/flow/progress"})
            while True:
                message = await ws.receive_json()
                if message.get("id") == 1:
                    print(json.dumps(message, indent=2)[:8000])
                    return


asyncio.run(main())
