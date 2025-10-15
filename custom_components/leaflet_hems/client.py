"""
Small persistent Nymea TCP+TLS JSON-RPC client used by the integration.

Provides:
- NymeaClient: manages a single TCP+TLS connection (reader/writer).
- send_request / recv_response helpers for line-based JSON-RPC.
- hello() helper to perform JSONRPC.Hello handshake.
- authenticate() helper to authenticate and return token.
- Notification dispatch system for real-time updates.
- Optional keepalive support.

This is intentionally lightweight to match the dart client's behaviour needed for the config flow.
"""
import asyncio
import json
import logging
from typing import Any, Dict, Optional, Callable, List
import uuid

_LOGGER = logging.getLogger(__name__)


class NymeaClient:
    def __init__(self) -> None:
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._lock = asyncio.Lock()
        self._notification_callbacks: List[tuple[str, Callable[[Dict[str, Any]], None]]] = []
        self._reader_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._pending_responses: Dict[int, asyncio.Future] = {}
        self._response_queue: asyncio.Queue = asyncio.Queue()
        self._next_id = 1000  # Start with high ID to avoid conflicts

    async def connect(self, host: str, port: int, timeout: float = 10.0) -> None:
        """Open a TCP+TLS connection to the device.

        Uses an SSLContext created in an executor to avoid blocking the event loop.
        """
        if self._writer is not None:
            return

        import ssl

        def _create_context():
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx

        loop = asyncio.get_event_loop()
        ssl_context = await loop.run_in_executor(None, _create_context)

        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=ssl_context), timeout=timeout
        )

    async def close(self) -> None:
        """Close the connection and stop all background tasks.

        Schedule wait_closed() as a background task and attach a done callback
        to consume and log any exception so it doesn't become an unhandled task exception.
        """
        # Stop background tasks first
        await self.stop_keepalive()
        await self.stop_reader_loop()
        
        if self._writer:
            try:
                self._writer.close()
                try:
                    task = asyncio.create_task(self._writer.wait_closed())
                    # Consume exceptions to avoid "Task exception was never retrieved"
                    def _on_wait_closed_done(t: asyncio.Task) -> None:
                        try:
                            _ = t.result()
                        except Exception as exc:  # pragma: no cover - defensive
                            _LOGGER.warning("NymeaClient wait_closed raised: %s", exc)
                    try:
                        task.add_done_callback(_on_wait_closed_done)
                    except Exception:
                        # Older Python versions may not support add_done_callback on created tasks
                        pass
                except Exception:
                    pass
            except Exception:
                pass
        self._reader = None
        self._writer = None

    async def send_request(self, payload: Dict[str, Any]) -> None:
        """Send a JSON-RPC payload (adds newline).

        If a client token is set via update_token(), attach it to the payload
        under the 'token' key unless the payload already contains a token.
        """
        if not self._writer:
            raise RuntimeError("Not connected")
        # Attach token if available and not already present
        if getattr(self, "_token", None) and "token" not in payload:
            payload = dict(payload)  # shallow copy
            payload["token"] = self._token
        msg = json.dumps(payload, ensure_ascii=False) + "\n"
        self._writer.write(msg.encode("utf-8"))
        await self._writer.drain()

    def update_token(self, token: Optional[str]) -> None:
        """Update the stored authentication token used for outgoing requests."""
        self._token = token

    @property
    def current_token(self) -> Optional[str]:
        """Return current token (or None)."""
        return getattr(self, "_token", None)

    async def recv_response(self, timeout: float = 10.0) -> Dict[str, Any]:
        """Receive a single line, parse JSON and return dict."""
        if not self._reader:
            raise RuntimeError("Not connected")
        line = await asyncio.wait_for(self._reader.readline(), timeout=timeout)
        text = line.decode("utf-8").strip()
        return json.loads(text)

    async def hello(self, locale: str = "de_DE", timeout: float = 10.0) -> Optional[Dict[str, Any]]:
        """Perform JSONRPC.Hello handshake and return parsed params on success."""
        async with self._lock:
            payload = {"id": 0, "method": "JSONRPC.Hello", "params": {"locale": locale}}
            await self.send_request(payload)
            try:
                resp = await self.recv_response(timeout=timeout)
            except asyncio.TimeoutError:
                _LOGGER.warning("Timeout waiting for hello response")
                return None
            status = resp.get("status")
            if status == "success":
                return resp.get("params", {})
            _LOGGER.warning("Hello returned non-success: %s", resp)
            return None

    async def authenticate(
        self, username: str, password: str, device_name: str, timeout: float = 10.0
    ) -> Optional[str]:
        """Authenticate using JSONRPC.Authenticate and return token on success.

        Returns the token string on success, None otherwise.
        """
        async with self._lock:
            payload = {
                "id": 1,
                "method": "JSONRPC.Authenticate",
                "params": {"username": username, "password": password, "deviceName": device_name},
            }
            await self.send_request(payload)
            try:
                resp = await self.recv_response(timeout=timeout)
            except asyncio.TimeoutError:
                _LOGGER.warning("Timeout waiting for authenticate response")
                return None

            if resp.get("status") == "success":
                params = resp.get("params") or {}
                token = params.get("token")
                if token:
                    return token
                _LOGGER.warning("Authenticate returned success but no token: %s", resp)
                return None

            _LOGGER.warning("Authenticate failed: %s", resp)
            return None

    def register_notification_callback(self, callback: Callable[[Dict[str, Any]], None]) -> str:
        """Register a callback for notifications. Returns a token for later removal."""
        token = str(uuid.uuid4())
        self._notification_callbacks.append((token, callback))
        return token

    def unregister_notification_callback(self, token: str) -> None:
        """Remove a notification callback by token."""
        self._notification_callbacks = [
            (t, cb) for t, cb in self._notification_callbacks if t != token
        ]

    async def start_reader_loop(self) -> None:
        """Start the background reader loop to handle notifications and responses."""
        if self._reader_task is not None:
            return
        
        if not self._reader:
            raise RuntimeError("Not connected")
            
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def stop_reader_loop(self) -> None:
        """Stop the background reader loop."""
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

    async def _reader_loop(self) -> None:
        """Background task to read incoming messages and dispatch them."""
        try:
            while self._reader and not self._reader.at_eof():
                try:
                    line = await self._reader.readline()
                    if not line:
                        break
                    
                    text = line.decode("utf-8").strip()
                    if not text:
                        continue
                        
                    message = json.loads(text)
                    
                    # Check if this is a notification
                    if "notification" in message or ("method" in message and "id" not in message):
                        # This is a notification - dispatch to callbacks
                        await self._dispatch_notification(message)
                    else:
                        # This is a response - check if we have a pending future for it
                        msg_id = message.get("id")
                        if msg_id is not None and msg_id in self._pending_responses:
                            future = self._pending_responses.pop(msg_id)
                            if not future.done():
                                future.set_result(message)
                        else:
                            # Fallback: put in response queue for legacy compatibility
                            await self._response_queue.put(message)
                        
                except json.JSONDecodeError as e:
                    _LOGGER.warning("Invalid JSON received: %s", e)
                except Exception as e:
                    _LOGGER.warning("Error in reader loop: %s", e)
                    break
        except asyncio.CancelledError:
            # Cancel all pending responses
            for future in self._pending_responses.values():
                if not future.done():
                    future.cancel()
            self._pending_responses.clear()
        except Exception as e:
            # Mark all pending responses as failed
            for future in self._pending_responses.values():
                if not future.done():
                    future.set_exception(e)
            self._pending_responses.clear()

    async def _dispatch_notification(self, notification: Dict[str, Any]) -> None:
        """Dispatch a notification to all registered callbacks."""
        for token, callback in self._notification_callbacks:
            try:
                callback(notification)
            except Exception as e:
                pass

    async def start_keepalive(self, interval: float = 30.0) -> None:
        """Start sending periodic keepalive messages."""
        if self._keepalive_task is not None:
            return
            
        self._keepalive_task = asyncio.create_task(self._keepalive_loop(interval))

    async def stop_keepalive(self) -> None:
        """Stop sending keepalive messages."""
        if self._keepalive_task:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
            self._keepalive_task = None

    async def _keepalive_loop(self, interval: float) -> None:
        """Background task to send periodic keepalive messages."""
        try:
            while True:
                await asyncio.sleep(interval)
                try:
                    payload = {"id": self._next_id, "method": "JSONRPC.KeepAlive", "params": {}}
                    self._next_id += 1
                    await self.send_request(payload)
                except Exception as e:
                    pass
        except asyncio.CancelledError:
            pass

    async def send_request_with_response(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 10.0) -> Dict[str, Any]:
        """Send a request and wait for the response, compatible with reader loop."""
        if not self._writer or not self._reader:
            raise RuntimeError("Not connected")
            
        request_id = self._next_id
        self._next_id += 1
        
        payload = {"id": request_id, "method": method}
        if params:
            payload["params"] = params
            
        # Create a future for this specific request
        response_future = asyncio.Future()
        self._pending_responses[request_id] = response_future
        
        try:
            await self.send_request(payload)
            
            # Wait for the specific response
            response = await asyncio.wait_for(response_future, timeout=timeout)
            return response
            
        except asyncio.TimeoutError:
            raise asyncio.TimeoutError(f"Timeout waiting for response to {method}")
        finally:
            # Clean up pending response
            self._pending_responses.pop(request_id, None)
