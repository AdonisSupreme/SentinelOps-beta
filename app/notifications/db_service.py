# app/notifications/db_service.py
"""
Database-backed Notification Service
- Stores all notifications in PostgreSQL
- Supports user and role-based targeting
- Auto-notifies admin/manager on critical events (skip/fail/escalation)
"""

from typing import List, Optional
from uuid import UUID, uuid4
from datetime import datetime, timezone
import json

from app.db.database import get_connection, get_async_connection
from app.core.logging import get_logger

log = get_logger("notifications-db-service")


class NotificationDBService:
    """Database-backed notification service"""
    
    @staticmethod
    def create_notification(
        title: str,
        message: str,
        user_id: Optional[UUID] = None,
        role_id: Optional[UUID] = None,
        related_entity: Optional[str] = None,
        related_id: Optional[UUID] = None
    ) -> dict:
        """
        Create a notification in the database.
        Either user_id or role_id must be provided.
        """
        if not user_id and not role_id:
            raise ValueError("Either user_id or role_id must be provided")
        
        notification_id = uuid4()
        
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO notifications (
                            id, user_id, role_id, title, message,
                            related_entity, related_id, is_read, created_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, FALSE, %s
                        ) RETURNING id, user_id, role_id, title, message,
                                  related_entity, related_id, is_read, created_at
                    """, (
                        notification_id, user_id, role_id, title, message,
                        related_entity, related_id, datetime.now(timezone.utc)
                    ))
                    
                    result = cur.fetchone()
                    conn.commit()
                    
                    if result:
                        log.info(f"✉️  Notification created: {notification_id}")
                        return {
                            'id': str(result[0]),
                            'user_id': str(result[1]) if result[1] else None,
                            'role_id': str(result[2]) if result[2] else None,
                            'title': result[3],
                            'message': result[4],
                            'related_entity': result[5],
                            'related_id': str(result[6]) if result[6] else None,
                            'is_read': result[7],
                            'created_at': result[8].isoformat() if result[8] else None
                        }
        except Exception as e:
            log.error(f"Failed to create notification: {e}")
            raise
    
    @staticmethod
    def notify_admin_and_managers(
        title: str,
        message: str,
        related_entity: Optional[str] = None,
        related_id: Optional[UUID] = None
    ) -> List[dict]:
        """
        Create notifications for all admin and manager users.
        Used for skip/fail/escalation events.
        """
        notifications = []
        
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Get admin and manager role IDs
                    cur.execute("""
                        SELECT id FROM roles WHERE name IN ('admin', 'manager')
                    """)
                    role_rows = cur.fetchall()
                    
                    if not role_rows:
                        log.warning("No admin/manager roles found in database")
                        return notifications
                    
                    for (role_id,) in role_rows:
                        try:
                            notification = NotificationDBService.create_notification(
                                title=title,
                                message=message,
                                role_id=UUID(role_id),
                                related_entity=related_entity,
                                related_id=related_id
                            )
                            notifications.append(notification)
                        except Exception as e:
                            log.error(f"Failed to notify role {role_id}: {e}")
                    
                    log.info(f"🔔 Notified {len(notifications)} admin/manager roles")
        
        except Exception as e:
            log.error(f"Failed to notify admin/managers: {e}")
            raise
        
        return notifications
    
    @staticmethod
    def get_user_notifications(
        user_id: UUID,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0
    ) -> List[dict]:
        """Get notifications for a specific user"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Get user-specific notifications
                    query = """
                        SELECT id, user_id, role_id, title, message, related_entity,
                               related_id, is_read, created_at
                        FROM notifications
                        WHERE (user_id = %s OR role_id IN (
                            SELECT role_id FROM user_roles WHERE user_id = %s
                        ))
                    """
                    
                    params = [user_id, user_id]
                    
                    if unread_only:
                        query += " AND is_read = FALSE"
                    
                    query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
                    params.extend([limit, offset])
                    
                    cur.execute(query, params)
                    rows = cur.fetchall()
                    
                    notifications = []
                    for row in rows:
                        notifications.append({
                            'id': str(row[0]),
                            'user_id': str(row[1]) if row[1] else None,
                            'role_id': str(row[2]) if row[2] else None,
                            'title': row[3],
                            'message': row[4],
                            'related_entity': row[5],
                            'related_id': str(row[6]) if row[6] else None,
                            'is_read': row[7],
                            'created_at': row[8].isoformat() if row[8] else None
                        })
                    
                    log.info(f"Retrieved {len(notifications)} notifications for user {user_id}")
                    return notifications
        
        except Exception as e:
            log.error(f"Failed to get notifications for user {user_id}: {e}")
            return []
    
    @staticmethod
    def mark_as_read(notification_id: UUID, user_id: UUID) -> bool:
        """Mark a notification as read (verify ownership first)"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Verify user owns this notification or has it via role
                    cur.execute("""
                        SELECT id FROM notifications
                        WHERE id = %s AND (
                            user_id = %s OR 
                            role_id IN (SELECT role_id FROM user_roles WHERE user_id = %s)
                        )
                    """, (notification_id, user_id, user_id))
                    
                    if not cur.fetchone():
                        log.warning(f"User {user_id} tried to mark unowned notification {notification_id}")
                        return False
                    
                    cur.execute("""
                        UPDATE notifications SET is_read = TRUE WHERE id = %s
                    """, (notification_id,))
                    
                    conn.commit()
                    log.info(f"Marked notification {notification_id} as read")
                    return True
        
        except Exception as e:
            log.error(f"Failed to mark notification as read: {e}")
            return False
    
    @staticmethod
    def mark_all_as_read(user_id: UUID) -> int:
        """Mark all notifications as read for a user"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE notifications SET is_read = TRUE
                        WHERE (user_id = %s OR role_id IN (
                            SELECT role_id FROM user_roles WHERE user_id = %s
                        )) AND is_read = FALSE
                        RETURNING id
                    """, (user_id, user_id))
                    
                    updated_count = len(cur.fetchall())
                    conn.commit()
                    
                    log.info(f"Marked {updated_count} notifications as read for user {user_id}")
                    return updated_count
        
        except Exception as e:
            log.error(f"Failed to mark all notifications as read: {e}")
            return 0
    
    @staticmethod
    def get_unread_count(user_id: UUID) -> int:
        """Get count of unread notifications for a user"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT COUNT(*) FROM notifications
                        WHERE (user_id = %s OR role_id IN (
                            SELECT role_id FROM user_roles WHERE user_id = %s
                        )) AND is_read = FALSE
                    """, (user_id, user_id))
                    
                    (count,) = cur.fetchone()
                    return count if count else 0
        
        except Exception as e:
            log.error(f"Failed to get unread count: {e}")
            return 0
    
    @staticmethod
    def create_item_skipped_notification(
        item_id: UUID,
        item_title: str,
        instance_id: UUID,
        checklist_date: str,
        shift: str,
        skipped_reason: str
    ) -> List[dict]:
        """
        Notify admin/manager when an item is skipped.
        High-signal event.
        """
        title = "⚠️ Checklist Item Skipped"
        message = (
            f"Item '{item_title}' was skipped on {checklist_date} ({shift} shift).\n"
            f"Reason: {skipped_reason}"
        )
        
        return NotificationDBService.notify_admin_and_managers(
            title=title,
            message=message,
            related_entity="checklist_item",
            related_id=item_id
        )
    
    @staticmethod
    def create_item_failed_notification(
        item_id: UUID,
        item_title: str,
        instance_id: UUID,
        checklist_date: str,
        shift: str,
        failure_reason: str
    ) -> List[dict]:
        """
        Notify admin/manager when an item fails (escalated).
        Critical issue event.
        """
        title = "🚨 CRITICAL: Checklist Item Escalated"
        message = (
            f"Item '{item_title}' FAILED on {checklist_date} ({shift} shift).\n"
            f"Issue: {failure_reason}\n"
            f"Requires immediate attention."
        )
        
        return NotificationDBService.notify_admin_and_managers(
            title=title,
            message=message,
            related_entity="checklist_item",
            related_id=item_id
        )
    
    @staticmethod
    def create_checklist_completed_notification(
        instance_id: UUID,
        checklist_date: str,
        shift: str,
        completion_rate: float,
        completed_by_username: str
    ) -> List[dict]:
        """
        Notify admin/manager when a checklist is completed.
        Success event.
        """
        title = f"✅ Checklist Completed - {shift} Shift ({completion_rate:.0f}%)"
        message = (
            f"Shift checklist for {checklist_date} ({shift}) "
            f"completed by {completed_by_username}.\n"
            f"Completion rate: {completion_rate:.1f}%"
        )
        
        return NotificationDBService.notify_admin_and_managers(
            title=title,
            message=message,
            related_entity="checklist_instance",
            related_id=instance_id
        )
    
    @staticmethod
    def create_override_notification(
        instance_id: UUID,
        override_reason: str,
        supervisor_username: str,
        checklist_date: str,
        shift: str
    ) -> List[dict]:
        """
        Notify team when a supervisor overrides checklist.
        Compliance event.
        """
        title = f"🔒 Checklist Override - {shift} Shift"
        message = (
            f"Supervisor {supervisor_username} overrode checklist for {checklist_date} ({shift}).\n"
            f"Reason: {override_reason}"
        )
        
        return NotificationDBService.notify_admin_and_managers(
            title=title,
            message=message,
            related_entity="checklist_override",
            related_id=instance_id
        )
