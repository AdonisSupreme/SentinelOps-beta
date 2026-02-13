# app/notifications/service.py
# UPDATED VERSION - DB-backed notification service
# Full replacement of file-based notification service

import asyncio
from typing import List, Optional
from uuid import UUID, uuid4
from datetime import datetime, timedelta

from app.notifications.db_service import NotificationDBService
from app.core.logging import get_logger

log = get_logger("notifications-service")

class NotificationService:
    """Notification management service - fully DB-backed"""
    
    @staticmethod
    async def get_user_notifications(
        user_id: str,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0
    ) -> List[dict]:
        """Get notifications for a user"""
        try:
            # Convert user_id string to UUID if needed
            user_uuid = UUID(user_id) if isinstance(user_id, str) else user_id
            
            notifications = NotificationDBService.get_user_notifications(
                user_id=user_uuid,
                unread_only=unread_only,
                limit=limit,
                offset=offset
            )
            
            # Format for frontend compatibility
            formatted_notifications = []
            for notification in notifications:
                formatted_notification = {
                    'id': notification['id'],
                    'user_id': notification['user_id'],
                    'role_id': notification.get('role_id'),
                    'title': notification['title'],
                    'message': notification['message'],
                    'related_entity': notification.get('related_entity'),
                    'related_id': notification.get('related_id'),
                    'is_read': notification.get('is_read', False),
                    'created_at': notification['created_at'],
                    'updated_at': notification.get('updated_at', notification['created_at'])
                }
                formatted_notifications.append(formatted_notification)
            
            log.info(f"Retrieved {len(formatted_notifications)} notifications for user {user_id}")
            return formatted_notifications
        
        except Exception as e:
            log.error(f"Failed to get notifications for user {user_id}: {e}")
            return []
    
    @staticmethod
    async def mark_as_read(notification_id: UUID, user_id: str) -> bool:
        """Mark a notification as read"""
        try:
            notification_uuid = notification_id if isinstance(notification_id, UUID) else UUID(notification_id)
            user_uuid = UUID(user_id) if isinstance(user_id, str) else user_id
            
            success = NotificationDBService.mark_as_read(notification_uuid, user_uuid)
            if success:
                log.info(f"Marked notification {notification_id} as read")
            return success
        
        except Exception as e:
            log.error(f"Failed to mark notification as read: {e}")
            return False
    
    @staticmethod
    async def mark_all_as_read(user_id: str) -> int:
        """Mark all notifications as read for a user"""
        try:
            user_uuid = UUID(user_id) if isinstance(user_id, str) else user_id
            
            count = NotificationDBService.mark_all_as_read(user_uuid)
            log.info(f"Marked {count} notifications as read for user {user_id}")
            return count
        
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
        """Create a new notification for a user"""
        try:
            user_uuid = UUID(user_id) if isinstance(user_id, str) else user_id
            related_uuid = UUID(related_id) if related_id and isinstance(related_id, str) else related_id
            
            notification = NotificationDBService.create_notification(
                title=title,
                message=message,
                user_id=user_uuid,
                related_entity=related_entity,
                related_id=related_uuid
            )
            
            log.info(f"Created notification for user {user_id}")
            return notification
        
        except Exception as e:
            log.error(f"Failed to create notification: {e}")
            return {}
    
    @staticmethod
    async def get_unread_count(user_id: str) -> int:
        """Get count of unread notifications for a user"""
        try:
            user_uuid = UUID(user_id) if isinstance(user_id, str) else user_id
            
            count = NotificationDBService.get_unread_count(user_uuid)
            return count
        
        except Exception as e:
            log.error(f"Failed to get unread count: {e}")
            return 0
    
    @staticmethod
    async def create_system_notification(
        title: str,
        message: str,
        related_entity: Optional[str] = None,
        related_id: Optional[str] = None
    ) -> dict:
        """Create a system-wide notification (notifies all admin and manager users)"""
        try:
            # This will notify all users with admin/manager roles
            notifications = NotificationDBService.notify_admin_and_managers(
                title=title,
                message=message,
                related_entity=related_entity,
                related_id=UUID(related_id) if related_id else None
            )
            
            log.info(f"Created system notification: {title} (notified {len(notifications)} roles)")
            return {
                "title": title,
                "message": message,
                "roles_notified": len(notifications),
                "status": "created"
            }
        except Exception as e:
            log.error(f"Failed to create system notification: {e}")
            return {}
    
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
            log.info(f"Created {count} bulk notifications")
            return count
        except Exception as e:
            log.error(f"Failed to create bulk notifications: {e}")
            return 0
    
    @staticmethod
    async def notify_admin_and_managers_item_skipped(
        item_id: str,
        item_title: str,
        instance_id: str,
        checklist_date: str,
        shift: str,
        skipped_reason: str
    ) -> List[dict]:
        """Notify admin and manager roles when item is skipped"""
        try:
            item_uuid = UUID(item_id) if isinstance(item_id, str) else item_id
            instance_uuid = UUID(instance_id) if isinstance(instance_id, str) else instance_id
            
            notifications = NotificationDBService.create_item_skipped_notification(
                item_id=item_uuid,
                item_title=item_title,
                instance_id=instance_uuid,
                checklist_date=checklist_date,
                shift=shift,
                skipped_reason=skipped_reason
            )
            
            log.info(f"Notified admins/managers of skipped item: {item_id}")
            return notifications
        
        except Exception as e:
            log.error(f"Failed to notify on item skip: {e}")
            return []
    
    @staticmethod
    async def notify_admin_and_managers_item_failed(
        item_id: str,
        item_title: str,
        instance_id: str,
        checklist_date: str,
        shift: str,
        failure_reason: str
    ) -> List[dict]:
        """Notify admin and manager roles when item fails (CRITICAL)"""
        try:
            item_uuid = UUID(item_id) if isinstance(item_id, str) else item_id
            instance_uuid = UUID(instance_id) if isinstance(instance_id, str) else instance_id
            
            notifications = NotificationDBService.create_item_failed_notification(
                item_id=item_uuid,
                item_title=item_title,
                instance_id=instance_uuid,
                checklist_date=checklist_date,
                shift=shift,
                failure_reason=failure_reason
            )
            
            log.info(f"CRITICAL: Notified admins/managers of failed item: {item_id}")
            return notifications
        
        except Exception as e:
            log.error(f"Failed to notify on item failure: {e}")
            return []
    
    @staticmethod
    async def notify_admin_and_managers_checklist_completed(
        instance_id: str,
        checklist_date: str,
        shift: str,
        completion_rate: float,
        completed_by_username: str
    ) -> List[dict]:
        """Notify admin and manager roles when checklist is completed"""
        try:
            instance_uuid = UUID(instance_id) if isinstance(instance_id, str) else instance_id
            
            notifications = NotificationDBService.create_checklist_completed_notification(
                instance_id=instance_uuid,
                checklist_date=checklist_date,
                shift=shift,
                completion_rate=completion_rate,
                completed_by_username=completed_by_username
            )
            
            log.info(f"Notified admins/managers of completed checklist: {instance_id}")
            return notifications
        
        except Exception as e:
            log.error(f"Failed to notify on checklist completion: {e}")
            return []
    
    @staticmethod
    async def notify_admin_and_managers_override(
        instance_id: str,
        override_reason: str,
        supervisor_username: str,
        checklist_date: str,
        shift: str
    ) -> List[dict]:
        """Notify admin and manager roles of supervisor override"""
        try:
            instance_uuid = UUID(instance_id) if isinstance(instance_id, str) else instance_id
            
            notifications = NotificationDBService.create_override_notification(
                instance_id=instance_uuid,
                override_reason=override_reason,
                supervisor_username=supervisor_username,
                checklist_date=checklist_date,
                shift=shift
            )
            
            log.info(f"Notified admins/managers of override: {instance_id}")
            return notifications
        
        except Exception as e:
            log.error(f"Failed to notify on override: {e}")
            return []