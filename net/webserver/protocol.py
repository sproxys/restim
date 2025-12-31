"""
WebSocket message protocol definitions for the Restim Web UI.

All messages use JSON format with consistent structure:
{
    "type": "message_type",
    "payload": { ... },
    "timestamp": 1234567890.123
}
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Optional
import json
import time


class MessageType(str, Enum):
    # Client -> Server (Commands)
    GET_STATE = "get_state"
    SET_POSITION = "set_position"
    SET_VOLUME = "set_volume"
    SET_CARRIER = "set_carrier"
    SET_PULSE_PARAMS = "set_pulse_params"
    SET_VIBRATION = "set_vibration"
    SET_PATTERN = "set_pattern"
    SET_CALIBRATION = "set_calibration"
    PLAY = "play"
    STOP = "stop"

    # Server -> Client (Events)
    STATE_UPDATE = "state_update"
    POSITION_UPDATE = "position_update"
    VOLUME_UPDATE = "volume_update"
    PLAY_STATE_UPDATE = "play_state_update"
    CARRIER_UPDATE = "carrier_update"
    PULSE_UPDATE = "pulse_update"
    PATTERN_UPDATE = "pattern_update"
    VIBRATION_UPDATE = "vibration_update"
    ERROR = "error"
    CONNECTED = "connected"


@dataclass
class Message:
    """WebSocket message container."""
    type: MessageType
    payload: dict = field(default_factory=dict)
    timestamp: float = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()

    def to_json(self) -> str:
        return json.dumps({
            "type": self.type.value if isinstance(self.type, MessageType) else self.type,
            "payload": self.payload,
            "timestamp": self.timestamp
        })

    @classmethod
    def from_json(cls, data: str) -> 'Message':
        try:
            obj = json.loads(data)
            return cls(
                type=MessageType(obj["type"]),
                payload=obj.get("payload", {}),
                timestamp=obj.get("timestamp")
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise InvalidMessageException(f"Failed to parse message: {e}")


class InvalidMessageException(Exception):
    """Raised when a message cannot be parsed or is invalid."""
    pass
