# app/services/websocket.py
"""
WebSocket manager for real-time checklist updates
Handles broadcasting checklist updates to connected clients
"""

import json
import asyncio
from typing import Dict, Any, Set
from datetime import datetime
from fastapi import WebSocket, WebSocketDisconnect
from app.db.database import get_connection

# Simple fallback logger to avoid dependency issues
class SimpleLogger:
    def __init__(self, name):
        self.name = name
    def debug(self, msg): print(f"DEBUG: {msg}")
    def info(self, msg): print(f"INFO: {msg}")
    def warning(self, msg): print(f"WARNING: {msg}")
    def error(self, msg): print(f"ERROR: {msg}")

log = SimpleLogger("websocket-manager")

class WebSocketManager:
    """Manages WebSocket connections and broadcasts real-time updates"""
    
    def __init__(self):
        self.connections: Set[WebSocket] = set()
        self.user_connections: Dict[str, Set[WebSocket]] = {}  # user_id -> connections
        self.connection_limit_per_user = 3  # Limit connections per user to prevent connection storms
    
    async def connect(self, websocket: WebSocket, user_id: str = None):
        """Add WebSocket connection to pool (connection already accepted)"""
        try:
            # Check connection limit per user
            if user_id and user_id in self.user_connections:
                existing_connections = len(self.user_connections[user_id])
                if existing_connections >= self.connection_limit_per_user:
                    log.warning(f"User {user_id} already has {existing_connections} connections, rejecting new connection")
                    await websocket.close(code=1008, reason="Too many connections")
                    return
            
            self.connections.add(websocket)
            # IMPORTANT: Do not mutate Starlette/FastAPI WebSocket internal state
            # (e.g. websocket.client_state / websocket.application_state). Track liveness
            # by sending and catching exceptions instead.
            
            if user_id:
                if user_id not in self.user_connections:
                    self.user_connections[user_id] = set()
                self.user_connections[user_id].add(websocket)
                
                log.info(f"User {user_id} now has {len(self.user_connections[user_id])} connections")
                if len(self.user_connections[user_id]) == 1:
                    await self.broadcast_presence_update(user_id, True)
            
            log.info(f"WebSocket connected. Total connections: {len(self.connections)}")
            
        except Exception as e:
            log.error(f"Error adding WebSocket connection: {e}")
            raise

    async def send_welcome_message(self, websocket: WebSocket, user_id: str = None):
        """Send welcome message after connection is established"""
        try:
            log.info(f"Sending welcome message to WebSocket")
            await self._safe_send(websocket, json.dumps({
                'type': 'CONNECTION_ESTABLISHED',
                'data': {
                    'user_id': user_id,
                    'timestamp': datetime.now().isoformat(),
                    'total_connections': len(self.connections)
                }
            }))
            log.info(f"Welcome message sent successfully")
        except Exception as e:
            log.error(f"Error sending welcome message: {e}")
    
    async def disconnect(self, websocket: WebSocket):
        """Remove WebSocket connection from pool"""
        try:
            disconnected_user_id = None
            should_broadcast_offline = False

            # Remove from general connections first
            if websocket in self.connections:
                self.connections.discard(websocket)
            
            # Remove from user connections - create a copy to avoid iteration errors
            user_connections_copy = dict(self.user_connections)
            for user_id, connections in user_connections_copy.items():
                if websocket in connections:
                    connections.discard(websocket)
                    disconnected_user_id = user_id
                    # Clean up empty connection sets
                    if not connections:
                        self.user_connections.pop(user_id, None)
                        should_broadcast_offline = True
                    break
            
            log.info(f"WebSocket disconnected. Total connections: {len(self.connections)}")
            if disconnected_user_id and should_broadcast_offline:
                await self.broadcast_presence_update(disconnected_user_id, False)
            
        except Exception as e:
            log.error(f"Error disconnecting WebSocket: {e}")
            # Continue cleanup even if there's an error
            try:
                self.connections.discard(websocket)
            except:
                pass
    
    async def broadcast_checklist_update(self, data: Dict[str, Any]):
        """Broadcast checklist update to all connected clients"""
        if not self.connections:
            return
        
        message = json.dumps({
            'type': 'CHECKLIST_UPDATE',
            'data': {
                **data,
                'timestamp': datetime.now().isoformat()
            }
        })
        
        log.info(f"Broadcasting checklist update to {len(self.connections)} connections: {data.get('type', 'UNKNOWN')}")
        
        # Send to all connections
        await asyncio.gather(
            *[self._safe_send(ws, message) for ws in self.connections],
            return_exceptions=True
        )
    
    async def send_to_user(self, user_id: str, data: Dict[str, Any]):
        """Send message to specific user's connections"""
        if user_id not in self.user_connections:
            return
        
        message = json.dumps({
            'type': 'USER_UPDATE',
            'data': {
                **data,
                'timestamp': datetime.now().isoformat()
            }
        })
        
        connections = self.user_connections[user_id]
        await asyncio.gather(
            *[self._safe_send(ws, message) for ws in connections],
            return_exceptions=True
        )
    
    async def broadcast_instance_update(self, instance_id: str, update_type: str, data: Dict[str, Any] = None):
        """Broadcast specific instance update"""
        broadcast_data = {
            'type': update_type,
            'instance_id': instance_id,
            **(data or {})
        }
        
        await self.broadcast_checklist_update(broadcast_data)
    
    async def broadcast_item_update(
        self, 
        instance_id: str, 
        item_id: str, 
        status: str, 
        user_id: str = None,
        previous_status: str = None
    ):
        """Broadcast item status update"""
        await self.broadcast_instance_update(
            instance_id,
            'ITEM_UPDATED',
            {
                'item_id': item_id,
                'status': status,
                'previous_status': previous_status,
                'user_id': user_id,
            },
        )
    
    async def broadcast_instance_joined(self, instance_id: str, user_id: str):
        """Broadcast when user joins checklist instance"""
        await self.broadcast_instance_update(
          instance_id,
          'INSTANCE_JOINED',
          {
              'user_id': user_id,
          },
        )
    
    async def broadcast_instance_created(self, instance_id: str, user_id: str = None):
        """Broadcast when new checklist instance is created"""
        await self.broadcast_instance_update(
          instance_id,
          'INSTANCE_CREATED',
          {
              'user_id': user_id,
          },
        )

    async def broadcast_presence_update(self, user_id: str, is_online: bool):
        """Broadcast participant presence changes to any checklist instances the user belongs to."""
        instance_ids = self._get_user_instance_ids(user_id)
        if not instance_ids:
            return

        await asyncio.gather(
            *[
                self.broadcast_instance_update(
                    instance_id,
                    'PARTICIPANT_PRESENCE_CHANGED',
                    {
                        'user_id': user_id,
                        'is_online': is_online,
                    },
                )
                for instance_id in instance_ids
            ],
            return_exceptions=True
        )
    
    async def _safe_send(self, websocket: WebSocket, message: str):
        """Safely send message to WebSocket with error handling"""
        try:
            await websocket.send_text(message)
        except (WebSocketDisconnect, RuntimeError) as e:
            # Normal-ish failure modes when a client disconnects mid-send.
            log.warning(f"WebSocket send failed (disconnected): {e}")
            await self.disconnect(websocket)
        except Exception as e:
            log.warning(f"Failed to send message to WebSocket: {e}")
            await self.disconnect(websocket)
    
    def get_connection_count(self) -> int:
        """Get total number of active connections"""
        return len(self.connections)
    
    def get_user_connection_count(self, user_id: str) -> int:
        """Get number of connections for specific user"""
        return len(self.user_connections.get(user_id, set()))

    def _get_user_instance_ids(self, user_id: str) -> Set[str]:
        """Return checklist instances this user participates in."""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT DISTINCT instance_id
                        FROM checklist_participants
                        WHERE user_id = %s
                        """,
                        (user_id,)
                    )
                    return {str(row[0]) for row in cur.fetchall()}
        except Exception as e:
            log.warning(f"Failed to resolve presence instance subscriptions for user {user_id}: {e}")
            return set()

# Global WebSocket manager instance
websocket_manager = WebSocketManager()

# Convenience functions for broadcasting
async def broadcast_checklist_update(data: Dict[str, Any]):
    """Broadcast checklist update to all connected clients"""
    await websocket_manager.broadcast_checklist_update(data)

async def broadcast_item_update(
    instance_id: str, 
    item_id: str, 
    status: str, 
    user_id: str = None,
    previous_status: str = None
):
    """Broadcast item status update"""
    await websocket_manager.broadcast_item_update(instance_id, item_id, status, user_id, previous_status)

async def broadcast_instance_joined(instance_id: str, user_id: str):
    """Broadcast when user joins checklist instance"""
    await websocket_manager.broadcast_instance_joined(instance_id, user_id)

async def broadcast_instance_created(instance_id: str, user_id: str = None):
    """Broadcast when new checklist instance is created"""
    await websocket_manager.broadcast_instance_created(instance_id, user_id)
