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
    
    async def connect(self, websocket: WebSocket, user_id: str = None):
        """Accept WebSocket connection and add to connection pool"""
        try:
            await websocket.accept()
            self.connections.add(websocket)
            
            if user_id:
                if user_id not in self.user_connections:
                    self.user_connections[user_id] = set()
                self.user_connections[user_id].add(websocket)
            
            log.info(f"WebSocket connected. Total connections: {len(self.connections)}")
            
            # Send welcome message
            await websocket.send_text(json.dumps({
                'type': 'CONNECTION_ESTABLISHED',
                'data': {
                    'user_id': user_id,
                    'timestamp': datetime.now().isoformat(),
                    'total_connections': len(self.connections)
                }
            }))
            
        except Exception as e:
            log.error(f"Error accepting WebSocket connection: {e}")
            raise
    
    async def disconnect(self, websocket: WebSocket):
        """Remove WebSocket connection from pool"""
        try:
            self.connections.discard(websocket)
            
            # Remove from user connections
            for user_id, connections in self.user_connections.items():
                connections.discard(websocket)
                if not connections:
                    del self.user_connections[user_id]
            
            log.info(f"WebSocket disconnected. Total connections: {len(self.connections)}")
        except Exception as e:
            log.error(f"Error disconnecting WebSocket: {e}")
    
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
        await self.broadcast_instance_update('ITEM_UPDATED', {
            'item_id': item_id,
            'status': status,
            'previous_status': previous_status,
            'user_id': user_id
        })
    
    async def broadcast_instance_joined(self, instance_id: str, user_id: str):
        """Broadcast when user joins checklist instance"""
        await self.broadcast_instance_update('INSTANCE_JOINED', {
            'user_id': user_id
        })
    
    async def broadcast_instance_created(self, instance_id: str, user_id: str = None):
        """Broadcast when new checklist instance is created"""
        await self.broadcast_instance_update('INSTANCE_CREATED', {
            'user_id': user_id
        })
    
    async def _safe_send(self, websocket: WebSocket, message: str):
        """Safely send message to WebSocket with error handling"""
        try:
            await websocket.send_text(message)
        except Exception as e:
            log.warning(f"Failed to send message to WebSocket: {e}")
            # Remove dead connection
            self.connections.discard(websocket)
            
            # Remove from user connections
            for uid, connections in self.user_connections.items():
                connections.discard(websocket)
                if not connections:
                    del self.user_connections[uid]
    
    def get_connection_count(self) -> int:
        """Get total number of active connections"""
        return len(self.connections)
    
    def get_user_connection_count(self, user_id: str) -> int:
        """Get number of connections for specific user"""
        return len(self.user_connections.get(user_id, set()))

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
