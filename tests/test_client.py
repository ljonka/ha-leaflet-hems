"""Tests for the persistent nymea JSON-RPC client."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from custom_components.leaflet_hems.client import (
    NymeaAuthenticationError,
    NymeaClient,
    NymeaConnectionError,
)

from .fake_nymea import FakeNymeaServer


@pytest.fixture
async def server(socket_enabled):
    fake = FakeNymeaServer()
    await fake.start()
    try:
        yield fake
    finally:
        await fake.stop()


def make_client(server: FakeNymeaServer, **kwargs) -> NymeaClient:
    defaults = {
        "use_tls": False,
        "request_timeout": 2.0,
        "keepalive_interval": 3600.0,
        "reconnect_initial_delay": 0.05,
        "reconnect_max_delay": 0.1,
    }
    defaults.update(kwargs)
    return NymeaClient("127.0.0.1", server.port, **defaults)


@pytest.mark.asyncio
async def test_connect_handshake_and_subscribe(server):
    """A connection performs Hello and enables the required namespaces."""

    client = make_client(server)
    try:
        hello = await client.async_connect()
        assert hello["uuid"] == "{12345678-1234-1234-1234-123456789abc}"
        assert client.is_connected
        hello_requests = server.requests_for("JSONRPC.Hello")
        assert hello_requests
        assert hello_requests[0]["id"] == 0, "Hello must use request id 0"
        notify = server.requests_for("JSONRPC.SetNotificationStatus")
        assert notify
        assert notify[0]["params"]["namespaces"] == [
            "Energy",
            "Hems",
            "Integrations",
        ]
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_authentication_uses_credentials_and_stores_token(server):
    """A device requiring authentication is authenticated on the same socket."""

    server.require_auth = True
    client = make_client(server, username="user", password="secret")
    try:
        await client.async_connect()
        assert client.token == server.valid_token
        assert len(server.requests_for("JSONRPC.Authenticate")) == 1
        hello_requests = server.requests_for("JSONRPC.Hello")
        assert len(hello_requests) == 2
        assert all(request["id"] == 0 for request in hello_requests)
        assert hello_requests[1]["token"] == server.valid_token
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_authentication_success_false_raises(server):
    """nymea returns status=success with success=false for bad credentials."""

    server.require_auth = True
    server.auth_success_false = True
    client = make_client(server, username="user", password="secret")
    try:
        with pytest.raises(NymeaAuthenticationError):
            await client.async_connect()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_authentication_required_without_credentials(server):
    """Missing credentials produce a clear authentication error."""

    server.require_auth = True
    client = make_client(server)
    try:
        with pytest.raises(NymeaAuthenticationError):
            await client.async_connect()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_concurrent_requests_are_correlated(server):
    """Responses are matched by request id even when sent concurrently."""

    server.responses["Test.Slow"] = lambda params: {"value": "slow"}
    server.responses["Test.Fast"] = lambda params: {"value": "fast"}
    client = make_client(server)
    try:
        await client.async_connect()
        slow, fast = await asyncio.gather(
            client.request("Test.Slow"),
            client.request("Test.Fast"),
        )
        assert slow["params"]["value"] == "slow"
        assert fast["params"]["value"] == "fast"
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_notifications_are_dispatched(server):
    """Unsolicited notifications reach registered callbacks."""

    client = make_client(server)
    received: asyncio.Queue = asyncio.Queue()
    client.register_notification_callback(received.put_nowait)
    try:
        await client.async_connect()
        await server.send_notification(
            "Energy.PowerBalanceChanged",
            {"currentPowerConsumption": 1234},
        )
        notification = await asyncio.wait_for(received.get(), timeout=2)
        assert notification["notification"] == "Energy.PowerBalanceChanged"
        assert notification["params"]["currentPowerConsumption"] == 1234
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_keepalive_uses_generated_session_id(server):
    """Keepalive must not send a null sessionId (the old bug)."""

    client = make_client(server, keepalive_interval=0.05)
    try:
        await client.async_connect()
        requests = await server.wait_for_requests(
            "JSONRPC.KeepAlive", count=2, timeout=2
        )
        session_ids = [request["params"]["sessionId"] for request in requests]
        assert all(isinstance(value, str) and value for value in session_ids)
        assert len(set(session_ids)) == 1
        uuid.UUID(session_ids[0])
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_disconnect_fails_pending_requests(server):
    """A dropped connection fails in-flight requests instead of hanging."""

    server.hang_methods.add("Test.Hang")
    client = make_client(server, request_timeout=30.0)
    try:
        await client.async_connect()
        task = asyncio.create_task(client.request("Test.Hang"))
        await server.wait_for_requests("Test.Hang")
        await server.disconnect_all()
        with pytest.raises(NymeaConnectionError):
            await asyncio.wait_for(task, timeout=2)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_supervisor_reconnects_and_calls_callbacks(server):
    """The supervisor reconnects and invokes connected callbacks again."""

    client = make_client(server)
    connected = asyncio.Event()
    calls = 0

    async def on_connected(_client, _error):
        nonlocal calls
        calls += 1
        if calls >= 2:
            connected.set()

    client.register_connected_callback(on_connected)
    try:
        await client.async_start()
        assert await client.async_wait_connected(timeout=2)
        await server.disconnect_all()
        await asyncio.wait_for(connected.wait(), timeout=3)
        assert calls >= 2
        assert client._reader_task is not None
    finally:
        await client.async_stop()


@pytest.mark.asyncio
async def test_notifications_are_buffered_during_snapshot(server):
    """Notifications received while the snapshot is loaded are replayed."""

    client = make_client(server)
    received: asyncio.Queue = asyncio.Queue()
    client.register_notification_callback(received.put_nowait)
    callback_started = asyncio.Event()
    release_callback = asyncio.Event()
    first = True

    async def on_connected(_client, _error):
        nonlocal first
        if first:
            first = False
            callback_started.set()
            await release_callback.wait()

    client.register_connected_callback(on_connected)
    try:
        await client.async_start()
        await asyncio.wait_for(callback_started.wait(), timeout=2)
        await server.send_notification(
            "Energy.PowerBalanceChanged",
            {"currentPowerConsumption": 42},
        )
        release_callback.set()
        notification = await asyncio.wait_for(received.get(), timeout=2)
        assert notification["params"]["currentPowerConsumption"] == 42
    finally:
        await client.async_stop()


@pytest.mark.asyncio
async def test_auth_failure_callback_is_not_spammed(server):
    """A revoked token triggers one reauth request, not one per reconnect."""

    server.require_auth = True
    client = make_client(server, username="user", password="wrong")
    called = 0

    def on_auth_failed():
        nonlocal called
        called += 1

    client.register_auth_failed_callback(on_auth_failed)
    try:
        await client.async_start()
        await asyncio.sleep(0.4)
        assert called == 1
    finally:
        await client.async_stop()
