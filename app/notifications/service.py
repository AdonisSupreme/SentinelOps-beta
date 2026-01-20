# app/notifications/service.py
import asyncio
from typing import List, Optional
from uuid import UUID
from datetime import datetime, timedelta

from app.db.database import get_async_connection
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
        async with get_async_connection() as conn:
            query = """
                SELECT 
                    n.id, n.user_id, n.role_id, n.title, n.message, n.related_entity,
                    n.related_id, n.is_read, n.created_at,
                    u.username as from_username,
                    r.name as from_role
                FROM notifications n
                LEFT JOIN users u ON n.user_id = u.id
                LEFT JOIN roles r ON n.role_id = r.id
                WHERE (n.user_id = $1 OR n.role_id IN (
                    SELECT role_id FROM user_roles WHERE user_id = $1
                ))
                {unread_filter}
                ORDER BY n.created_at DESC
                LIMIT $2 OFFSET $3
            """
            
            if unread_only:
                query = query.format(unread_filter="AND n.is_read = FALSE")
            else:
                query = query.format(unread_filter="")
            
            rows = await conn.fetch(query, user_id, limit, offset)
            
            return [
                {
                    'id': row['id'],
                    'user_id': row['user_id'],
                    'role_id': row['role_id'],
                    'title': row['title'],
                    'message': row['message'],
                    'related_entity': row['related_entity'],
                    'related_id': row['related_id'],
                    'is_read': row['is_read'],
                    'created_at': row['created_at'],
                    'from_username': row['from_username'],
                    'from_role': row['from_role']
                }
                for row in rows
            ]
    
    @staticmethod
    async def mark_as_read(
        notification_id: UUID,
        user_id: UUID
    ) -> bool:
        """Mark a notification as read"""
        async with get_async_connection() as conn:
            result = await conn.execute("""
                UPDATE notifications 
                SET is_read = TRUE 
                    WHERE id = $1 AND (user_id = $2 OR role_id IN (
                        SELECT role_id FROM user_roles WHERE user_id = $2
                    ))
                """, notification_id, user_id)
            
            return result == 'UPDATE 1'
    
    @staticmethod
    async def mark_all_as_read(user_id: UUID) -> int:
        """Mark all user notifications as read"""
        async with get_async_connection() as conn:
            result = await conn.execute("""
                UPDATE notifications 
                SET is_read = TRUE 
                WHERE (user_id = $1 OR role_id IN (
                    SELECT role_id FROM user_roles WHERE user_id = $1
                )) AND is_read = FALSE
            """, user_id)
            
            if result:
                count = int(result.split()[-1]) if result.startswith('UPDATE') else 0
                return count
            return 0
    
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
        async with get_async_connection() as conn:
            row = await conn.fetchrow("""
                INSERT INTO notifications 
                (user_id, role_id, title, message, related_entity, related_id)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id, user_id, role_id, title, message, related_entity, related_id, is_read, created_at
            """, user_id, role_id, title, message, related_entity, related_id)
            
            return {
                'id': row['id'],
                'user_id': row['user_id'],
                'role_id': row['role_id'],
                'title': row['title'],
                'message': row['message'],
                'related_entity': row['related_entity'],
                'related_id': row['related_id'],
                'is_read': row['is_read'],
                'created_at': row['created_at']
            }
    
    @staticmethod
    async def create_bulk_notifications(
        notifications: List[dict]
    ) -> int:
        """Create multiple notifications efficiently"""
        async with get_async_connection() as conn:
            async with conn.transaction():
                for n in notifications:
                    await conn.execute("""
                        INSERT INTO notifications 
                        (user_id, role_id, title, message, related_entity, related_id)
                        VALUES ($1, $2, $3, $4, $5, $6)
                    """, n.get('user_id'), n.get('role_id'), n['title'], n['message'], 
                       n.get('related_entity'), n.get('related_id'))
            
            return len(notifications)
    
    @staticmethod
    async def cleanup_old_notifications(days: int = 30):
        """Clean up notifications older than specified days"""
        async with get_async_connection() as conn:
            cutoff_date = datetime.now() - timedelta(days=days)
            result = await conn.execute("""
                DELETE FROM notifications 
                WHERE created_at < $1 AND is_read = TRUE
            """, cutoff_date)
            
            deleted = int(result.split()[-1]) if result and result.startswith('DELETE') else 0
            log.info(f"Cleaned up {deleted} old notifications")
            return deleted