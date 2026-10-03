# app/notifications/websocket.py
"""
WebSocket endpoint for real-time notifications
Handles bidirectional communication for notification management
"""

import json
import asyncio
from typing import Dict, Set, Optional
from uuid import UUID
import asyncpg
from fastapi import WebSocket, WebSocketDisconnect, Depends, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.config import settings
from app.core.logging import get_logger
from app.auth.dependencies import get_current_user_websocket
from app.notifications.service import NotificationService
from app.notifications.protocol import (
    WSMessageType, WSMessage, 
    ping, pong, unread_notifications, new_notification, 
    notification_updated, error
)

log = get_logger("notifications-websocket")
security = HTTPBearer()

class NotificationWebSocketManager:
    """Manages WebSocket connections for notifications"""
    
    def __init__(self):
        # Store active connections by user_id
        self.user_connections: Dict[str, Set[WebSocket]] = {}
        # Store connection metadata
        self.connection_metadata: Dict[WebSocket, Dict] = {}
    
    async def connect(self, websocket: WebSocket, user_id: str):
        """Accept and register WebSocket connection"""
        await websocket.accept()
        
        # Add to user connections
        if user_id not in self.user_connections:
            self.user_connections[user_id] = set()
        self.user_connections[user_id].add(websocket)
        
        # Store metadata
        self.connection_metadata[websocket] = {
            "user_id": user_id,
            "connected_at": asyncio.get_event_loop().time()
        }
        
        log.info(f"WebSocket connected for user {user_id}. Total connections: {len(self.connection_metadata)}")
        
        # Send initial unread notifications
        try:
            notifications = await NotificationService.get_user_notifications(
                user_id=user_id,
                unread_only=True,
                limit=50
            )
            await websocket.send_text(unread_notifications(len(notifications), notifications).to_json())
        except Exception as e:
            log.error(f"Failed to send initial notifications: {e}")
            await websocket.send_text(error("Failed to load notifications").to_json())
    
    def disconnect(self, websocket: WebSocket):
        """Remove WebSocket connection"""
        metadata = self.connection_metadata.get(websocket)
        if metadata:
            user_id = metadata["user_id"]
            
            # Remove from user connections
            if user_id in self.user_connections:
                self.user_connections[user_id].discard(websocket)
                if not self.user_connections[user_id]:
                    del self.user_connections[user_id]
            
            # Remove metadata
            del self.connection_metadata[websocket]
            
            log.info(f"WebSocket disconnected for user {user_id}. Remaining connections: {len(self.connection_metadata)}")
    
    async def send_to_user(self, user_id: str, message: WSMessage):
        """Send message to all connections for a specific user"""
        if user_id in self.user_connections:
            disconnected = []
            for websocket in self.user_connections[user_id].copy():
                try:
                    await websocket.send_text(message.to_json())
                except Exception as e:
                    log.error(f"Failed to send message to WebSocket: {e}")
                    disconnected.append(websocket)
            
            # Clean up disconnected connections
            for websocket in disconnected:
                self.disconnect(websocket)
    
    async def broadcast_to_all(self, message: WSMessage):
        """Broadcast message to all connected users"""
        for websocket in list(self.connection_metadata.keys()):
            try:
                await websocket.send_text(message.to_json())
            except Exception as e:
                log.error(f"Failed to broadcast to WebSocket: {e}")
                self.disconnect(websocket)
    
    async def handle_message(self, websocket: WebSocket, message_data: dict):
        """Handle incoming WebSocket message"""
        metadata = self.connection_metadata.get(websocket)
        if not metadata:
            await websocket.send_text(error("Connection not authenticated").to_json())
            return
        
        user_id = metadata["user_id"]
        message_type = message_data.get("type")
        payload = message_data.get("data", {})
        
        try:
            if message_type == WSMessageType.PING:
                await websocket.send_text(pong().to_json())
            
            elif message_type == WSMessageType.GET_UNREAD:
                limit = payload.get("limit", 20)
                notifications = await NotificationService.get_user_notifications(
                    user_id=user_id,
                    unread_only=True,
                    limit=limit
                )
                await websocket.send_text(unread_notifications(len(notifications), notifications).to_json())
            
            elif message_type == WSMessageType.MARK_READ:
                notification_id = payload.get("notification_id")
                if not notification_id:
                    await websocket.send_text(error("Missing notification_id").to_json())
                    return
                
                success = await NotificationService.mark_as_read(notification_id, user_id)
                if success:
                    # Send confirmation back to user
                    await websocket.send_text(notification_updated(notification_id, True).to_json())
                    # Update unread count
                    unread_count = await NotificationService.get_unread_count(user_id)
                    notifications = await NotificationService.get_user_notifications(
                        user_id=user_id,
                        unread_only=True,
                        limit=50
                    )
                    await self.send_to_user(user_id, unread_notifications(unread_count, notifications))
                else:
                    await websocket.send_text(error("Failed to mark notification as read").to_json())
            
            else:
                await websocket.send_text(error(f"Unknown message type: {message_type}").to_json())
        
        except Exception as e:
            log.error(f"Error handling WebSocket message: {e}")
            await websocket.send_text(error("Internal server error").to_json())
    
    async def notify_user(self, user_id: str, notification: dict):
        """Send new notification to specific user"""
        await self.send_to_user(user_id, new_notification(notification))
    
    async def notify_users(self, user_ids: list, notification: dict):
        """Send notification to multiple users"""
        for user_id in user_ids:
            await self.notify_user(user_id, notification)


class PostgresNotificationBridge:
    """Fan database-created notifications into each API worker's local sockets."""

    CHANNEL = "sentinelops_notification_created"

    def __init__(self) -> None:
        self._stop_event: asyncio.Event | None = None
        self._connection: asyncpg.Connection | None = None

    async def run_forever(self) -> None:
        self._stop_event = asyncio.Event()
        retry_seconds = 1
        while not self._stop_event.is_set():
            try:
                connection = await asyncpg.connect(dsn=settings.DATABASE_URL, command_timeout=30)
                self._connection = connection
                await connection.add_listener(self.CHANNEL, self._handle_notification)
                retry_seconds = 1
                log.info("PostgreSQL realtime notification bridge is listening on %s", self.CHANNEL)
                while not self._stop_event.is_set():
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=30)
                    except asyncio.TimeoutError:
                        await connection.fetchval("SELECT 1")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("PostgreSQL realtime notification bridge disconnected: %s", exc)
                if self._stop_event.is_set():
                    break
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=retry_seconds)
                except asyncio.TimeoutError:
                    pass
                retry_seconds = min(30, retry_seconds * 2)
            finally:
                connection = self._connection
                self._connection = None
                if connection is not None and not connection.is_closed():
                    try:
                        await connection.remove_listener(self.CHANNEL, self._handle_notification)
                    except Exception:
                        pass
                    try:
                        await connection.close()
                    except Exception as exc:
                        log.warning("Failed to close PostgreSQL notification bridge connection cleanly: %s", exc)

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()

    @staticmethod
    def _handle_notification(
        _connection: asyncpg.Connection,
        _process_id: int,
        _channel: str,
        raw_payload: str,
    ) -> None:
        try:
            payload = json.loads(raw_payload)
            user_id = str(payload.get("user_id") or "").strip()
            notification = payload.get("notification")
            if not user_id or not isinstance(notification, dict):
                raise ValueError("notification bridge payload is missing user_id or notification")
            asyncio.create_task(PostgresNotificationBridge._dispatch_notification(user_id, notification))
        except Exception as exc:
            log.warning("Ignored malformed PostgreSQL notification payload: %s", exc)

    @staticmethod
    async def _dispatch_notification(user_id: str, notification: dict) -> None:
        try:
            await ws_manager.notify_user(user_id, notification)
        except Exception as exc:
            log.warning("Failed to fan PostgreSQL notification to user %s: %s", user_id, exc)

# Global WebSocket manager instance
ws_manager = NotificationWebSocketManager()
postgres_notification_bridge = PostgresNotificationBridge()

# Helper function to send notifications from other parts of the app
async def send_notification_to_user(user_id: str, notification: dict):
    """Send notification to specific user via WebSocket"""
    await ws_manager.notify_user(user_id, notification)

async def send_notification_to_users(user_ids: list, notification: dict):
    """Send notification to multiple users via WebSocket"""
    await ws_manager.notify_users(user_ids, notification)
