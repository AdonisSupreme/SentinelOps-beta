# app/notifications/service.py
import asyncio
from typing import List, Optional
from uuid import UUID
from datetime import datetime, timedelta

from app.db.database import get_connection
from app.core.logging import get_logger

log = get_logger("notifications-service")

class NotificationService:
    """Notification management service"""
    
    @staticmethod
    async def get_user_notifications(
        user_id: UUID,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0
    ) -> List[dict]:
        """Get notifications for a user"""
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                query = """
                    SELECT 
                        n.*,
                        u.username as from_username,
                        r.name as from_role
                    FROM notifications n
                    LEFT JOIN users u ON n.user_id = u.id
                    LEFT JOIN roles r ON n.role_id = r.id
                    WHERE (n.user_id = %s OR n.role_id IN (
                        SELECT role_id FROM user_roles WHERE user_id = %s
                    ))
                    {unread_filter}
                    ORDER BY n.created_at DESC
                    LIMIT %s OFFSET %s
                """
                
                if unread_only:
                    query = query.format(unread_filter="AND n.is_read = FALSE")
                else:
                    query = query.format(unread_filter="")
                
                await cur.execute(query, [user_id, user_id, limit, offset])
                rows = await cur.fetchall()
                
                return [
                    {
                        'id': row[0],
                        'user_id': row[1],
                        'role_id': row[2],
                        'title': row[3],
                        'message': row[4],
                        'related_entity': row[5],
                        'related_id': row[6],
                        'is_read': row[7],
                        'created_at': row[8],
                        'from_username': row[9],
                        'from_role': row[10]
                    }
                    for row in rows
                ]
    
    @staticmethod
    async def mark_as_read(
        notification_id: UUID,
        user_id: UUID
    ) -> bool:
        """Mark a notification as read"""
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE notifications 
                    SET is_read = TRUE 
                    WHERE id = %s AND (user_id = %s OR role_id IN (
                        SELECT role_id FROM user_roles WHERE user_id = %s
                    ))
                    RETURNING id
                """, [notification_id, user_id, user_id])
                
                return await cur.fetchone() is not None
    
    @staticmethod
    async def mark_all_as_read(user_id: UUID) -> int:
        """Mark all user notifications as read"""
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE notifications 
                    SET is_read = TRUE 
                    WHERE (user_id = %s OR role_id IN (
                        SELECT role_id FROM user_roles WHERE user_id = %s
                    )) AND is_read = FALSE
                    RETURNING COUNT(*)
                """, [user_id, user_id])
                
                result = await cur.fetchone()
                return result[0] if result else 0
    
    @staticmethod
    async def create_notification(
        title: str,
        message: str,
        user_id: Optional[UUID] = None,
        role_id: Optional[UUID] = None,
        related_entity: Optional[str] = None,
        related_id: Optional[UUID] = None
    ) -> dict:
        """Create a new notification"""
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO notifications 
                    (user_id, role_id, title, message, related_entity, related_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING *
                """, [user_id, role_id, title, message, related_entity, related_id])
                
                row = await cur.fetchone()
                
                return {
                    'id': row[0],
                    'user_id': row[1],
                    'role_id': row[2],
                    'title': row[3],
                    'message': row[4],
                    'related_entity': row[5],
                    'related_id': row[6],
                    'is_read': row[7],
                    'created_at': row[8]
                }
    
    @staticmethod
    async def create_bulk_notifications(
        notifications: List[dict]
    ) -> int:
        """Create multiple notifications efficiently"""
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                values = []
                for n in notifications:
                    values.append((
                        n.get('user_id'),
                        n.get('role_id'),
                        n['title'],
                        n['message'],
                        n.get('related_entity'),
                        n.get('related_id')
                    ))
                
                await cur.executemany("""
                    INSERT INTO notifications 
                    (user_id, role_id, title, message, related_entity, related_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, values)
                
                return len(notifications)
    
    @staticmethod
    async def cleanup_old_notifications(days: int = 30):
        """Clean up notifications older than specified days"""
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                cutoff_date = datetime.now() - timedelta(days=days)
                await cur.execute("""
                    DELETE FROM notifications 
                    WHERE created_at < %s AND is_read = TRUE
                """, [cutoff_date])
                
                deleted = cur.rowcount
                log.info(f"Cleaned up {deleted} old notifications")
                return deleted