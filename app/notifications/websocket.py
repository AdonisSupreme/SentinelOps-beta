# app/notifications/websocket.py
"""
WebSocket connection manager for real-time notifications.
Handles connection lifecycle, authentication, and message broadcasting.
"""
from typing import Dict, Set
from fastapi import WebSocket, WebSocketDisconnect
from app.core.logging import get_logger
import json
import asyncio

log = get_logger("websocket")


class ConnectionManager:
    """
    Manages WebSocket connections per user.
    Supports:
    - Multiple connections per user (browser tabs)
    - Broadcasting messages to specific users
    - Connection lifecycle management
    - Graceful error handling
    """

    def __init__(self):
        # user_id -> set of WebSocket connections
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        # user_id -> connection metadata
        self.connection_metadata: Dict[str, dict] = {}

    async def connect(self, websocket: WebSocket, user_id: str):
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        
        if user_id not in self.active_connections:
            self.active_connections[user_id] = set()
        
        self.active_connections[user_id].add(websocket)
        self.connection_metadata[user_id] = {
            "connected_at": asyncio.get_event_loop().time(),
            "connection_count": len(self.active_connections[user_id])
        }
        
        log.info(
            f"✅ WebSocket connected for user {user_id} "
            f"(Total connections: {len(self.active_connections[user_id])})"
        )

    def disconnect(self, user_id: str, websocket: WebSocket):
        """Remove a WebSocket connection from active connections."""
        if user_id in self.active_connections:
            self.active_connections[user_id].discard(websocket)
            
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]
                if user_id in self.connection_metadata:
                    del self.connection_metadata[user_id]
                log.info(f"❌ All WebSocket connections closed for user {user_id}")
            else:
                self.connection_metadata[user_id]["connection_count"] = len(
                    self.active_connections[user_id]
                )
                log.info(
                    f"❌ WebSocket disconnected for user {user_id} "
                    f"(Remaining: {len(self.active_connections[user_id])})"
                )

    async def broadcast_to_user(
        self,
        user_id: str,
        message: dict,
        exclude_websocket: WebSocket = None
    ):
        """
        Send a message to all connections of a specific user.
        
        Args:
            user_id: Target user ID
            message: Message dict to send (will be JSON-encoded)
            exclude_websocket: Optional WebSocket to exclude from broadcast
        """
        if user_id not in self.active_connections:
            log.debug(f"No active connections for user {user_id}")
            return

        disconnected = set()
        
        for connection in self.active_connections[user_id]:
            if exclude_websocket and connection == exclude_websocket:
                continue
            
            try:
                await connection.send_json(message)
            except Exception as e:
                log.warning(f"Error sending message to {user_id}: {e}")
                disconnected.add(connection)
        
        # Clean up disconnected connections
        for connection in disconnected:
            self.disconnect(user_id, connection)

    async def broadcast_to_multiple_users(
        self,
        user_ids: list,
        message: dict
    ):
        """Send a message to multiple users."""
        for user_id in user_ids:
            await self.broadcast_to_user(user_id, message)

    def get_connection_count(self, user_id: str) -> int:
        """Get number of active connections for a user."""
        return len(self.active_connections.get(user_id, set()))

    def get_active_users(self) -> list:
        """Get list of user IDs with active connections."""
        return list(self.active_connections.keys())


# Global manager instance
manager = ConnectionManager()


async def handle_websocket_message(
    websocket: WebSocket,
    user_id: str,
    message: dict,
    notification_service
):
    """
    Handle incoming WebSocket messages.
    
    Message types:
    - ping: Connection keep-alive
    - get_unread: Request unread notifications
    - mark_read: Mark notification as read
    """
    msg_type = message.get("type")
    
    if msg_type == "ping":
        await websocket.send_json({"type": "pong"})
    
    elif msg_type == "get_unread":
        try:
            notifications = await notification_service.get_user_notifications(
                user_id=user_id,
                unread_only=True,
                limit=message.get("limit", 10)
            )
            await websocket.send_json({
                "type": "unread_notifications",
                "count": len(notifications),
                "notifications": notifications
            })
        except Exception as e:
            log.error(f"Error fetching unread notifications: {e}")
            await websocket.send_json({
                "type": "error",
                "message": "Failed to fetch notifications",
                "details": str(e)
            })
    
    elif msg_type == "mark_read":
        try:
            notification_id = message.get("notification_id")
            success = await notification_service.mark_as_read(
                notification_id, user_id
            )
            await websocket.send_json({
                "type": "notification_updated",
                "notification_id": notification_id,
                "success": success
            })
        except Exception as e:
            log.error(f"Error marking notification as read: {e}")
            await websocket.send_json({
                "type": "error",
                "message": "Failed to update notification"
            })
    
    else:
        log.warning(f"Unknown WebSocket message type: {msg_type}")
        await websocket.send_json({
            "type": "error",
            "message": f"Unknown message type: {msg_type}"
        })


async def send_notification_to_user(
    user_id: str,
    notification: dict
):
    """
    Send a notification to a user via WebSocket.
    Called when a new notification is created.
    """
    message = {
        "type": "new_notification",
        "notification": notification
    }
    await manager.broadcast_to_user(user_id, message)
