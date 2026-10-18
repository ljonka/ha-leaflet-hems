"""Test doubles for the Leaflet HEMS integration."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from custom_components.leaflet_hems.client import NymeaResponseError


class FakeNymeaServer:
    """A tiny line-based JSON-RPC server used by client tests."""

    def __init__(self) -> None:
        self.server: asyncio.AbstractServer | None = None
        self.port = 0
        self.requests: list[dict[str, Any]] = []
        self.connections = 0
        self.writers: list[asyncio.StreamWriter] = []
        self.require_auth = False
        self.valid_token = "valid-token"
        self.valid_password = "secret"
        self.auth_success_false = False
        self.hello_params: dict[str, Any] = {
            "uuid": "{12345678-1234-1234-1234-123456789abc}",
            "name": "Test Leaflet",
            "version": "1.7.26",
            "protocol version": "7.1",
            "initialSetupRequired": False,
            "pushButtonAuthAvailable": False,
        }
        self.responses: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        self.hang_methods: set[str] = set()
        self.request_event = asyncio.Event()

    async def start(self) -> None:
        self.server = await asyncio.start_server(
            self._handle_client, "127.0.0.1", 0
        )
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for writer in list(self.writers):
            writer.close()
        self.writers.clear()
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.connections += 1
        self.writers.append(writer)
        try:
            while line := await reader.readline():
                message = json.loads(line)
                self.requests.append(message)
                self.request_event.set()
                response = self.handle_message(message)
                if response is not None:
                    writer.write(json.dumps(response).encode() + b"\n")
                    await writer.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass
        finally:
            if writer in self.writers:
                self.writers.remove(writer)
            writer.close()

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        params = message.get("params") or {}
        request_id = message.get("id")

        if method == "JSONRPC.Hello":
            authenticated = not self.require_auth or (
                message.get("token") == self.valid_token
            )
            return {
                "id": request_id,
                "status": "success",
                "params": {
                    **self.hello_params,
                    "authenticationRequired": self.require_auth,
                    "authenticated": authenticated,
                },
            }

        if method == "JSONRPC.Authenticate":
            if self.auth_success_false:
                return {
                    "id": request_id,
                    "status": "success",
                    "params": {"success": False},
                }
            if params.get("password") == self.valid_password:
                return {
                    "id": request_id,
                    "status": "success",
                    "params": {"token": self.valid_token},
                }
            return {
                "id": request_id,
                "status": "error",
                "error": "Invalid credentials",
            }

        if method == "JSONRPC.SetNotificationStatus":
            return {
                "id": request_id,
                "status": "success",
                "params": {
                    "enabled": True,
                    "namespaces": params.get("namespaces", []),
                },
            }

        if method == "JSONRPC.KeepAlive":
            return {"id": request_id, "status": "success", "params": params}

        if method in self.hang_methods:
            return None

        handler = self.responses.get(method)
        if handler is not None:
            result = handler(params)
            if isinstance(result, Exception):
                return {
                    "id": request_id,
                    "status": "error",
                    "error": str(result),
                }
            return {"id": request_id, "status": "success", "params": result}

        return {
            "id": request_id,
            "status": "error",
            "error": f"Method not found: {method}",
        }

    async def send_notification(
        self, notification: str, params: dict[str, Any]
    ) -> None:
        payload = json.dumps(
            {"notification": notification, "params": params}
        ).encode() + b"\n"
        for writer in list(self.writers):
            writer.write(payload)
            await writer.drain()

    async def disconnect_all(self) -> None:
        for writer in list(self.writers):
            writer.close()
            await writer.wait_closed()
        self.writers.clear()

    def requests_for(self, method: str) -> list[dict[str, Any]]:
        return [request for request in self.requests if request.get("method") == method]

    async def wait_for_requests(
        self, method: str, count: int = 1, timeout: float = 2.0
    ) -> list[dict[str, Any]]:
        async def waiter() -> None:
            while len(self.requests_for(method)) < count:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(waiter(), timeout=timeout)
        return self.requests_for(method)


class FakeNymeaClient:
    """A request-only fake used by coordinator tests."""

    def __init__(
        self, responses: dict[str, Callable[[dict[str, Any]], dict[str, Any]]]
    ) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.notification_callbacks: dict[str, Callable[[dict], Any]] = {}
        self.connected_callbacks: list[Callable] = []
        self.disconnected_callbacks: list[Callable] = []
        self.auth_failed_callbacks: list[Callable] = []
        self.started = False
        self.stopped = False

    async def request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls.append((method, params))
        handler = self.responses.get(method)
        if handler is None:
            raise NymeaResponseError(
                method, {"status": "error", "error": "Method not found"}
            )
        result = handler(params or {})
        if isinstance(result, Exception):
            raise result
        return {"status": "success", "params": result}

    def register_notification_callback(self, callback: Callable) -> str:
        token = f"cb-{len(self.notification_callbacks)}"
        self.notification_callbacks[token] = callback
        return token

    def unregister_notification_callback(self, token: str) -> None:
        self.notification_callbacks.pop(token, None)

    def register_connected_callback(self, callback: Callable) -> None:
        self.connected_callbacks.append(callback)

    def register_disconnected_callback(self, callback: Callable) -> None:
        self.disconnected_callbacks.append(callback)

    def unregister_connected_callback(self, callback: Callable) -> None:
        if callback in self.connected_callbacks:
            self.connected_callbacks.remove(callback)

    def unregister_disconnected_callback(self, callback: Callable) -> None:
        if callback in self.disconnected_callbacks:
            self.disconnected_callbacks.remove(callback)

    def register_auth_failed_callback(self, callback: Callable) -> None:
        self.auth_failed_callbacks.append(callback)

    async def async_start(self) -> None:
        self.started = True
        for callback in list(self.connected_callbacks):
            await callback(self, None)

    async def async_stop(self) -> None:
        self.stopped = True

    def emit(self, notification: dict[str, Any]) -> None:
        for callback in list(self.notification_callbacks.values()):
            callback(notification)
