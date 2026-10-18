"""No-device integration tests against the mock nymea server."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.leaflet_hems.client import NymeaClient
from custom_components.leaflet_hems.coordinator import LeafletHEMSCoordinator

from .fake_nymea import (
    BATTERY_ID,
    PV_ID,
    ROOT_ID,
    MockNymeaServer,
)


@pytest.fixture
async def tls_server(socket_enabled):
    server = MockNymeaServer(use_tls=True)
    server.device.require_auth = True
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


def make_client(server: MockNymeaServer, **kwargs) -> NymeaClient:
    defaults = {
        "token": server.valid_token,
        "use_tls": True,
        "request_timeout": 3.0,
        "keepalive_interval": 3600.0,
        "reconnect_initial_delay": 0.05,
        "reconnect_max_delay": 0.1,
    }
    defaults.update(kwargs)
    return NymeaClient("127.0.0.1", server.port, **defaults)


@pytest.mark.asyncio
async def test_tls_mock_receives_notifications(tls_server):
    """The real client receives notifications from the TLS mock."""

    client = make_client(tls_server)
    received: asyncio.Queue = asyncio.Queue()
    client.register_notification_callback(received.put_nowait)
    try:
        await client.async_connect()
        await tls_server.set_power_balance(
            currentPowerAcquisition=-500.0,
            currentPowerProduction=-1500.0,
            currentPowerStorage=200.0,
            currentPowerConsumption=800.0,
        )
        notification = await asyncio.wait_for(received.get(), timeout=2)
        assert notification["notification"] == "Energy.PowerBalanceChanged"
        assert notification["params"]["currentPowerAcquisition"] == -500.0
        assert "id" in notification
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_mock_requires_hello_id_zero_for_notifications(socket_enabled):
    """A non-zero Hello id does not enable notifications (nymea quirk)."""

    server = MockNymeaServer(use_tls=True, require_hello_id_zero=True)
    await server.start()
    client = make_client(server)
    received: asyncio.Queue = asyncio.Queue()
    client.register_notification_callback(received.put_nowait)
    try:
        await client.async_open()
        await client.request(
            "JSONRPC.Hello",
            {"locale": "de_DE"},
            request_id=1,
            include_token=True,
        )
        await client.request(
            "JSONRPC.SetNotificationStatus",
            {"namespaces": ["Energy", "Hems", "Integrations"]},
            request_id=2,
        )
        client._ready = True
        await server.set_power_balance(currentPowerAcquisition=-1.0)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(received.get(), timeout=0.4)
    finally:
        await client.async_close()
        await server.stop()


@pytest.mark.asyncio
async def test_reconnect_after_mock_restart(tls_server):
    """The supervisor reconnects and resubscribes after a server restart."""

    client = make_client(tls_server)
    connected = asyncio.Event()
    calls = 0
    received: asyncio.Queue = asyncio.Queue()

    async def on_connected(_client, _error):
        nonlocal calls
        calls += 1
        if calls >= 2:
            connected.set()

    client.register_connected_callback(on_connected)
    client.register_notification_callback(received.put_nowait)
    try:
        await client.async_start()
        assert await client.async_wait_connected(timeout=2)
        await tls_server.restart()
        await asyncio.wait_for(connected.wait(), timeout=5)
        assert client.is_connected
        await tls_server.set_power_balance(currentPowerConsumption=321.0)
        notification = await asyncio.wait_for(received.get(), timeout=2)
        assert notification["params"]["currentPowerConsumption"] == 321.0
    finally:
        await client.async_stop()


@pytest.mark.asyncio
async def test_coordinator_against_mock_device(hass, tls_server):
    """The coordinator builds grouped data from the mock device over TLS."""

    client = make_client(tls_server)
    coordinator = LeafletHEMSCoordinator(hass, client, "mock-uuid", "Mock HEMS")
    try:
        await coordinator.async_start(first_snapshot_timeout=5)

        assert coordinator.data["grid_import_energy"] == pytest.approx(22177.0)
        assert coordinator.data["generation_energy"] == pytest.approx(50397.6)
        assert coordinator.data["storage_energy_stored"] == pytest.approx(0.003)
        assert coordinator.data["storage_energy_provided"] == pytest.approx(0.304)
        assert coordinator.data["generation_power"] == pytest.approx(459.0)

        await tls_server.set_state(ROOT_ID, "currentPower", -500.0)
        await tls_server.set_state(PV_ID, "currentPower", -1500.0)
        await tls_server.set_state(BATTERY_ID, "currentPower", 200.0)

        for _ in range(30):
            if coordinator.data.get("consumption_power") == 800.0:
                break
            await asyncio.sleep(0.1)

        assert coordinator.data["grid_export_power"] == 500.0
        assert coordinator.data["generation_power"] == 1500.0
        assert coordinator.data["storage_charging_power"] == 200.0
        assert coordinator.data["consumption_power"] == 800.0
    finally:
        await coordinator.async_stop()


@pytest.mark.asyncio
async def test_coordinator_picks_up_pv_configuration_changes(hass, tls_server):
    """Adding/removing a PV thing updates the grouped generation values."""

    client = make_client(tls_server)
    coordinator = LeafletHEMSCoordinator(hass, client, "mock-uuid", "Mock HEMS")
    try:
        await coordinator.async_start(first_snapshot_timeout=5)
        assert coordinator.data["generation_energy"] == pytest.approx(50397.6)

        await tls_server.add_pv(
            "pv-2", current_power=-100.0, total_energy=1000.0
        )
        for _ in range(30):
            if coordinator.data.get("generation_energy") == pytest.approx(51397.6):
                break
            await asyncio.sleep(0.1)
        assert coordinator.data["generation_energy"] == pytest.approx(51397.6)
        assert coordinator.data["generation_power"] == pytest.approx(559.0)

        await tls_server.remove_pv("pv-2")
        for _ in range(30):
            if coordinator.data.get("generation_energy") == pytest.approx(50397.6):
                break
            await asyncio.sleep(0.1)
        assert coordinator.data["generation_energy"] == pytest.approx(50397.6)
    finally:
        await coordinator.async_stop()
