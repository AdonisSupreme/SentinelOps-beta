# app/notifications/service.py
import asyncio
from typing import List, Optional
from uuid import UUID
from datetime import datetime, timedelta

from app.notifications.file_service import (
    get_user_notifications, mark_notification_as_read, mark_all_notifications_as_read,
    get_unread_count, create_notification, create_system_notification
)
from app.core.logging import get_logger

log = get_logger("notifications-service")

class NotificationService:
    """Notification management service - file-based implementation"""
    
    @staticmethod
    async def get_user_notifications(
        user_id: str,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0
    ) -> List[dict]:
        """Get notifications for a user"""
        try:
            notifications = get_user_notifications(
                user_id=user_id,
                unread_only=unread_only,
                limit=limit,
                offset=offset
            )
            
            # Format notifications to match expected response format
            formatted_notifications = []
            for notification in notifications:
                formatted_notification = {
                    'id': notification['id'],
                    'user_id': notification['user_id'],
                    'title': notification['title'],
                    'message': notification['message'],
                    'related_entity': notification.get('related_entity'),
                    'related_id': notification.get('related_id'),
                    'is_read': notification.get('is_read', False),
                    'created_at': notification['created_at'],
                    'updated_at': notification.get('updated_at', notification['created_at'])
                }
                formatted_notifications.append(formatted_notification)
            
            return formatted_notifications
        except Exception as e:
            log.error(f"Failed to get notifications for user {user_id}: {e}")
            return []
    
    @staticmethod
    async def mark_as_read(notification_id: UUID, user_id: str) -> bool:
        """Mark a notification as read"""
        try:
            return mark_notification_as_read(
                notification_id=str(notification_id),
                user_id=user_id
            )
        except Exception as e:
            log.error(f"Failed to mark notification as read: {e}")
            return False
    
    @staticmethod
    async def mark_all_as_read(user_id: str) -> int:
        """Mark all notifications as read for a user"""
        try:
            return mark_all_notifications_as_read(user_id=user_id)
        except Exception as e:
            log.error(f"Failed to mark all notifications as read: {e}")
            return 0
    
    @staticmethod
    async def create_notification(
        user_id: str,
        title: str,
        message: str,
        related_entity: Optional[str] = None,
        related_id: Optional[str] = None
    ) -> dict:
        """Create a new notification"""
        try:
            return create_notification(
                user_id=user_id,
                title=title,
                message=message,
                related_entity=related_entity,
                related_id=related_id
            )
        except Exception as e:
            log.error(f"Failed to create notification: {e}")
            return {}
    
    @staticmethod
    async def get_unread_count(user_id: str) -> int:
        """Get count of unread notifications for a user"""
        try:
            return get_unread_count(user_id=user_id)
        except Exception as e:
            log.error(f"Failed to get unread count: {e}")
            return 0
    
    @staticmethod
    async def create_system_notification(
        title: str,
        message: str,
        related_entity: Optional[str] = None,
        related_id: Optional[str] = None
    ) -> None:
        """Create a system notification"""
        try:
            create_system_notification(
                title=title,
                message=message,
                related_entity=related_entity,
                related_id=related_id
            )
        except Exception as e:
            log.error(f"Failed to create system notification: {e}")
    
    @staticmethod
    async def create_bulk_notifications(
        notifications: List[dict]
    ) -> int:
        """Create multiple notifications efficiently"""
        try:
            count = 0
            for notification in notifications:
                await NotificationService.create_notification(
                    user_id=notification['user_id'],
                    title=notification['title'],
                    message=notification['message'],
                    related_entity=notification.get('related_entity'),
                    related_id=notification.get('related_id')
                )
                count += 1
            return count
        except Exception as e:
            log.error(f"Failed to create bulk notifications: {e}")
            return 0
    
    @staticmethod
    async def cleanup_old_notifications(days: int = 30) -> int:
        """Clean up notifications older than specified days"""
        try:
            from app.notifications.file_service import cleanup_old_notifications
            return cleanup_old_notifications(days_old=days)
        except Exception as e:
            log.error(f"Failed to cleanup old notifications: {e}")
            return 0