"""Shared helpers for the live Leaflet HEMS checks.

These scripts are intended to run inside the Home Assistant container:

    docker exec homeassistant python /config/e2e/<script>.py

No device serial number, entity id or IP is hardcoded.  The config entry is
selected by title (when more than one Leaflet integration is configured) and
all entity ids are resolved through the entity registry.
"""

from __future__ import annotations

import json
import time
from typing import Any

import aiohttp
import jwt

BASE = "http://127.0.0.1:8123"
DOMAIN = "leaflet_hems"

SENSOR_KEYS = [
    "grid_import_power",
    "grid_export_power",
    "grid_import_energy",
    "grid_export_energy",
    "generation_power",
    "generation_energy",
    "storage_power",
    "storage_charging_power",
    "storage_discharging_power",
    "storage_energy_stored",
    "storage_energy_provided",
    "consumption_power",
    "consumption_energy",
]

ENERGY_KEYS = {
    "grid_import_energy",
    "grid_export_energy",
    "generation_energy",
    "storage_energy_stored",
    "storage_energy_provided",
    "consumption_energy",
}

POWER_KEYS = {
    "grid_import_power",
    "grid_export_power",
    "generation_power",
    "storage_power",
    "storage_charging_power",
    "storage_discharging_power",
    "consumption_power",
}


def access_token() -> str:
    """Mint a short-lived Home Assistant access token from the owner token."""

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


class HA:
    """Small Home Assistant REST/WebSocket client."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.session: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.next_id = 1

    async def __aenter__(self) -> "HA":
        self.session = aiohttp.ClientSession()
        self.ws = await self.session.ws_connect(f"{BASE}/api/websocket")
        hello = await self.ws.receive_json()
        assert hello["type"] == "auth_required", hello
        await self.ws.send_json({"type": "auth", "access_token": self.token})
        auth = await self.ws.receive_json()
        assert auth["type"] == "auth_ok", auth
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self.ws is not None:
            await self.ws.close()
        if self.session is not None:
            await self.session.close()

    async def ws_command(self, type_: str, **kwargs: Any) -> Any:
        command_id = self.next_id
        self.next_id += 1
        await self.ws.send_json({"id": command_id, "type": type_, **kwargs})
        while True:
            message = await self.ws.receive_json()
            if message.get("id") == command_id:
                if not message.get("success"):
                    raise RuntimeError(f"{type_} failed: {message}")
                return message["result"]

    async def states(self) -> dict[str, dict[str, Any]]:
        async with self.session.get(
            f"{BASE}/api/states",
            headers={"Authorization": f"Bearer {self.token}"},
        ) as response:
            response.raise_for_status()
            return {state["entity_id"]: state for state in await response.json()}

    async def config_entry(self, title: str | None = None) -> dict[str, Any]:
        entries = await self.ws_command("config_entries/get", domain=DOMAIN)
        if title:
            matches = [entry for entry in entries if entry["title"] == title]
            if not matches:
                raise RuntimeError(
                    f"No Leaflet entry titled {title!r}; available: "
                    f"{[entry['title'] for entry in entries]}"
                )
            return matches[0]
        if len(entries) != 1:
            raise RuntimeError(
                "More than one Leaflet config entry; pass --title. Available: "
                f"{[entry['title'] for entry in entries]}"
            )
        return entries[0]

    async def entity_map(
        self, entry_id: str
    ) -> tuple[str, dict[str, str]]:
        """Return (uuid_prefix, {sensor_key: entity_id})."""

        registry = await self.ws_command("config/entity_registry/list")
        entries = [
            item for item in registry if item.get("config_entry_id") == entry_id
        ]
        mapping: dict[str, str] = {}
        uuid_prefix = ""
        for key in SENSOR_KEYS:
            suffix = f"_{key}"
            for item in entries:
                unique_id = item.get("unique_id") or ""
                if unique_id.endswith(suffix):
                    mapping[key] = item["entity_id"]
                    uuid_prefix = unique_id[: -len(suffix)]
                    break
        missing = set(SENSOR_KEYS) - set(mapping)
        if missing or not uuid_prefix:
            raise RuntimeError(
                f"Could not resolve grouped entities (missing={sorted(missing)}, "
                f"uuid_prefix={uuid_prefix!r})"
            )
        return uuid_prefix, mapping
