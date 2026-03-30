# app/notifications/router.py
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Dict, Set, List, Optional
from uuid import UUID
import json

from app.auth.dependencies import get_current_user, get_current_user_websocket
from app.notifications.schemas import (
    NotificationResponse, NotificationUpdate, NotificationPreferences
)
from app.notifications.service import NotificationService
from app.notifications.websocket import ws_manager, send_notification_to_user, send_notification_to_users
from app.notifications.protocol import unread_notifications
from app.core.logging import get_logger
from app.core.error_models import ErrorResponse, ErrorCodes

log = get_logger("notifications-router")

router = APIRouter(prefix="/notifications", tags=["Notifications"])

@router.get("/", response_model=List[NotificationResponse])
async def get_notifications(
    unread_only: bool = Query(True, description="Show only unread notifications"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user)
):
    """Get notifications for current user"""
    try:
        notifications = await NotificationService.get_user_notifications(
            user_id=current_user["id"],
            unread_only=unread_only,
            limit=limit,
            offset=offset
        )
        return notifications
    except Exception as e:
        log.error(f"Error getting notifications: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/unread/count")
async def get_unread_count(
    current_user: dict = Depends(get_current_user)
):
    """Get count of unread notifications"""
    try:
        count = await NotificationService.get_unread_count(current_user["id"])
        return {"count": count}
    except Exception as e:
        log.error(f"Error counting unread notifications: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/{notification_id}/read")
async def mark_notification_read(
    notification_id: UUID,
    current_user: dict = Depends(get_current_user)
):
    """Mark a notification as read"""
    try:
        success = await NotificationService.mark_as_read(
            notification_id, current_user["id"]
        )
        
        if not success:
            raise HTTPException(status_code=404, 
                              detail="Notification not found or unauthorized")
        
        return {"message": "Notification marked as read"}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error marking notification as read: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/mark-all-read")
async def mark_all_notifications_read(
    current_user: dict = Depends(get_current_user)
):
    """Mark all notifications as read"""
    try:
        count = await NotificationService.mark_all_as_read(current_user["id"])
        await ws_manager.send_to_user(current_user["id"], unread_notifications(0, []))
        return {"message": f"Marked {count} notifications as read", "updated": count}
    except Exception as e:
        log.error(f"Error marking all notifications as read: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/preferences", response_model=NotificationPreferences)
async def get_notification_preferences(
    current_user: dict = Depends(get_current_user)
):
    """Get user's notification preferences"""
    # For now, return defaults
    # In production, this would read from a user_preferences table
    return NotificationPreferences()

@router.put("/preferences")
async def update_notification_preferences(
    preferences: NotificationPreferences,
    current_user: dict = Depends(get_current_user)
):
    """Update user's notification preferences"""
    # For now, just return the preferences
    # In production, this would save to a user_preferences table
    return {
        "message": "Preferences updated successfully",
        "preferences": preferences
    }

@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(..., description="Authentication token")
):
    """WebSocket endpoint for real-time notifications"""
    try:
        # Authenticate user from token
        user = await get_current_user_websocket(token)
        user_id = user["id"]
        
        # Connect to WebSocket manager
        await ws_manager.connect(websocket, user_id)
        
        try:
            # Handle messages
            while True:
                # Receive message
                data = await websocket.receive_text()
                try:
                    message_data = json.loads(data)
                    await ws_manager.handle_message(websocket, message_data)
                except json.JSONDecodeError:
                    log.error(f"Invalid JSON received: {data}")
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "data": {"message": "Invalid JSON format"}
                    }))
                except Exception as e:
                    log.error(f"Error processing message: {e}")
                    await websocket.send_text(json.dumps({
                        "type": "error", 
                        "data": {"message": "Error processing message"}
                    }))
        
        except WebSocketDisconnect:
            log.info(f"WebSocket disconnected for user {user_id}")
        except Exception as e:
            log.error(f"WebSocket error for user {user_id}: {e}")
        finally:
            # Clean up connection
            ws_manager.disconnect(websocket)
    
    except Exception as e:
        log.error(f"WebSocket connection failed: {e}")
        await websocket.close(code=4001, reason="Authentication failed")
