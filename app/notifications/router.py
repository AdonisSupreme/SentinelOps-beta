# app/notifications/router.py
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from typing import List, Optional
from uuid import UUID
import json

from app.auth.service import get_current_user, get_user_from_token, AuthenticationError
from app.notifications.schemas import (
    NotificationResponse, NotificationUpdate, NotificationPreferences
)
from app.notifications.service import NotificationService
from app.notifications.websocket import manager, handle_websocket_message
from app.core.logging import get_logger

log = get_logger("notifications-router")

router = APIRouter(prefix="/notifications", tags=["Notifications"])

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = None):
    """
    WebSocket endpoint for real-time notifications.
    
    Connection:
        ws://localhost:8000/api/v1/notifications/ws?token=<JWT>
    
    Message types:
        - ping: Connection keep-alive, server responds with pong
        - get_unread: Request list of unread notifications
        - mark_read: Mark a notification as read
    
    Server messages:
        - pong: Response to ping
        - unread_notifications: List of unread notifications
        - new_notification: Newly created notification
        - notification_updated: Notification status changed
        - error: Error message
    """
    # Authenticate the token
    if not token:
        await websocket.close(code=4001, reason="Missing authentication token")
        return

    try:
        user = get_user_from_token(token)
        user_id = user["id"]
    except AuthenticationError as e:
        await websocket.close(code=4001, reason=f"Authentication failed: {str(e)}")
        return
    except Exception as e:
        log.error(f"WebSocket authentication error: {e}")
        await websocket.close(code=4000, reason="Authentication error")
        return

    # Register connection
    await manager.connect(websocket, user_id)

    try:
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            
            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "message": "Invalid JSON format"
                })
                continue
            
            # Handle message
            await handle_websocket_message(
                websocket, user_id, message, NotificationService
            )
                
    except WebSocketDisconnect:
        manager.disconnect(user_id, websocket)
    except Exception as e:
        log.error(f"WebSocket error for user {user_id}: {e}")
        manager.disconnect(user_id, websocket)

router = APIRouter(prefix="/notifications", tags=["Notifications"])

@router.get("/", response_model=List[NotificationResponse])
async def get_notifications(
    unread_only: bool = Query(False, description="Show only unread notifications"),
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
        notifications = await NotificationService.get_user_notifications(
            user_id=current_user["id"],
            unread_only=True,
            limit=1000  # Large limit to count all
        )
        return {"count": len(notifications)}
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
        return {"message": f"Marked {count} notifications as read"}
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