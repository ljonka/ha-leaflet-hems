"""In-process mock of a nymea device and JSON-RPC server.

The mock models the parts of the nymea API used by the integration:

* TLS and token authentication (optional)
* JSONRPC.Hello (including the id-0 notification quirk)
* JSONRPC.Authenticate / KeepAlive / SetNotificationStatus
* Energy.GetRootMeter / GetPowerBalance
* Hems.GetPvConfigurations / GetBatteryConfigurations
* Integrations.GetThings / GetThingClasses / GetStateTypes / GetStateValues
* live Integrations.StateChanged and Energy.PowerBalanceChanged notifications
* disconnect/restart behaviour for reconnect tests

It is used by the unit tests and can be run as a standalone mock server:

    python -m tests.mock_nymea_server --host 0.0.0.0 --port 12222 --tls
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import os
import shutil
import ssl
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from custom_components.leaflet_hems.client import NymeaResponseError

_LOGGER = logging.getLogger(__name__)

DEFAULT_UUID = "{12345678-1234-1234-1234-123456789abc}"
DEFAULT_NAME = "Test Leaflet"
DEFAULT_VERSION = "1.7.26"
DEFAULT_PROTOCOL = "7.1"

ROOT_ID = "root-1"
PV_ID = "pv-1"
BATTERY_ID = "battery-1"

ROOT_CLASS = "class-root"
PV_CLASS = "class-pv"
BATTERY_CLASS = "class-battery"

ROOT_STATES = {
    "st-root-connected": "connected",
    "st-root-power": "currentPower",
    "st-root-consumed": "totalEnergyConsumed",
    "st-root-produced": "totalEnergyProduced",
}
PV_STATES = {
    "st-pv-connected": "connected",
    "st-pv-power": "currentPower",
    "st-pv-produced": "totalEnergyProduced",
}
BATTERY_STATES = {
    "st-bat-connected": "connected",
    "st-bat-power": "currentPower",
    "st-bat-consumed": "totalEnergyConsumed",
    "st-bat-produced": "totalEnergyProduced",
}

ALL_NAMESPACES = [
    "AppData",
    "Configuration",
    "Energy",
    "Hems",
    "Integrations",
    "JSONRPC",
    "Logging",
    "ModbusRtu",
    "NetworkManager",
    "Rules",
    "Scripts",
    "System",
    "Tags",
    "Users",
    "ZWave",
    "Zigbee",
]


@dataclass
class MockThing:
    """A thing with state type ids and current values."""

    id: str
    name: str
    class_id: str
    state_types: dict[str, str]
    values: dict[str, Any]

    def state_type_id(self, name: str) -> str | None:
        for state_type_id, state_name in self.state_types.items():
            if state_name == name:
                return state_type_id
        return None


@dataclass
class MockNymeaDevice:
    """The data model of a small Leaflet HEMS device."""

    uuid: str = DEFAULT_UUID
    name: str = DEFAULT_NAME
    version: str = DEFAULT_VERSION
    protocol: str = DEFAULT_PROTOCOL
    require_auth: bool = False
    valid_token: str = "valid-token"
    valid_password: str = "secret"
    auth_success_false: bool = False

    root_meter_id: str | None = ROOT_ID
    pv_ids: list[str] = field(default_factory=lambda: [PV_ID])
    battery_ids: list[str] = field(default_factory=lambda: [BATTERY_ID])

    things: dict[str, MockThing] = field(default_factory=dict)
    thing_classes: dict[str, dict[str, Any]] = field(default_factory=dict)
    power_balance: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.things = {
            ROOT_ID: MockThing(
                ROOT_ID,
                "Test Grid Meter",
                ROOT_CLASS,
                dict(ROOT_STATES),
                {
                    "st-root-connected": True,
                    "st-root-power": 103.99,
                    "st-root-consumed": 22177.0,
                    "st-root-produced": 27994.0,
                },
            ),
            PV_ID: MockThing(
                PV_ID,
                "Test PV Inverter",
                PV_CLASS,
                dict(PV_STATES),
                {
                    "st-pv-connected": True,
                    "st-pv-power": -459.0,
                    "st-pv-produced": 50397.6,
                },
            ),
            BATTERY_ID: MockThing(
                BATTERY_ID,
                "Test Battery",
                BATTERY_CLASS,
                dict(BATTERY_STATES),
                {
                    "st-bat-connected": True,
                    "st-bat-power": 0.0,
                    "st-bat-consumed": 0.003,
                    "st-bat-produced": 0.304,
                },
            ),
        }
        self.thing_classes = {
            ROOT_CLASS: {"id": ROOT_CLASS, "name": "Mock Grid Meter"},
            PV_CLASS: {"id": PV_CLASS, "name": "Mock PV Inverter"},
            BATTERY_CLASS: {"id": BATTERY_CLASS, "name": "Mock Battery"},
        }
        self.power_balance = {
            "currentPowerAcquisition": 103.99,
            "currentPowerProduction": -459.0,
            "currentPowerStorage": 0.0,
            "currentPowerConsumption": 562.99,
            "totalAcquisition": 19729726.74,
            "totalReturn": 8984406.36,
            "totalProduction": 12079.5,
            "totalConsumption": 10757399.9,
        }

    def hello_params(self, authenticated: bool) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "version": self.version,
            "protocol version": self.protocol,
            "initialSetupRequired": False,
            "pushButtonAuthAvailable": False,
            "authenticationRequired": self.require_auth,
            "authenticated": authenticated,
            "locale": "de_DE",
            "language": "de_DE",
        }

    def thing_json(self, thing: MockThing) -> dict[str, Any]:
        return {
            "id": thing.id,
            "name": thing.name,
            "thingClassId": thing.class_id,
        }

    def things_json(self, thing_id: str | None = None) -> list[dict[str, Any]]:
        if thing_id is not None:
            thing = self.things.get(thing_id)
            return [self.thing_json(thing)] if thing else []
        return [self.thing_json(thing) for thing in self.things.values()]

    def thing_classes_json(
        self, class_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        if class_ids is None:
            return list(self.thing_classes.values())
        return [
            self.thing_classes[class_id]
            for class_id in class_ids
            if class_id in self.thing_classes
        ]

    def state_types_json(self, class_id: str) -> list[dict[str, Any]]:
        for thing in self.things.values():
            if thing.class_id == class_id:
                return [
                    {"id": state_type_id, "name": state_name}
                    for state_type_id, state_name in thing.state_types.items()
                ]
        return []

    def state_values_json(self, thing_id: str) -> list[dict[str, Any]]:
        thing = self.things.get(thing_id)
        if thing is None:
            return []
        return [
            {"stateTypeId": state_type_id, "value": value}
            for state_type_id, value in thing.values.items()
        ]

    def pv_configurations(self) -> list[dict[str, Any]]:
        return [{"pvThingId": thing_id} for thing_id in self.pv_ids]

    def battery_configurations(self) -> list[dict[str, Any]]:
        return [{"batteryThingId": thing_id} for thing_id in self.battery_ids]

    def state_type_id(self, thing_id: str, state_name: str) -> str | None:
        thing = self.things.get(thing_id)
        return thing.state_type_id(state_name) if thing else None

    def set_state(self, thing_id: str, state_name: str, value: Any) -> dict[str, Any]:
        thing = self.things[thing_id]
        state_type_id = thing.state_type_id(state_name)
        if state_type_id is None:
            raise KeyError(f"{thing_id} has no state {state_name}")
        thing.values[state_type_id] = value
        return {
            "thingId": thing_id,
            "stateTypeId": state_type_id,
            "value": value,
            "maxValue": None,
            "minValue": None,
        }


@dataclass
class _Connection:
    writer: asyncio.StreamWriter
    hello_id: int | None = None
    authenticated: bool = False
    notifications_ready: bool = False
    namespaces: set[str] | None = None


class MockNymeaServer:
    """A line-based JSON-RPC mock server with optional TLS and reconnect support."""

    def __init__(
        self,
        device: MockNymeaDevice | None = None,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        use_tls: bool = False,
        require_hello_id_zero: bool = True,
    ) -> None:
        self.device = device or MockNymeaDevice()
        self.host = host
        self._requested_port = port
        self.port = port
        self.use_tls = use_tls
        self.require_hello_id_zero = require_hello_id_zero

        self.server: asyncio.AbstractServer | None = None
        self.requests: list[dict[str, Any]] = []
        self.connection_count = 0
        self.writers: list[asyncio.StreamWriter] = []
        self.responses: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        self.hang_methods: set[str] = set()
        self.notification_id = 100000
        self.request_event = asyncio.Event()

        self._connections: dict[asyncio.StreamWriter, _Connection] = {}
        self._ssl_context: ssl.SSLContext | None = None
        self._cert_dir: str | None = None

    # ------------------------------------------------------------------
    # Backwards-compatible attributes used by older tests
    # ------------------------------------------------------------------
    @property
    def connections(self) -> int:
        return self.connection_count

    @property
    def require_auth(self) -> bool:
        return self.device.require_auth

    @require_auth.setter
    def require_auth(self, value: bool) -> None:
        self.device.require_auth = value

    @property
    def valid_token(self) -> str:
        return self.device.valid_token

    @valid_token.setter
    def valid_token(self, value: str) -> None:
        self.device.valid_token = value

    @property
    def valid_password(self) -> str:
        return self.device.valid_password

    @valid_password.setter
    def valid_password(self, value: str) -> None:
        self.device.valid_password = value

    @property
    def auth_success_false(self) -> bool:
        return self.device.auth_success_false

    @auth_success_false.setter
    def auth_success_false(self, value: bool) -> None:
        self.device.auth_success_false = value

    @property
    def hello_params(self) -> dict[str, Any]:
        return self.device.hello_params(authenticated=True)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self.server is not None:
            return
        if self.use_tls and self._ssl_context is None:
            self._ssl_context = self._create_ssl_context()

        self.server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self.port or self._requested_port,
            ssl=self._ssl_context,
        )
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for writer in list(self.writers):
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        self.writers.clear()
        self._connections.clear()
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        if self._cert_dir:
            shutil.rmtree(self._cert_dir, ignore_errors=True)
            self._cert_dir = None
            self._ssl_context = None

    async def restart(self) -> None:
        """Stop and start on the same port to exercise reconnect handling."""

        port = self.port
        await self.stop()
        self.port = port
        await self.start()

    async def disconnect_all(self) -> None:
        for writer in list(self.writers):
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        self.writers.clear()
        self._connections.clear()

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------
    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.connection_count += 1
        connection = _Connection(writer)
        self._connections[writer] = connection
        self.writers.append(writer)
        try:
            while line := await reader.readline():
                message = json.loads(line)
                self.requests.append(message)
                self.request_event.set()
                response = self.handle_message(message, connection)
                if response is not None:
                    writer.write(json.dumps(response).encode() + b"\n")
                    await writer.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass
        finally:
            self._connections.pop(writer, None)
            if writer in self.writers:
                self.writers.remove(writer)
            writer.close()

    def handle_message(
        self, message: dict[str, Any], connection: _Connection | None = None
    ) -> dict[str, Any] | None:
        method = message.get("method")
        params = message.get("params") or {}
        request_id = message.get("id")
        if connection is None:
            connection = _Connection(asyncio.StreamWriter.__new__(asyncio.StreamWriter))

        if method == "JSONRPC.Hello":
            connection.hello_id = request_id
            authenticated = not self.device.require_auth or (
                message.get("token") == self.device.valid_token
            )
            connection.authenticated = authenticated
            connection.notifications_ready = (
                not self.require_hello_id_zero or request_id == 0
            )
            return {
                "id": request_id,
                "status": "success",
                "params": self.device.hello_params(authenticated),
            }

        if method == "JSONRPC.Authenticate":
            if self.device.auth_success_false:
                return {
                    "id": request_id,
                    "status": "success",
                    "params": {"success": False},
                }
            if params.get("password") != self.device.valid_password:
                return {
                    "id": request_id,
                    "status": "error",
                    "error": "Invalid credentials",
                }
            connection.authenticated = True
            return {
                "id": request_id,
                "status": "success",
                "params": {
                    "success": True,
                    "token": self.device.valid_token,
                    "username": params.get("username"),
                },
            }

        if method == "JSONRPC.SetNotificationStatus":
            namespaces = params.get("namespaces")
            if isinstance(namespaces, list):
                connection.namespaces = {str(namespace) for namespace in namespaces}
            elif params.get("enabled"):
                connection.namespaces = None
            else:
                connection.namespaces = set()
            enabled_namespaces = (
                sorted(connection.namespaces)
                if connection.namespaces is not None
                else list(ALL_NAMESPACES)
            )
            return {
                "id": request_id,
                "status": "success",
                "params": {
                    "enabled": True,
                    "namespaces": enabled_namespaces,
                },
            }

        if method == "JSONRPC.KeepAlive":
            return {"id": request_id, "status": "success", "params": params}

        if method in self.hang_methods:
            return None

        device_response = self._device_response(method, params)
        if device_response is not None:
            return {
                "id": request_id,
                "status": "success",
                "params": device_response,
            }

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

    def _device_response(
        self, method: str, params: dict[str, Any]
    ) -> dict[str, Any] | None:
        device = self.device
        if method == "Energy.GetRootMeter":
            return (
                {"rootMeterThingId": device.root_meter_id}
                if device.root_meter_id
                else {}
            )
        if method == "Energy.GetPowerBalance":
            return dict(device.power_balance)
        if method == "Hems.GetPvConfigurations":
            return {"pvConfigurations": device.pv_configurations()}
        if method == "Hems.GetBatteryConfigurations":
            return {"batteryConfigurations": device.battery_configurations()}
        if method == "Integrations.GetThings":
            return {"things": device.things_json(params.get("thingId"))}
        if method == "Integrations.GetThingClasses":
            return {
                "thingClasses": device.thing_classes_json(
                    params.get("thingClassIds")
                )
            }
        if method == "Integrations.GetStateTypes":
            return {
                "stateTypes": device.state_types_json(params.get("thingClassId"))
            }
        if method == "Integrations.GetStateValues":
            return {"values": device.state_values_json(params.get("thingId"))}
        return None

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------
    async def send_notification(
        self,
        notification: str,
        params: dict[str, Any],
        *,
        force: bool = False,
    ) -> None:
        self.notification_id += 1
        payload = json.dumps(
            {
                "id": self.notification_id,
                "notification": notification,
                "params": params,
            }
        ).encode() + b"\n"
        namespace = notification.split(".", 1)[0]
        for writer, connection in list(self._connections.items()):
            if writer.is_closing():
                continue
            if not force:
                if not connection.notifications_ready:
                    continue
                if connection.namespaces is not None and namespace not in connection.namespaces:
                    continue
            try:
                writer.write(payload)
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                self._connections.pop(writer, None)
                if writer in self.writers:
                    self.writers.remove(writer)

    async def set_state(
        self, thing_id: str, state_name: str, value: Any
    ) -> None:
        params = self.device.set_state(thing_id, state_name, value)
        await self.send_notification("Integrations.StateChanged", params)
        if state_name == "currentPower":
            self._update_power_balance_from_things()
            await self.send_notification(
                "Energy.PowerBalanceChanged", dict(self.device.power_balance)
            )

    async def set_power_balance(self, **values: float) -> None:
        self.device.power_balance.update(values)
        await self.send_notification(
            "Energy.PowerBalanceChanged", dict(self.device.power_balance)
        )

    def _update_power_balance_from_things(self) -> None:
        device = self.device
        acquisition = 0.0
        production = 0.0
        storage = 0.0
        if device.root_meter_id and device.root_meter_id in device.things:
            power = device.things[device.root_meter_id].values.get("st-root-power")
            if isinstance(power, (int, float)):
                acquisition = float(power)
        for pv_id in device.pv_ids:
            power = device.things[pv_id].values.get("st-pv-power")
            if isinstance(power, (int, float)):
                production += float(power)
        for battery_id in device.battery_ids:
            power = device.things[battery_id].values.get("st-bat-power")
            if isinstance(power, (int, float)):
                storage += float(power)
        device.power_balance.update(
            {
                "currentPowerAcquisition": acquisition,
                "currentPowerProduction": production,
                "currentPowerStorage": storage,
                "currentPowerConsumption": acquisition
                + abs(min(0.0, production))
                - storage,
            }
        )

    async def add_pv(
        self,
        thing_id: str,
        *,
        name: str = "Test PV 2",
        current_power: float = -100.0,
        total_energy: float = 1000.0,
    ) -> None:
        self.device.things[thing_id] = MockThing(
            thing_id,
            name,
            PV_CLASS,
            dict(PV_STATES),
            {
                "st-pv-connected": True,
                "st-pv-power": current_power,
                "st-pv-produced": total_energy,
            },
        )
        self.device.pv_ids.append(thing_id)
        await self.send_notification("Hems.PvConfigurationAdded", {})

    async def remove_pv(self, thing_id: str) -> None:
        self.device.pv_ids = [
            existing for existing in self.device.pv_ids if existing != thing_id
        ]
        self.device.things.pop(thing_id, None)
        await self.send_notification("Hems.PvConfigurationRemoved", {})

    async def add_battery(
        self,
        thing_id: str,
        *,
        name: str = "Test Battery 2",
        current_power: float = 0.0,
        consumed: float = 0.0,
        produced: float = 0.0,
    ) -> None:
        self.device.things[thing_id] = MockThing(
            thing_id,
            name,
            BATTERY_CLASS,
            dict(BATTERY_STATES),
            {
                "st-bat-connected": True,
                "st-bat-power": current_power,
                "st-bat-consumed": consumed,
                "st-bat-produced": produced,
            },
        )
        self.device.battery_ids.append(thing_id)
        await self.send_notification("Hems.BatteryConfigurationAdded", {})

    async def remove_battery(self, thing_id: str) -> None:
        self.device.battery_ids = [
            existing for existing in self.device.battery_ids if existing != thing_id
        ]
        self.device.things.pop(thing_id, None)
        await self.send_notification("Hems.BatteryConfigurationRemoved", {})

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

    # ------------------------------------------------------------------
    # TLS
    # ------------------------------------------------------------------
    def _create_ssl_context(self) -> ssl.SSLContext:
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.x509.oid import NameOID
        except ImportError as exc:  # pragma: no cover - cryptography is an HA dep
            raise RuntimeError("cryptography is required for the TLS mock") from exc

        self._cert_dir = tempfile.mkdtemp(prefix="mock-nymea-")
        cert_path = os.path.join(self._cert_dir, "cert.pem")
        key_path = os.path.join(self._cert_dir, "key.pem")

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "mock-nymea.local")]
        )
        now = datetime.datetime.now(datetime.timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=365))
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("localhost"),
                        x509.DNSName("mock-nymea.local"),
                        x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1")),
                    ]
                ),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        with open(cert_path, "wb") as cert_file:
            cert_file.write(certificate.public_bytes(serialization.Encoding.PEM))
        with open(key_path, "wb") as key_file:
            key_file.write(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.TraditionalOpenSSL,
                    serialization.NoEncryption(),
                )
            )

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        return context


class FakeNymeaServer(MockNymeaServer):
    """Backwards-compatible name used by the unit tests (plain TCP by default)."""

    def __init__(self, device: MockNymeaDevice | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("use_tls", False)
        super().__init__(device, **kwargs)


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


async def _simulate(server: MockNymeaServer) -> None:
    """Emit changing power values so a connected HA instance shows live data."""

    step = 0
    while True:
        await asyncio.sleep(5)
        pv_power = -300.0 if step % 2 == 0 else -900.0
        battery_power = 200.0 if step % 3 == 0 else -100.0
        await server.set_state(PV_ID, "currentPower", pv_power)
        await server.set_state(BATTERY_ID, "currentPower", battery_power)
        step += 1


async def _run_standalone(args: argparse.Namespace) -> None:
    server = MockNymeaServer(
        host=args.host,
        port=args.port,
        use_tls=args.tls,
    )
    server.device.require_auth = not args.no_auth
    server.device.valid_token = args.token
    server.device.valid_password = args.password
    await server.start()
    print(
        json.dumps(
            {
                "host": args.host,
                "port": server.port,
                "tls": args.tls,
                "uuid": server.device.uuid,
                "name": server.device.name,
                "token": server.device.valid_token,
                "username": "test",
                "password": server.device.valid_password,
            },
            indent=2,
        ),
        flush=True,
    )
    simulation = asyncio.create_task(_simulate(server)) if args.simulate else None
    try:
        await asyncio.Event().wait()
    finally:
        if simulation is not None:
            simulation.cancel()
        await server.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=12222)
    parser.add_argument("--tls", action="store_true")
    parser.add_argument("--no-auth", action="store_true")
    parser.add_argument("--token", default="mock-token")
    parser.add_argument("--password", default="mock-password")
    parser.add_argument("--simulate", action="store_true")
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run_standalone(parser.parse_args()))
