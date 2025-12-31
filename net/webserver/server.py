"""
Web UI Server for Restim remote control.

Provides HTTP server for static files and WebSocket for real-time bidirectional
communication. Runs in a separate thread with asyncio event loop.
"""

import asyncio
import base64
import json
import logging
import mimetypes
import os
import threading
import weakref
from http import HTTPStatus
from http.server import HTTPServer, SimpleHTTPRequestHandler
from typing import Optional, Set, TYPE_CHECKING

import websockets
from websockets.server import serve as websocket_serve, WebSocketServerProtocol

from PySide6 import QtCore

from qt_ui import settings
from qt_ui.resources import resource_path
from .auth import check_basic_auth, create_auth_challenge_headers
from .handlers import WebSocketHandler
from .protocol import Message, MessageType, InvalidMessageException

if TYPE_CHECKING:
    from qt_ui.mainwindow import Window

logger = logging.getLogger('restim.webserver')


class WebUIServer(QtCore.QObject):
    """
    HTTP + WebSocket server for browser-based control.

    Integrates with Qt's main thread via signals for thread-safe state access.
    """

    # Signals for thread-safe communication with main thread
    request_state = QtCore.Signal()
    state_ready = QtCore.Signal(str)

    def __init__(self, parent, main_window: 'Window'):
        super().__init__(parent)
        self._main_window_ref = weakref.ref(main_window)
        self._handler: Optional[WebSocketHandler] = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._http_server: Optional[HTTPServer] = None
        self._ws_server = None
        self._ws_clients: Set[WebSocketServerProtocol] = set()
        self._running = False
        self._broadcast_timer: Optional[QtCore.QTimer] = None

        # Start server if enabled
        if settings.webui_enabled.get():
            self._start()

    @property
    def main_window(self) -> Optional['Window']:
        return self._main_window_ref()

    def _start(self):
        """Start the HTTP and WebSocket servers in a background thread."""
        if self._running:
            return

        port = settings.webui_port.get()
        localhost_only = settings.webui_localhost_only.get()
        host = '127.0.0.1' if localhost_only else '0.0.0.0'

        # Initialize handler
        mw = self.main_window
        if mw:
            self._handler = WebSocketHandler(mw)

        # Start asyncio event loop in background thread
        self._running = True
        self._thread = threading.Thread(target=self._run_event_loop, args=(host, port), daemon=True)
        self._thread.start()

        # Start broadcast timer in Qt thread (30Hz for position updates)
        self._broadcast_timer = QtCore.QTimer(self)
        self._broadcast_timer.timeout.connect(self._broadcast_position)
        self._broadcast_timer.start(33)

        logger.info(f"Web UI server starting at http://{host}:{port}")

    def stop(self):
        """Stop the servers and clean up."""
        self._running = False

        if self._broadcast_timer:
            self._broadcast_timer.stop()
            self._broadcast_timer = None

        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

        logger.info("Web UI server stopped")

    def _run_event_loop(self, host: str, port: int):
        """Run asyncio event loop in background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._loop.run_until_complete(self._serve(host, port))
        except Exception as e:
            logger.exception(f"Server error: {e}")
        finally:
            self._loop.close()

    async def _serve(self, host: str, port: int):
        """Start HTTP and WebSocket servers."""
        username = settings.webui_username.get()
        password = settings.webui_password.get()

        # Start WebSocket server on port + 1
        ws_port = port + 1

        async def ws_handler(websocket: WebSocketServerProtocol, path: str):
            await self._handle_websocket(websocket, path, username, password)

        try:
            self._ws_server = await websocket_serve(
                ws_handler,
                host,
                ws_port,
            )
            logger.info(f"WebSocket server listening on ws://{host}:{ws_port}")
        except Exception as e:
            logger.error(f"Failed to start WebSocket server: {e}")
            return

        # Run HTTP server in thread pool
        http_future = self._loop.run_in_executor(
            None,
            self._run_http_server,
            host,
            port,
            username,
            password
        )

        # Keep running until stopped
        while self._running:
            await asyncio.sleep(0.1)

        # Cleanup
        if self._ws_server:
            self._ws_server.close()
            await self._ws_server.wait_closed()

    def _run_http_server(self, host: str, port: int, username: str, password: str):
        """Run HTTP server for static files."""
        webui_dir = resource_path('resources/webui')

        class AuthHandler(SimpleHTTPRequestHandler):
            def __init__(self, *args, directory=None, **kwargs):
                super().__init__(*args, directory=webui_dir, **kwargs)

            def do_GET(self):
                # Check authentication
                auth_header = self.headers.get('Authorization', '')
                if not check_basic_auth(auth_header, username, password):
                    self.send_response(HTTPStatus.UNAUTHORIZED)
                    for key, value in create_auth_challenge_headers().items():
                        self.send_header(key, value)
                    self.end_headers()
                    self.wfile.write(b'Unauthorized')
                    return

                # Serve index.html for root
                if self.path == '/':
                    self.path = '/index.html'

                super().do_GET()

            def log_message(self, format, *args):
                logger.debug(f"HTTP: {format % args}")

        try:
            self._http_server = HTTPServer((host, port), AuthHandler)
            logger.info(f"HTTP server listening on http://{host}:{port}")

            while self._running:
                self._http_server.handle_request()

        except Exception as e:
            logger.error(f"HTTP server error: {e}")
        finally:
            if self._http_server:
                self._http_server.server_close()

    async def _handle_websocket(self, websocket: WebSocketServerProtocol, path: str,
                                 username: str, password: str):
        """Handle a WebSocket connection."""
        # Authentication via first message or header
        if password:
            # Check if auth is in request headers (for some clients)
            auth_header = websocket.request_headers.get('Authorization', '')
            if not check_basic_auth(auth_header, username, password):
                # Wait for auth message
                try:
                    auth_msg = await asyncio.wait_for(websocket.recv(), timeout=10.0)
                    auth_data = json.loads(auth_msg)
                    if auth_data.get('type') == 'auth':
                        provided_user = auth_data.get('username', '')
                        provided_pass = auth_data.get('password', '')
                        if provided_user != username or provided_pass != password:
                            await websocket.send(json.dumps({
                                "type": "error",
                                "payload": {"error": "Invalid credentials"}
                            }))
                            await websocket.close(1008, "Invalid credentials")
                            return
                    else:
                        await websocket.close(1008, "Authentication required")
                        return
                except asyncio.TimeoutError:
                    await websocket.close(1008, "Authentication timeout")
                    return
                except Exception as e:
                    logger.debug(f"Auth error: {e}")
                    await websocket.close(1008, "Authentication error")
                    return

        # Add to connected clients
        self._ws_clients.add(websocket)
        logger.info(f"WebSocket client connected: {websocket.remote_address}")

        try:
            # Send welcome message with initial state
            if self._handler:
                welcome = Message(MessageType.CONNECTED, {
                    "version": "1.0",
                    "wsPort": settings.webui_port.get() + 1,
                })
                await websocket.send(welcome.to_json())

                # Send full state
                state = Message(MessageType.STATE_UPDATE, self._handler.get_full_state())
                await websocket.send(state.to_json())

            # Handle messages
            async for raw_message in websocket:
                await self._process_message(websocket, raw_message)

        except websockets.exceptions.ConnectionClosed:
            logger.debug(f"WebSocket client disconnected: {websocket.remote_address}")
        except Exception as e:
            logger.exception(f"WebSocket error: {e}")
        finally:
            self._ws_clients.discard(websocket)

    async def _process_message(self, websocket: WebSocketServerProtocol, raw_message: str):
        """Process an incoming WebSocket message."""
        if not self._handler:
            return

        try:
            message = Message.from_json(raw_message)
            response = self._handler.handle_message(message)

            if response:
                await websocket.send(response.to_json())

            # Broadcast state changes to all clients
            if message.type in (MessageType.SET_VOLUME, MessageType.SET_CARRIER,
                               MessageType.SET_PULSE_PARAMS, MessageType.SET_PATTERN,
                               MessageType.SET_VIBRATION, MessageType.PLAY, MessageType.STOP):
                await self._broadcast_state_update()

        except InvalidMessageException as e:
            error = Message(MessageType.ERROR, {"error": str(e)})
            await websocket.send(error.to_json())
        except Exception as e:
            logger.exception(f"Message processing error: {e}")
            error = Message(MessageType.ERROR, {"error": "Internal error"})
            await websocket.send(error.to_json())

    async def _broadcast_state_update(self):
        """Broadcast full state to all connected clients."""
        if not self._handler or not self._ws_clients:
            return

        state = Message(MessageType.STATE_UPDATE, self._handler.get_full_state())
        message = state.to_json()

        for client in list(self._ws_clients):
            try:
                await client.send(message)
            except Exception:
                pass

    def _broadcast_position(self):
        """Broadcast position update (called from Qt timer)."""
        if not self._handler or not self._ws_clients or not self._loop:
            return

        position = self._handler.get_position_update()
        message = Message(MessageType.POSITION_UPDATE, position)
        json_msg = message.to_json()

        # Schedule broadcast in asyncio loop
        asyncio.run_coroutine_threadsafe(
            self._broadcast_to_all(json_msg),
            self._loop
        )

    async def _broadcast_to_all(self, message: str):
        """Broadcast a message to all connected clients."""
        for client in list(self._ws_clients):
            try:
                await client.send(message)
            except Exception:
                pass

    def broadcast_play_state(self, play_state):
        """Broadcast play state change (called from MainWindow)."""
        if not self._ws_clients or not self._loop:
            return

        message = Message(MessageType.PLAY_STATE_UPDATE, {"state": play_state.name})
        json_msg = message.to_json()

        asyncio.run_coroutine_threadsafe(
            self._broadcast_to_all(json_msg),
            self._loop
        )

    def get_client_count(self) -> int:
        """Return number of connected WebSocket clients."""
        return len(self._ws_clients)
