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
    RECONNECT_INITIAL_DELAY = 1  # seconds
    RECONNECT_MAX_DELAY = 15  # seconds

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
        self._host: Optional[str] = None
        self._port: Optional[int] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._reconnect_delay: float = self.RECONNECT_INITIAL_DELAY
        self._is_connected: bool = False
        self._reconnection_callbacks: List[Callable[[], None]] = []
        self._disconnection_callbacks: List[Callable[[], None]] = []
        self._session_id: Optional[str] = None

    async def connect(self, host: str, port: int, timeout: float = 10.0) -> None:
        """Open a TCP+TLS connection to the device.

        Uses an SSLContext created in an executor to avoid blocking the event loop.
        """
        if self._writer is not None:
            _LOGGER.debug("Connect called, but client already connected.")
            return

        self._host = host
        self._port = port

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
        _LOGGER.info("Successfully connected to %s:%s", host, port)
        self._reconnect_delay = self.RECONNECT_INITIAL_DELAY # Reset delay on successful connect

    async def close(self) -> None:
        """Close the connection and stop all background tasks.

        Properly handle SSL connection cleanup to avoid SSL errors.
        """
        # Stop background tasks first
        await self.stop_keepalive()
        await self.stop_reader_loop()
        
        # Clear session state - important for reconnection to get fresh sessionId
        self._session_id = None
        _LOGGER.debug("Cleared sessionId during connection close")
        
        # Clear pending responses to avoid hanging futures
        for future in self._pending_responses.values():
            if not future.done():
                future.cancel()
        self._pending_responses.clear()
        
        if self._writer:
            try:
                # For SSL connections, we need to be more careful about closing
                # Check if the transport is SSL
                transport = self._writer.transport
                if hasattr(transport, '_ssl_protocol') and transport._ssl_protocol:
                    # This is an SSL connection
                    try:
                        # Write EOF to signal clean shutdown
                        self._writer.write_eof()
                        await asyncio.wait_for(self._writer.drain(), timeout=1.0)
                    except (asyncio.TimeoutError, Exception):
                        # If graceful shutdown fails, force close
                        pass
                
                self._writer.close()
                
                # Wait for the writer to close, but with a timeout and ignore SSL cleanup errors
                try:
                    await asyncio.wait_for(self._writer.wait_closed(), timeout=2.0)
                except asyncio.TimeoutError:
                    _LOGGER.debug("Writer close timeout - connection may already be closed")
                except Exception as exc:
                    # Ignore SSL cleanup errors as they're expected during ungraceful shutdowns
                    if "APPLICATION_DATA_AFTER_CLOSE_NOTIFY" not in str(exc):
                        _LOGGER.debug("Writer close exception (ignoring): %s", exc)
                        
            except Exception as exc:
                # Ignore all exceptions during close as the connection may already be broken
                _LOGGER.debug("Exception during writer close (ignoring): %s", exc)
                
        self._reader = None
        self._writer = None
        self._reconnect_task = None # Ensure reconnect task is cleared on close

    async def send_request(self, payload: Dict[str, Any]) -> None:
        """Send a JSON-RPC payload (adds newline).

        If a client token is set via update_token(), attach it to the payload
        under the 'token' key unless the payload already contains a token.
        """
        if not self._writer:
            _LOGGER.debug("send_request called when not connected. Attempting reconnect.")
            await self._handle_disconnect("send_request called when not connected")
            if not self._writer: # Recheck after reconnect attempt
                raise RuntimeError("Not connected after reconnect attempt")
        try:
            # Attach token if available and not already present
            if getattr(self, "_token", None) and "token" not in payload:
                payload = dict(payload)  # shallow copy
                payload["token"] = self._token
            msg = json.dumps(payload, ensure_ascii=False) + "\n"
            self._writer.write(msg.encode("utf-8"))
            await self._writer.drain()
        except (BrokenPipeError, ConnectionResetError) as e:
            _LOGGER.warning("Connection lost during send_request: %s", e)
            await self._handle_disconnect(f"Connection lost during send_request: {e}")
            raise RuntimeError("Connection lost, reconnecting") from e # Re-raise to propagate error to caller

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
            _LOGGER.debug("recv_response called when not connected. Attempting reconnect.")
            await self._handle_disconnect("recv_response called when not connected")
            if not self._reader: # Recheck after reconnect attempt
                raise RuntimeError("Not connected after reconnect attempt")
        try:
            line = await asyncio.wait_for(self._reader.readline(), timeout=timeout)
            text = line.decode("utf-8").strip()
            return json.loads(text)
        except (asyncio.IncompleteReadError, ConnectionResetError) as e:
            _LOGGER.warning("Connection lost during recv_response: %s", e)
            await self._handle_disconnect(f"Connection lost during recv_response: {e}")
            raise RuntimeError("Connection lost, reconnecting") from e # Re-raise to propagate error to caller
        except asyncio.TimeoutError as e:
            _LOGGER.warning("Timeout during recv_response: %s", e)
            await self._handle_disconnect(f"Timeout during recv_response: {e}")
            raise # Re-raise the timeout error

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
                params = resp.get("params", {})
                # Store sessionId for use in keepalive and other requests (optional in some protocol versions)
                session_id = params.get("sessionId")
                if session_id:
                    self._session_id = session_id
                    _LOGGER.info("Stored sessionId from hello response: %s", session_id)
                else:
                    _LOGGER.debug("Hello succeeded without sessionId (not required by this protocol version)")
                return params
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

    def register_reconnection_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback to be called when reconnection succeeds."""
        if callback not in self._reconnection_callbacks:
            self._reconnection_callbacks.append(callback)

    def unregister_reconnection_callback(self, callback: Callable[[], None]) -> None:
        """Remove a reconnection callback."""
        if callback in self._reconnection_callbacks:
            self._reconnection_callbacks.remove(callback)

    def register_disconnection_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback to be called when disconnection is detected."""
        if callback not in self._disconnection_callbacks:
            self._disconnection_callbacks.append(callback)

    def unregister_disconnection_callback(self, callback: Callable[[], None]) -> None:
        """Remove a disconnection callback."""
        if callback in self._disconnection_callbacks:
            self._disconnection_callbacks.remove(callback)

    @property
    def is_connected(self) -> bool:
        """Return True if the client is connected."""
        return self._writer is not None and not self._writer.is_closing()

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
        _LOGGER.debug("Reader loop starting")
        disconnect_triggered = False
        try:
            while self._reader and not self._reader.at_eof():
                try:
                    # Add timeout to readline to detect stale connections
                    line = await asyncio.wait_for(self._reader.readline(), timeout=60.0)
                    if not line:
                        _LOGGER.warning("Reader received empty line, connection may be closed")
                        # Trigger disconnect handling when connection is closed
                        if not disconnect_triggered:
                            disconnect_triggered = True
                            asyncio.create_task(self._handle_disconnect("Reader received empty line"))
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
                        
                except asyncio.TimeoutError:
                    # No data received for 60 seconds - this is normal, continue
                    # The keepalive mechanism will detect connection issues
                    continue
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    _LOGGER.warning("Invalid data received: %s", e)
                    continue
                except (BrokenPipeError, ConnectionResetError, OSError) as e:
                    _LOGGER.warning("Connection error in reader loop: %s", e)
                    # Trigger disconnect handling only once
                    if not disconnect_triggered:
                        disconnect_triggered = True
                        asyncio.create_task(self._handle_disconnect(f"Connection error in reader loop: {e}"))
                    break
                except Exception as e:
                    _LOGGER.warning("Unexpected error in reader loop: %s", e)
                    # For unexpected errors that might indicate connection issues
                    if "readuntil" in str(e) or "another coroutine" in str(e):
                        if not disconnect_triggered:
                            disconnect_triggered = True
                            asyncio.create_task(self._handle_disconnect(f"Concurrent access error: {e}"))
                    break
        except asyncio.CancelledError:
            # Cancel all pending responses
            for future in self._pending_responses.values():
                if not future.done():
                    future.cancel()
            self._pending_responses.clear()
            _LOGGER.debug("Reader loop cancelled")
        except Exception as e:
            _LOGGER.error("Fatal error in reader loop: %s", e)
            # Mark all pending responses as failed
            for future in self._pending_responses.values():
                if not future.done():
                    future.set_exception(e)
            self._pending_responses.clear()
            # Trigger disconnect handling only once
            if not disconnect_triggered:
                disconnect_triggered = True
                asyncio.create_task(self._handle_disconnect(f"Fatal error in reader loop: {e}"))
        finally:
            # Clean up pending responses but don't trigger another disconnect
            for future in self._pending_responses.values():
                if not future.done():
                    future.cancel()
            self._pending_responses.clear()
            _LOGGER.debug("Reader loop finished")

    async def _handle_disconnect(self, reason: str) -> None:
        """Handle disconnection by closing current connection and starting reconnect."""
        _LOGGER.warning("Handling disconnect: %s", reason)
        
        # Use a lock to prevent concurrent disconnect handling
        async with self._lock:
            if self._reconnect_task and not self._reconnect_task.done():
                # Reconnect already in progress
                _LOGGER.debug("Reconnection already in progress, skipping duplicate disconnect handling")
                return

            # Notify all registered disconnection callbacks
            _LOGGER.info("Notifying %d disconnection callbacks", len(self._disconnection_callbacks))
            for callback in self._disconnection_callbacks:
                try:
                    callback()
                except Exception as e:
                    _LOGGER.error("Error in disconnection callback: %s", e)

            # Clean up existing connection
            await self.close()

            # Only start reconnect if we have host/port configured
            if self._host and self._port:
                # Start reconnect loop
                self._reconnect_task = asyncio.create_task(self._reconnect_loop())
            else:
                _LOGGER.error("Cannot start reconnection: host or port not configured")

    async def _reconnect_loop(self) -> None:
        """Attempt to reconnect with exponential backoff."""
        _LOGGER.info("Starting reconnection attempts to %s:%s", self._host, self._port)
        attempt = 0
        max_attempts = 100  # Prevent infinite loops, but allow many retries
        
        while attempt < max_attempts:
            attempt += 1
            _LOGGER.info("Reconnection attempt %d/%d in %s seconds...", attempt, max_attempts, self._reconnect_delay)
            await asyncio.sleep(self._reconnect_delay)
            
            try:
                _LOGGER.info("Attempting to reconnect to %s:%s (attempt %d)", self._host, self._port, attempt)
                
                # Step 1: Establish TCP connection with timeout
                try:
                    await asyncio.wait_for(self.connect(self._host, self._port), timeout=10.0)
                    _LOGGER.info("TCP connection re-established to %s:%s", self._host, self._port)
                except asyncio.TimeoutError:
                    _LOGGER.warning("Timeout connecting to %s:%s (attempt %d), will retry", 
                                   self._host, self._port, attempt)
                    raise RuntimeError("Connection timeout")
                
                # Step 2: Perform handshake with extended timeout
                try:
                    hello_params = await asyncio.wait_for(self.hello(timeout=20.0), timeout=25.0)
                    if not hello_params:
                        _LOGGER.warning("Hello handshake failed after reconnection (attempt %d), will retry", attempt)
                        raise RuntimeError("Hello handshake returned None")
                    _LOGGER.info("Handshake successful after reconnection (attempt %d)", attempt)
                except asyncio.TimeoutError:
                    _LOGGER.warning("Timeout during handshake (attempt %d), will retry", attempt)
                    raise RuntimeError("Handshake timeout")
                
                # Step 3: Restart reader loop
                try:
                    await self.start_reader_loop()
                    _LOGGER.debug("Reader loop restarted after reconnection")
                except Exception as e:
                    _LOGGER.warning("Failed to start reader loop (attempt %d): %s, will retry", attempt, e)
                    raise RuntimeError(f"Failed to start reader loop: {e}")
                
                # Step 4: Restart keepalive loop
                try:
                    await self.start_keepalive()
                    _LOGGER.debug("Keepalive loop restarted after reconnection")
                except Exception as e:
                    _LOGGER.warning("Failed to start keepalive (attempt %d): %s, will retry", attempt, e)
                    raise RuntimeError(f"Failed to start keepalive: {e}")
                
                # Step 5: Verify connection health before declaring success
                try:
                    health_check = await asyncio.wait_for(self.verify_connection_health(), timeout=10.0)
                    if not health_check:
                        _LOGGER.warning("Connection health check failed after reconnection (attempt %d), will retry", attempt)
                        raise RuntimeError("Connection health check failed")
                    _LOGGER.info("Connection health verified after reconnection")
                except asyncio.TimeoutError:
                    _LOGGER.warning("Timeout during health check (attempt %d), will retry", attempt)
                    raise RuntimeError("Health check timeout")
                
                # Success! Reset delay and notify callbacks
                self._reconnect_delay = self.RECONNECT_INITIAL_DELAY
                
                # Notify all registered reconnection callbacks
                _LOGGER.info("Notifying %d reconnection callbacks", len(self._reconnection_callbacks))
                for callback in self._reconnection_callbacks:
                    try:
                        callback()
                    except Exception as e:
                        _LOGGER.error("Error in reconnection callback: %s", e)
                
                _LOGGER.info("Reconnection completed successfully after %d attempts", attempt)
                break  # Exit reconnect loop
                
            except Exception as e:
                _LOGGER.warning("Reconnection attempt %d failed: %s", attempt, e)
                
                # Clean up partial connection
                try:
                    await self.close()
                except Exception as cleanup_error:
                    _LOGGER.debug("Error during cleanup after failed reconnect: %s", cleanup_error)
                
                # Increase delay with exponential backoff
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self.RECONNECT_MAX_DELAY
                )
                
                # Continue to next attempt
                continue
        
        # If we exhausted all attempts
        if attempt >= max_attempts:
            _LOGGER.error("Reconnection failed after %d attempts, giving up", max_attempts)

    async def _dispatch_notification(self, notification: Dict[str, Any]) -> None:
        """Dispatch a notification to all registered callbacks."""
        for token, callback in self._notification_callbacks:
            try:
                callback(notification)
            except Exception as e:
                _LOGGER.error("Error in notification callback %s: %s", token, e)

    async def start_keepalive(self, interval: float = 15.0) -> None:
        """Start sending periodic keepalive messages."""
        if self._keepalive_task is not None:
            return

        # SessionId is required by the protocol for keepalive
        if self._session_id:
            _LOGGER.debug("Starting keepalive loop with sessionId: %s", self._session_id)
        else:
            _LOGGER.debug("Starting keepalive loop without sessionId")

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
                    # Build keepalive params - sessionId is required by the protocol
                    keepalive_params = {"sessionId": self._session_id}

                    # Use send_request_with_response to detect timeouts
                    response = await self.send_request_with_response("JSONRPC.KeepAlive", keepalive_params, timeout=10.0)
                    if response.get("status") != "success":
                        _LOGGER.warning("Keepalive returned non-success: %s", response)
                        await self._handle_disconnect("Keepalive returned non-success")
                        break
                except asyncio.TimeoutError:
                    _LOGGER.warning("Keepalive timeout - connection appears to be lost")
                    await self._handle_disconnect("Keepalive timeout")
                    break
                except Exception as e:
                    _LOGGER.warning("Keepalive failed: %s", e)
                    await self._handle_disconnect(f"Keepalive failed: {e}")
                    break
        except asyncio.CancelledError:
            pass

    async def send_request_with_response(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 10.0) -> Dict[str, Any]:
        """Send a request and wait for the response, compatible with reader loop."""
        if not self._writer or not self._reader:
            _LOGGER.debug("send_request_with_response called when not connected. Attempting reconnect.")
            await self._handle_disconnect("send_request_with_response called when not connected")
            # If reconnect is successful, retry request
            if not self._writer or not self._reader:
                raise RuntimeError("Not connected after reconnect attempt")
            
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
            _LOGGER.warning("Timeout waiting for response to %s", method)
            raise asyncio.TimeoutError(f"Timeout waiting for response to {method}")
        except RuntimeError as e: # Catch "Connection lost, reconnecting" from send_request
            _LOGGER.warning("RuntimeError during send_request_with_response: %s", e)
            raise
        finally:
            # Clean up pending response
            self._pending_responses.pop(request_id, None)

    async def verify_connection_health(self) -> bool:
        """Verify that the connection is still healthy by sending a ping."""
        try:
            if not self.is_connected:
                return False
                
            # Send a simple hello request to verify connection
            response = await self.send_request_with_response("JSONRPC.Hello", {"locale": "en_US"}, timeout=5.0)
            return response.get("status") == "success"
            
        except Exception as e:
            _LOGGER.debug("Connection health check failed: %s", e)
            return False

    async def ensure_connected(self) -> bool:
        """Ensure we have a working connection, attempting reconnect if needed."""
        if self.is_connected and await self.verify_connection_health():
            return True
            
        _LOGGER.info("Connection not healthy, attempting to reconnect...")
        await self._handle_disconnect("Connection health check failed")
        
        # Wait a bit for reconnection to complete
        for _ in range(10):  # Wait up to 10 seconds
            await asyncio.sleep(1.0)
            if self.is_connected and await self.verify_connection_health():
                return True
                
        return False
