# app/notifications/protocol.py
"""
WebSocket protocol v1: typed envelopes and versioning.
"""

from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
import json

class WSMessageType(str, Enum):
    # Client → Server
    PING = "ping"
    GET_UNREAD = "get_unread"
    MARK_READ = "mark_read"

    # Server → Client
    PONG = "pong"
    UNREAD_NOTIFICATIONS = "unread_notifications"
    NEW_NOTIFICATION = "new_notification"
    NOTIFICATION_UPDATED = "notification_updated"
    ERROR = "error"

class WSMessage(BaseModel):
    version: int = Field(default=1, description="Protocol version")
    type: WSMessageType
    payload: Optional[Dict[str, Any]] = Field(default=None, description="Message payload")
    meta: Optional[Dict[str, str]] = Field(default=None, description="Optional metadata")

    @classmethod
    def from_client(cls, raw: str) -> "WSMessage":
        try:
            data = json.loads(raw)
            return cls.parse_obj(data)
        except Exception:
            return cls(
                version=1,
                type=WSMessageType.ERROR,
                payload={"message": "Invalid JSON format"}
            )

    def to_json(self) -> str:
        return self.model_dump_json(exclude_none=True)

# Client message constructors
def ping() -> WSMessage:
    return WSMessage(type=WSMessageType.PING)

def get_unread(limit: int = 10) -> WSMessage:
    return WSMessage(
        type=WSMessageType.GET_UNREAD,
        payload={"limit": limit}
    )

def mark_read(notification_id: str) -> WSMessage:
    return WSMessage(
        type=WSMessageType.MARK_READ,
        payload={"notification_id": notification_id}
    )

# Server message constructors
def pong() -> WSMessage:
    return WSMessage(type=WSMessageType.PONG)

def unread_notifications(count: int, notifications: list) -> WSMessage:
    return WSMessage(
        type=WSMessageType.UNREAD_NOTIFICATIONS,
        payload={
            "count": count,
            "notifications": notifications
        }
    )

def new_notification(notification: dict) -> WSMessage:
    return WSMessage(
        type=WSMessageType.NEW_NOTIFICATION,
        payload={"notification": notification}
    )

def notification_updated(notification_id: str, success: bool) -> WSMessage:
    return WSMessage(
        type=WSMessageType.NOTIFICATION_UPDATED,
        payload={
            "notification_id": notification_id,
            "success": success
        }
    )

def error(message: str) -> WSMessage:
    return WSMessage(
        type=WSMessageType.ERROR,
        payload={"message": message}
    )
