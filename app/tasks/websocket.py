"""
WebSocket endpoint support for real-time task updates.
"""

import asyncio
import json
from typing import Any, Dict, Set

from fastapi import WebSocket, WebSocketDisconnect

from app.core.logging import get_logger

log = get_logger("tasks-websocket")


class TaskWebSocketManager:
    """Manage per-user task WebSocket connections."""

    def __init__(self) -> None:
        self.user_connections: Dict[str, Set[WebSocket]] = {}
        self.connection_metadata: Dict[WebSocket, Dict[str, Any]] = {}

    async def connect(self, websocket: WebSocket, user: Dict[str, Any]) -> None:
        await websocket.accept()

        user_id = str(user.get("id") or "").strip()
        if not user_id:
            await websocket.close(code=1008, reason="Unauthorized")
            return

        if user_id not in self.user_connections:
            self.user_connections[user_id] = set()
        self.user_connections[user_id].add(websocket)
        self.connection_metadata[websocket] = {
            "user_id": user_id,
            "username": user.get("username"),
            "connected_at": asyncio.get_running_loop().time(),
        }

        log.info(
            "Task WebSocket connected for user %s. Total connections: %s",
            user_id,
            len(self.connection_metadata),
        )

    def disconnect(self, websocket: WebSocket) -> None:
        metadata = self.connection_metadata.pop(websocket, None)
        if not metadata:
            return

        user_id = metadata.get("user_id")
        if user_id and user_id in self.user_connections:
            self.user_connections[user_id].discard(websocket)
            if not self.user_connections[user_id]:
                del self.user_connections[user_id]

        log.info(
            "Task WebSocket disconnected for user %s. Remaining connections: %s",
            user_id,
            len(self.connection_metadata),
        )

    async def send_to_user(self, user_id: str, message: Dict[str, Any]) -> None:
        connections = self.user_connections.get(str(user_id), set()).copy()
        if not connections:
            return

        payload = json.dumps(message)
        disconnected: list[WebSocket] = []

        for websocket in connections:
            try:
                await websocket.send_text(payload)
            except Exception as exc:
                log.warning("Failed to send task realtime message to user %s: %s", user_id, exc)
                disconnected.append(websocket)

        for websocket in disconnected:
            self.disconnect(websocket)

    async def send_to_users(self, user_ids: list[str], message: Dict[str, Any]) -> None:
        for user_id in {str(uid) for uid in user_ids if uid}:
            await self.send_to_user(user_id, message)

    async def handle_message(self, websocket: WebSocket, raw_message: str) -> None:
        try:
            payload = json.loads(raw_message) if raw_message else {}
        except json.JSONDecodeError:
            await self._safe_send(
                websocket,
                {
                    "type": "ERROR",
                    "data": {"message": "Invalid task websocket payload"},
                },
            )
            return

        message_type = str(payload.get("type") or "").upper()
        if message_type == "PING":
            await self._safe_send(
                websocket,
                {
                    "type": "PONG",
                    "data": {},
                },
            )

    async def _safe_send(self, websocket: WebSocket, message: Dict[str, Any]) -> None:
        try:
            await websocket.send_text(json.dumps(message))
        except (WebSocketDisconnect, RuntimeError):
            self.disconnect(websocket)
        except Exception as exc:
            log.warning("Failed to send direct task websocket message: %s", exc)
            self.disconnect(websocket)


task_ws_manager = TaskWebSocketManager()

