"""Persistent nymea JSON-RPC client used by the Leaflet HEMS integration.

The client keeps one TCP+TLS connection open for the lifetime of the config
entry.  It only sends requests for the initial snapshot (and for structural
changes such as a new battery); all normal updates arrive as JSON-RPC
notifications.  A supervisor task owns the connection lifecycle and
reconnects with exponential backoff, re-running the handshake and the
coordinator's snapshot callback after every reconnect.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import random
import socket
import ssl
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from .const import (
    DEFAULT_LOCALE,
    NOTIFICATION_NAMESPACES,
    RPC_AUTHENTICATE_METHOD,
    RPC_HELLO_METHOD,
    RPC_KEEPALIVE_METHOD,
    RPC_SET_NOTIFICATION_STATUS,
)
from .util import clean_params

_LOGGER = logging.getLogger(__name__)

NotificationCallback = Callable[[dict[str, Any]], Any]
ConnectionCallback = Callable[["NymeaClient", Exception | None], Awaitable[None]]

RECONNECT_INITIAL_DELAY = 1.0
RECONNECT_MAX_DELAY = 60.0
REQUEST_TIMEOUT = 15.0
KEEPALIVE_INTERVAL = 60.0
KEEPALIVE_TIMEOUT = 10.0
MAX_BUFFERED_NOTIFICATIONS = 512


class NymeaError(Exception):
    """Base error for the nymea client."""


class NymeaConnectionError(NymeaError):
    """The TCP/TLS connection is not available."""


class NymeaTimeoutError(NymeaError):
    """A request did not receive a response in time."""


class NymeaAuthenticationError(NymeaError):
    """Authentication with the device failed or a token was rejected."""


class NymeaResponseError(NymeaError):
    """The device returned a JSON-RPC error response."""

    def __init__(self, method: str, response: dict[str, Any]) -> None:
        self.method = method
        self.response = response
        super().__init__(
            f"{method} failed: {response.get('error') or response.get('status')}"
        )


class NymeaClient:
    """A single persistent nymea JSON-RPC connection with reconnect handling."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        device_name: str = "HomeAssistant",
        locale: str = DEFAULT_LOCALE,
        request_timeout: float = REQUEST_TIMEOUT,
        keepalive_interval: float = KEEPALIVE_INTERVAL,
        reconnect_initial_delay: float = RECONNECT_INITIAL_DELAY,
        reconnect_max_delay: float = RECONNECT_MAX_DELAY,
        use_tls: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self._token = token
        self._username = username
        self._password = password
        self._device_name = device_name
        self._locale = locale
        self._request_timeout = request_timeout
        self._keepalive_interval = keepalive_interval
        self._reconnect_initial_delay = reconnect_initial_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._use_tls = use_tls

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._supervisor_task: asyncio.Task[None] | None = None

        self._write_lock = asyncio.Lock()
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._session_id = str(uuid.uuid4())

        self._notification_callbacks: dict[str, NotificationCallback] = {}
        self._connected_callbacks: list[ConnectionCallback] = []
        self._disconnected_callbacks: list[ConnectionCallback] = []
        self._auth_failed_callbacks: list[Callable[[], Any]] = []

        self._connected = False
        self._ready = False
        self._stopping = False
        self._disconnect_event = asyncio.Event()
        self._connected_event = asyncio.Event()
        self._notification_buffer: list[dict[str, Any]] = []
        self._reconnect_attempt = 0
        self._auth_failure_notified = False
        self._failure_logged = False

        self.server_info: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Properties and callback registration
    # ------------------------------------------------------------------
    @property
    def is_connected(self) -> bool:
        """Return True while the TCP/TLS connection is usable."""

        return (
            self._connected
            and self._writer is not None
            and not self._writer.is_closing()
            and not self._disconnect_event.is_set()
        )

    @property
    def token(self) -> str | None:
        """Return the current device token, if any."""

        return self._token

    @property
    def server_uuid(self) -> str | None:
        """Return the UUID reported by the most recent Hello."""

        value = self.server_info.get("uuid")
        return str(value) if value is not None else None

    def register_notification_callback(self, callback: NotificationCallback) -> str:
        """Register a callback for JSON-RPC notifications."""

        token = str(uuid.uuid4())
        self._notification_callbacks[token] = callback
        return token

    def unregister_notification_callback(self, token: str) -> None:
        """Remove a previously registered notification callback."""

        self._notification_callbacks.pop(token, None)

    def register_connected_callback(self, callback: ConnectionCallback) -> None:
        """Register an async callback invoked after each successful connect."""

        if callback not in self._connected_callbacks:
            self._connected_callbacks.append(callback)

    def register_disconnected_callback(self, callback: ConnectionCallback) -> None:
        """Register an async callback invoked after a connection is lost."""

        if callback not in self._disconnected_callbacks:
            self._disconnected_callbacks.append(callback)

    def register_auth_failed_callback(self, callback: Callable[[], Any]) -> None:
        """Register a callback invoked once when authentication fails."""

        if callback not in self._auth_failed_callbacks:
            self._auth_failed_callbacks.append(callback)

    def unregister_connected_callback(self, callback: ConnectionCallback) -> None:
        """Remove a connected callback."""

        with contextlib.suppress(ValueError):
            self._connected_callbacks.remove(callback)

    def unregister_disconnected_callback(self, callback: ConnectionCallback) -> None:
        """Remove a disconnected callback."""

        with contextlib.suppress(ValueError):
            self._disconnected_callbacks.remove(callback)

    # ------------------------------------------------------------------
    # One-shot connection handling (used by the config flow)
    # ------------------------------------------------------------------
    async def async_open(self) -> None:
        """Open the TCP/TLS connection and start the reader task."""

        if self._writer is not None and not self._writer.is_closing():
            return

        ssl_context = self._create_ssl_context() if self._use_tls else None
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port, ssl=ssl_context),
                timeout=self._request_timeout,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise NymeaConnectionError(
                f"Unable to connect to {self.host}:{self.port}: {exc}"
            ) from exc

        self._configure_socket()
        self._disconnect_event.clear()
        self._connected = True
        self._reader_task = asyncio.create_task(
            self._reader_loop(), name=f"leaflet-hems-reader-{self.host}"
        )
        _LOGGER.debug("Connected to nymea at %s:%s", self.host, self.port)

    async def async_hello(self) -> dict[str, Any]:
        """Perform JSONRPC.Hello and return its params."""

        # nymea requires the JSONRPC.Hello request id to be 0.  Using a
        # different id still returns a successful Hello, but the connection
        # is not fully initialized for server notifications.
        response = await self.request(
            RPC_HELLO_METHOD,
            {"locale": self._locale},
            include_token=self._token is not None,
            request_id=0,
        )
        params = clean_params(response.get("params") or {})
        self.server_info = params
        return params

    async def async_authenticate(
        self, username: str, password: str, device_name: str | None = None
    ) -> str:
        """Authenticate and store the returned device token."""

        try:
            response = await self.request(
                RPC_AUTHENTICATE_METHOD,
                {
                    "username": username,
                    "password": password,
                    "deviceName": device_name or self._device_name,
                },
                include_token=False,
            )
        except NymeaResponseError as exc:
            raise NymeaAuthenticationError(
                "The device rejected the username or password"
            ) from exc
        params = clean_params(response.get("params") or {})
        if params.get("success") is False:
            raise NymeaAuthenticationError("The device rejected the username or password")
        token = params.get("token")
        if not token:
            raise NymeaAuthenticationError(
                "Authentication succeeded without returning a token"
            )
        self._token = str(token)
        return self._token

    async def async_connect(self) -> dict[str, Any]:
        """Open the connection, perform the handshake and subscribe to events."""

        await self.async_open()

        hello = await self.async_hello()
        if hello.get("authenticationRequired") and not hello.get("authenticated"):
            if not self._username or not self._password:
                raise NymeaAuthenticationError(
                    "The device requires authentication but no credentials are stored"
                )
            await self.async_authenticate(
                self._username, self._password, self._device_name
            )
            hello = await self.async_hello()
            if not hello.get("authenticated"):
                raise NymeaAuthenticationError("The device rejected the token")

        await self.request(
            RPC_SET_NOTIFICATION_STATUS,
            {"namespaces": NOTIFICATION_NAMESPACES},
        )

        if self._keepalive_task is None or self._keepalive_task.done():
            self._keepalive_task = asyncio.create_task(
                self._keepalive_loop(), name=f"leaflet-hems-keepalive-{self.host}"
            )

        self._connected = True
        self._auth_failure_notified = False
        # When the supervisor is not running (config flow, one-shot probes)
        # notifications can be dispatched immediately.  During supervised
        # connections the supervisor keeps _ready=False until the snapshot
        # callback has completed.
        if self._supervisor_task is None or self._supervisor_task.done():
            self._ready = True
        return hello

    # ------------------------------------------------------------------
    # Supervisor / reconnect handling
    # ------------------------------------------------------------------
    async def async_start(self) -> None:
        """Start the persistent connection supervisor."""

        if self._supervisor_task is not None and not self._supervisor_task.done():
            return
        self._stopping = False
        self._supervisor_task = asyncio.create_task(
            self._supervisor_loop(), name=f"leaflet-hems-supervisor-{self.host}"
        )

    async def async_stop(self) -> None:
        """Stop the supervisor and close the current connection."""

        self._stopping = True
        task = self._supervisor_task
        self._supervisor_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._async_teardown()

    async def async_close(self) -> None:
        """Close the current connection without touching the supervisor."""

        await self._async_teardown()

    async def async_wait_connected(self, timeout: float | None = None) -> bool:
        """Wait until the supervisor has completed a connection snapshot."""

        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return True

    async def _supervisor_loop(self) -> None:
        """Own the connection lifecycle and reconnect forever with backoff."""

        was_connected = False
        while not self._stopping:
            connected_this_round = False
            try:
                hello = await self.async_connect()
                connected_this_round = True
                was_connected = True

                # Do not dispatch live notifications while the coordinator is
                # rebuilding its snapshot.  They are buffered and replayed
                # after the snapshot so no state transition is lost.
                self._ready = False
                await self._run_connection_callbacks(self._connected_callbacks)
                self._ready = True
                await self._replay_notification_buffer()

                self._reconnect_attempt = 0
                self._failure_logged = False
                self._connected_event.set()
                _LOGGER.info(
                    "Connected to %s (%s:%s), protocol %s",
                    hello.get("name") or self.server_info.get("name") or "nymea",
                    self.host,
                    self.port,
                    hello.get("protocol version") or "?",
                )
                await self._disconnect_event.wait()
            except asyncio.CancelledError:
                raise
            except NymeaAuthenticationError as exc:
                if not self._failure_logged:
                    _LOGGER.error("Authentication with %s failed: %s", self.host, exc)
                    self._failure_logged = True
                else:
                    _LOGGER.debug("Authentication with %s still failing: %s", self.host, exc)
                await self._notify_auth_failed()
                if was_connected:
                    await self._run_connection_callbacks(
                        self._disconnected_callbacks, exc
                    )
            except Exception as exc:  # noqa: BLE001 - supervisor must never die
                if not self._failure_logged:
                    _LOGGER.warning(
                        "Nymea connection to %s failed: %s", self.host, exc
                    )
                    self._failure_logged = True
                else:
                    _LOGGER.debug(
                        "Nymea connection to %s still failing: %s", self.host, exc
                    )
                if was_connected:
                    await self._run_connection_callbacks(
                        self._disconnected_callbacks, exc
                    )
            finally:
                await self._async_teardown()
                if connected_this_round:
                    _LOGGER.debug("Nymea connection closed; reconnecting")
                if not self._stopping:
                    delay = self._next_reconnect_delay()
                    _LOGGER.info(
                        "Reconnecting to %s:%s in %.1f seconds", self.host, self.port, delay
                    )
                    await asyncio.sleep(delay)

    def _next_reconnect_delay(self) -> float:
        """Return the next exponential backoff delay with jitter."""

        base = min(
            self._reconnect_max_delay,
            self._reconnect_initial_delay * (2**self._reconnect_attempt),
        )
        self._reconnect_attempt += 1
        return max(0.1, base * random.uniform(0.8, 1.2))

    async def _run_connection_callbacks(
        self, callbacks: list[ConnectionCallback], error: Exception | None = None
    ) -> None:
        for callback in list(callbacks):
            try:
                await callback(self, error)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Error in nymea connection callback")

    async def _notify_auth_failed(self) -> None:
        if self._auth_failure_notified:
            return
        self._auth_failure_notified = True
        for callback in list(self._auth_failed_callbacks):
            try:
                result = callback()
                if inspect.isawaitable(result):
                    await result
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Error in authentication failure callback")

    async def _async_teardown(self) -> None:
        """Cancel background tasks and close the streams."""

        self._connected = False
        self._ready = False
        self._connected_event.clear()
        self._notification_buffer.clear()

        keepalive = self._keepalive_task
        self._keepalive_task = None
        if keepalive is not None and keepalive is not asyncio.current_task():
            keepalive.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keepalive

        reader_task = self._reader_task
        self._reader_task = None
        if reader_task is not None and reader_task is not asyncio.current_task():
            reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader_task

        self._fail_pending(NymeaConnectionError("Connection closed"))

        writer = self._writer
        self._writer = None
        self._reader = None
        if writer is not None:
            writer.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(writer.wait_closed(), timeout=2.0)

    def _mark_disconnected(self, reason: str) -> None:
        """Signal the supervisor that the current connection is unusable."""

        if self._disconnect_event.is_set():
            return
        _LOGGER.debug("Nymea connection lost: %s", reason)
        self._connected = False
        self._ready = False
        self._disconnect_event.set()
        writer = self._writer
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()

    # ------------------------------------------------------------------
    # JSON-RPC request/response handling
    # ------------------------------------------------------------------
    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        include_token: bool = True,
        request_id: int | None = None,
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and wait for the matching response."""

        writer = self._writer
        if writer is None or writer.is_closing() or self._disconnect_event.is_set():
            raise NymeaConnectionError("Not connected to the nymea device")

        if request_id is None:
            request_id = self._next_id
            self._next_id += 1
        payload: dict[str, Any] = {"id": request_id, "method": method}
        if params:
            payload["params"] = params
        if include_token and self._token:
            payload["token"] = self._token

        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request_id] = future

        try:
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            async with self._write_lock:
                writer.write(data.encode("utf-8") + b"\n")
                await writer.drain()
        except (OSError, RuntimeError) as exc:
            self._pending.pop(request_id, None)
            self._mark_disconnected(f"write failed: {exc}")
            raise NymeaConnectionError(f"Unable to send {method}: {exc}") from exc

        try:
            response = await asyncio.wait_for(
                future, timeout=timeout if timeout is not None else self._request_timeout
            )
        except asyncio.TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise NymeaTimeoutError(f"Timeout waiting for {method}") from exc
        finally:
            self._pending.pop(request_id, None)

        if response.get("status") != "success":
            raise NymeaResponseError(method, response)
        return response

    async def _reader_loop(self) -> None:
        """Read responses and notifications until the connection closes."""

        reader = self._reader
        if reader is None:
            return
        try:
            while True:
                line = await reader.readline()
                if not line:
                    self._mark_disconnected("EOF")
                    return
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    _LOGGER.warning("Ignoring invalid JSON from nymea: %s", text[:200])
                    continue

                # nymea notifications carry their own numeric "id" field.
                # They must be recognized before the response-id lookup,
                # otherwise all live updates are silently dropped.
                if "notification" in message or (
                    "method" in message and "status" not in message
                ):
                    await self._dispatch_notification(message)
                    continue

                if "id" in message:
                    future = self._pending.pop(message["id"], None)
                    if future is not None and not future.done():
                        future.set_result(message)
                    continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Nymea reader stopped: %s", exc)
            self._mark_disconnected(str(exc))
        finally:
            self._fail_pending(NymeaConnectionError("Connection lost"))

    async def _dispatch_notification(self, notification: dict[str, Any]) -> None:
        """Deliver a notification, buffering it during snapshot rebuilds."""

        if not self._ready:
            if len(self._notification_buffer) >= MAX_BUFFERED_NOTIFICATIONS:
                self._notification_buffer.pop(0)
            self._notification_buffer.append(notification)
            return

        for token, callback in list(self._notification_callbacks.items()):
            try:
                result = callback(notification)
                if inspect.isawaitable(result):
                    await result
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Error in notification callback %s", token)

    async def _replay_notification_buffer(self) -> None:
        """Dispatch notifications received while the snapshot was rebuilt."""

        buffered = self._notification_buffer
        self._notification_buffer = []
        for notification in buffered:
            await self._dispatch_notification(notification)

    def _fail_pending(self, error: Exception) -> None:
        """Fail all outstanding request futures."""

        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    # ------------------------------------------------------------------
    # Keepalive and socket setup
    # ------------------------------------------------------------------
    async def _keepalive_loop(self) -> None:
        """Send a small ping so half-open connections are detected."""

        try:
            while True:
                await asyncio.sleep(self._keepalive_interval)
                if not self.is_connected:
                    return
                try:
                    await self.request(
                        RPC_KEEPALIVE_METHOD,
                        {"sessionId": self._session_id},
                        timeout=KEEPALIVE_TIMEOUT,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning("Nymea keepalive failed: %s", exc)
                    self._mark_disconnected(f"keepalive failed: {exc}")
                    return
        except asyncio.CancelledError:
            pass

    @staticmethod
    def _create_ssl_context() -> ssl.SSLContext:
        """Create the permissive TLS context used by nymea devices.

        Leaflet devices use a self-signed certificate.  The connection is
        still encrypted; certificate pinning can be added later without
        changing the rest of the client.
        """

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    def _configure_socket(self) -> None:
        """Enable TCP keepalive on the underlying socket."""

        if self._writer is None:
            return
        sock = self._writer.get_extra_info("socket")
        if sock is None:
            return
        with contextlib.suppress(OSError):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        with contextlib.suppress(OSError, AttributeError):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 20)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
