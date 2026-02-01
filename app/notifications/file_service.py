# app/notifications/file_service.py
"""
File-based notification service - eliminates database dependency
Stores notifications as JSON files on the local filesystem
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Any
from uuid import UUID, uuid4
from datetime import datetime, timedelta

from app.core.logging import get_logger

log = get_logger("notifications-file-service")

# Directory where notifications are stored
NOTIFICATIONS_DIR = Path(__file__).parent / "notifications"

def ensure_notifications_dir():
    """Ensure the notifications directory exists"""
    NOTIFICATIONS_DIR.mkdir(exist_ok=True)

def get_notification_file_path(user_id: str) -> Path:
    """Get the file path for a user's notifications"""
    ensure_notifications_dir()
    return NOTIFICATIONS_DIR / f"{user_id}_notifications.json"

def create_notification(
    user_id: str,
    title: str,
    message: str,
    related_entity: Optional[str] = None,
    related_id: Optional[str] = None,
    role_id: Optional[str] = None
) -> Dict[str, Any]:
    """Create a new notification"""
    notification = {
        'id': str(uuid4()),
        'user_id': user_id,
        'role_id': role_id,
        'title': title,
        'message': message,
        'related_entity': related_entity,
        'related_id': related_id,
        'is_read': False,
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat()
    }
    
    # Save notification
    save_notification(notification)
    return notification

def save_notification(notification: Dict[str, Any]) -> bool:
    """Save notification to file"""
    try:
        user_id = notification['user_id']
        file_path = get_notification_file_path(user_id)
        
        # Load existing notifications
        notifications = []
        if file_path.exists():
            with open(file_path, 'r', encoding='utf-8') as f:
                notifications = json.load(f)
        
        # Add new notification
        notifications.append(notification)
        
        # Keep only last 100 notifications per user
        notifications = notifications[-100:]
        
        # Save to file
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(notifications, f, indent=2, default=str)
        
        return True
    except Exception as e:
        log.error(f"Failed to save notification: {e}")
        return False

def get_user_notifications(
    user_id: str,
    unread_only: bool = False,
    limit: int = 50,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """Get notifications for a user"""
    try:
        file_path = get_notification_file_path(user_id)
        
        if not file_path.exists():
            return []
        
        with open(file_path, 'r', encoding='utf-8') as f:
            notifications = json.load(f)
        
        # Filter by read status if requested
        if unread_only:
            notifications = [n for n in notifications if not n.get('is_read', False)]
        
        # Sort by created_at descending
        notifications.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        # Apply pagination
        end_index = offset + limit
        paginated_notifications = notifications[offset:end_index]
        
        return paginated_notifications
    except Exception as e:
        log.error(f"Failed to get notifications for user {user_id}: {e}")
        return []

def mark_notification_as_read(notification_id: str, user_id: str) -> bool:
    """Mark a notification as read"""
    try:
        file_path = get_notification_file_path(user_id)
        
        if not file_path.exists():
            return False
        
        with open(file_path, 'r', encoding='utf-8') as f:
            notifications = json.load(f)
        
        # Find and update the notification
        updated = False
        for notification in notifications:
            if notification['id'] == notification_id:
                notification['is_read'] = True
                notification['updated_at'] = datetime.now().isoformat()
                updated = True
                break
        
        if updated:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(notifications, f, indent=2, default=str)
        
        return updated
    except Exception as e:
        log.error(f"Failed to mark notification as read: {e}")
        return False

def mark_all_notifications_as_read(user_id: str) -> int:
    """Mark all notifications as read for a user"""
    try:
        file_path = get_notification_file_path(user_id)
        
        if not file_path.exists():
            return 0
        
        with open(file_path, 'r', encoding='utf-8') as f:
            notifications = json.load(f)
        
        # Mark all as unread as read
        count = 0
        for notification in notifications:
            if not notification.get('is_read', False):
                notification['is_read'] = True
                notification['updated_at'] = datetime.now().isoformat()
                count += 1
        
        # Save updated notifications
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(notifications, f, indent=2, default=str)
        
        return count
    except Exception as e:
        log.error(f"Failed to mark all notifications as read: {e}")
        return 0

def get_unread_count(user_id: str) -> int:
    """Get count of unread notifications for a user"""
    try:
        notifications = get_user_notifications(user_id, unread_only=True)
        return len(notifications)
    except Exception as e:
        log.error(f"Failed to get unread count: {e}")
        return 0

def create_system_notification(
    title: str,
    message: str,
    related_entity: Optional[str] = None,
    related_id: Optional[str] = None
) -> None:
    """Create a system notification for all users"""
    # This would typically be called by the system to broadcast notifications
    # For now, we'll create it for a default system user
    create_notification(
        user_id="system",
        title=title,
        message=message,
        related_entity=related_entity,
        related_id=related_id
    )

def cleanup_old_notifications(days_old: int = 30) -> int:
    """Clean up notifications older than specified days"""
    try:
        cutoff_date = datetime.now() - timedelta(days=days_old)
        cleaned_count = 0
        
        if not NOTIFICATIONS_DIR.exists():
            return 0
        
        for file_path in NOTIFICATIONS_DIR.glob("*_notifications.json"):
            with open(file_path, 'r', encoding='utf-8') as f:
                notifications = json.load(f)
            
            # Filter out old notifications
            original_count = len(notifications)
            notifications = [
                n for n in notifications 
                if datetime.fromisoformat(n.get('created_at', '')) > cutoff_date
            ]
            
            # Save if any notifications were removed
            if len(notifications) < original_count:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(notifications, f, indent=2, default=str)
                cleaned_count += original_count - len(notifications)
        
        return cleaned_count
    except Exception as e:
        log.error(f"Failed to cleanup old notifications: {e}")
        return 0
