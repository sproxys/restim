"""
Web UI Server package for Restim remote control.

Provides HTTP + WebSocket server for browser-based control with authentication.
"""

from .server import WebUIServer
from .protocol import Message, MessageType, InvalidMessageException
from .handlers import WebSocketHandler

__all__ = ['WebUIServer', 'Message', 'MessageType', 'InvalidMessageException', 'WebSocketHandler']
