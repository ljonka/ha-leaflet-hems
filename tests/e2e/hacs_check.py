"""Check HACS update detection for the Leaflet HEMS repository.

Run inside the Home Assistant container:

    docker exec homeassistant python /config/e2e/hacs_check.py [--install]

Only the public repository name is used; no device-specific data.
"""

from __future__ import annotations

import argparse
import asyncio

from ha_helpers import HA, access_token

REPOSITORY = "ljonka/ha-leaflet-hems"


async def find_repository(ha: HA) -> dict:
    repositories = await ha.ws_command("hacs/repositories/list")
    matches = [
        repository
        for repository in repositories
        if repository.get("full_name") == REPOSITORY
    ]
    if not matches:
        raise RuntimeError(f"{REPOSITORY} is not registered in HACS")
    return matches[0]


def print_repository(repository: dict) -> None:
    print(
        repository.get("full_name"),
        "installed=",
        repository.get("installed_version"),
        "available=",
        repository.get("available_version"),
        "pending_upgrade=",
        repository.get("pending_upgrade"),
        "status=",
        repository.get("status"),
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()

    async with HA(access_token()) as ha:
        repository = await find_repository(ha)
        print_repository(repository)
        repository_id = str(repository["id"])

        await ha.ws_command("hacs/repository/refresh", repository=repository_id)
        info = await ha.ws_command(
            "hacs/repository/info", repository_id=repository_id
        )
        print_repository(info)
        if info.get("available_version") == info.get("installed_version"):
            print("HACS is already up to date")
            return
        assert info.get("pending_upgrade") is True, info

        if args.install:
            await ha.ws_command(
                "hacs/repository/download",
                repository=repository_id,
                version=info["available_version"],
            )
            info = await ha.ws_command(
                "hacs/repository/info", repository_id=repository_id
            )
            print_repository(info)
            assert (
                info.get("installed_version") == info.get("available_version")
            ), info
            assert info.get("pending_upgrade") is False, info
            print("HACS UPDATE INSTALLED")


asyncio.run(main())
