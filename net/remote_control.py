"""
Remote control client for controlling other Restim instances over WebSocket.

Connects to remote Restim WebUI servers and forwards position/state changes.
"""

import asyncio
import json
import logging
import threading
import time
from typing import Optional, List, Dict, Set
from dataclasses import dataclass

from PySide6 import QtCore

logger = logging.getLogger('restim.remote_control')


@dataclass
class RemoteInstance:
    """Configuration for a remote Restim instance."""
    url: str
    enabled: bool = True
    username: str = ""
    password: str = ""


class RemoteControlClient(QtCore.QObject):
    """
    Manages connections to remote Restim instances and forwards state changes.
    """

    # Signals for UI updates
    connection_changed = QtCore.Signal(str, bool)  # url, connected

    def __init__(self, parent=None):
        super().__init__(parent)
        self._instances: List[RemoteInstance] = []
        self._connections: Dict[str, any] = {}  # url -> websocket
        self._connected: Set[str] = set()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

        # Throttling for position updates
        self._last_position_send = 0
        self._position_throttle_ms = 33  # ~30Hz

    def set_instances(self, instances: List[RemoteInstance]):
        """Update the list of remote instances."""
        with self._lock:
            self._instances = instances.copy()

        # Restart connections if running
        if self._running:
            self._schedule_reconnect()

    def get_instances(self) -> List[RemoteInstance]:
        """Get the current list of remote instances."""
        with self._lock:
            return self._instances.copy()

    def start(self):
        """Start the remote control client."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the remote control client."""
        self._running = False

        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

        self._connected.clear()
        self._connections.clear()

    def is_connected(self, url: str) -> bool:
        """Check if connected to a specific instance."""
        return url in self._connected

    def get_connected_count(self) -> int:
        """Get the number of connected instances."""
        return len(self._connected)

    def send_position(self, alpha: float, beta: float, gamma: float = 0.0):
        """Send position update to all connected instances."""
        now = time.time() * 1000
        if now - self._last_position_send < self._position_throttle_ms:
            return
        self._last_position_send = now

        message = {
            "type": "set_position",
            "payload": {
                "alpha": alpha,
                "beta": beta,
                "gamma": gamma,
                "interval": 0.1
            }
        }
        self._broadcast(message)

    def send_volume(self, value: float):
        """Send volume update to all connected instances."""
        message = {
            "type": "set_volume",
            "payload": {"value": value}
        }
        self._broadcast(message)

    def send_carrier(self, frequency: float):
        """Send carrier frequency update to all connected instances."""
        message = {
            "type": "set_carrier",
            "payload": {"frequency": frequency}
        }
        self._broadcast(message)

    def send_play(self):
        """Send play command to all connected instances."""
        message = {"type": "play", "payload": {}}
        self._broadcast(message)

    def send_stop(self):
        """Send stop command to all connected instances."""
        message = {"type": "stop", "payload": {}}
        self._broadcast(message)

    def send_pulse_params(self, **kwargs):
        """Send pulse parameter updates to all connected instances."""
        message = {
            "type": "set_pulse_params",
            "payload": kwargs
        }
        self._broadcast(message)

    def _broadcast(self, message: dict):
        """Broadcast a message to all connected instances."""
        if not self._loop or not self._running:
            return

        msg_str = json.dumps(message)

        with self._lock:
            for url, ws in list(self._connections.items()):
                if ws and url in self._connected:
                    try:
                        asyncio.run_coroutine_threadsafe(
                            ws.send(msg_str),
                            self._loop
                        )
                    except Exception as e:
                        logger.debug(f"Failed to send to {url}: {e}")

    def _run_event_loop(self):
        """Run the asyncio event loop in a background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._loop.run_until_complete(self._manage_connections())
        except Exception as e:
            logger.exception(f"Event loop error: {e}")
        finally:
            self._loop.close()
            self._loop = None

    async def _manage_connections(self):
        """Main connection management loop."""
        import websockets

        while self._running:
            # Get enabled instances
            with self._lock:
                instances = [i for i in self._instances if i.enabled]

            # Start connections for new instances
            tasks = []
            for instance in instances:
                if instance.url not in self._connections:
                    tasks.append(self._connect(instance))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            # Wait before checking again
            await asyncio.sleep(5.0)

    async def _connect(self, instance: RemoteInstance):
        """Connect to a remote instance."""
        import websockets

        # Convert HTTP URL to WebSocket URL
        ws_url = instance.url
        if ws_url.startswith('http://'):
            ws_url = 'ws://' + ws_url[7:]
        elif ws_url.startswith('https://'):
            ws_url = 'wss://' + ws_url[8:]

        # WebSocket is on port + 1
        if ':' in ws_url.split('/')[-1]:
            # Has port
            parts = ws_url.rsplit(':', 1)
            try:
                port = int(parts[1].split('/')[0])
                ws_url = f"{parts[0]}:{port + 1}"
            except ValueError:
                pass

        logger.info(f"Connecting to remote instance: {ws_url}")

        try:
            # Build headers for auth if needed
            headers = {}
            if instance.username and instance.password:
                import base64
                credentials = base64.b64encode(
                    f"{instance.username}:{instance.password}".encode()
                ).decode()
                headers['Authorization'] = f'Basic {credentials}'

            ws = await websockets.connect(
                ws_url,
                extra_headers=headers if headers else None,
                ping_interval=30,
                ping_timeout=10
            )

            with self._lock:
                self._connections[instance.url] = ws
                self._connected.add(instance.url)

            self.connection_changed.emit(instance.url, True)
            logger.info(f"Connected to remote instance: {instance.url}")

            # Keep connection alive and handle messages
            try:
                async for message in ws:
                    # We don't need to process incoming messages for now
                    pass
            except websockets.ConnectionClosed:
                pass

        except Exception as e:
            logger.warning(f"Failed to connect to {instance.url}: {e}")
        finally:
            with self._lock:
                self._connections.pop(instance.url, None)
                self._connected.discard(instance.url)
            self.connection_changed.emit(instance.url, False)

    def _schedule_reconnect(self):
        """Schedule a reconnection attempt."""
        if self._loop:
            # Close existing connections to trigger reconnect
            with self._lock:
                for url in list(self._connections.keys()):
                    ws = self._connections.get(url)
                    if ws:
                        asyncio.run_coroutine_threadsafe(ws.close(), self._loop)
