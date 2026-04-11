# app/notifications/db_service.py
"""
Database-backed Notification Service
- Stores all notifications in PostgreSQL
- Supports user and role-based targeting
- Auto-notifies admin/manager on critical events (skip/fail/escalation)
"""

from typing import List, Optional
from uuid import UUID, uuid4
from datetime import datetime, timezone, timedelta
import json
import asyncio

from app.db.database import get_connection, get_async_connection
from app.core.logging import get_logger

log = get_logger("notifications-db-service")


class NotificationDBService:
    """Database-backed notification service"""

    LEGACY_DUPLICATE_WINDOW_SECONDS = 5

    @staticmethod
    def _normalize_priority(priority: Optional[str]) -> str:
        normalized = str(priority or "").lower()
        if normalized in {"low", "high"}:
            return normalized
        return "medium"

    @staticmethod
    def _derive_priority(
        title: Optional[str],
        message: Optional[str],
        related_entity: Optional[str],
        priority: Optional[str] = None,
    ) -> str:
        if priority:
            return NotificationDBService._normalize_priority(priority)

        related_key = str(related_entity or "").lower()
        if related_key == "checklist_manager_alert":
            return "high"
        if related_key in {"performance_badge"}:
            return "low"
        if related_key in {"checklist_manager_review", "schedule"}:
            return "medium"

        signal = f"{title or ''} {message or ''}".lower()
        if any(token in signal for token in ("critical", "failed", "down", "exception", "urgent")):
            return "high"
        if any(token in signal for token in ("badge", "unlocked", "success")):
            return "low"
        return "medium"

    @staticmethod
    def create_notification(
        title: str,
        message: str,
        user_id: Optional[UUID] = None,
        role_id: Optional[UUID] = None,
        related_entity: Optional[str] = None,
        related_id: Optional[UUID] = None,
        priority: Optional[str] = None,
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
                    cur.execute(
                        """
                        INSERT INTO notifications (
                            id, user_id, role_id, title, message,
                            related_entity, related_id, is_read, created_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, FALSE, %s
                        ) RETURNING id, user_id, role_id, title, message,
                                  related_entity, related_id, is_read, created_at
                        """,
                        (
                            notification_id,
                            user_id,
                            role_id,
                            title,
                            message,
                            related_entity,
                            related_id,
                            datetime.now(timezone.utc),
                        ),
                    )

                    result = cur.fetchone()
                    if result and result[1]:
                        recipient_user_ids: List[str] = [str(result[1])]
                    elif result and result[2]:
                        cur.execute(
                            """
                            SELECT DISTINCT user_id
                            FROM user_roles
                            WHERE role_id = %s
                            """,
                            (result[2],),
                        )
                        recipient_user_ids = [str(row[0]) for row in cur.fetchall()]
                    else:
                        recipient_user_ids = []

                    conn.commit()

                    if result:
                        log.info(f"Notification created: {notification_id}")
                        resolved_priority = NotificationDBService._derive_priority(
                            result[3],
                            result[4],
                            result[5],
                            priority,
                        )
                        notification = {
                            "id": str(result[0]),
                            "user_id": str(result[1]) if result[1] else None,
                            "role_id": str(result[2]) if result[2] else None,
                            "title": result[3],
                            "message": result[4],
                            "related_entity": result[5],
                            "related_id": str(result[6]) if result[6] else None,
                            "is_read": result[7],
                            "created_at": result[8].isoformat() if result[8] else None,
                            "priority": resolved_priority,
                        }
                        NotificationDBService._dispatch_realtime_notification(notification, recipient_user_ids)
                        return notification
        except Exception as e:
            log.error(f"Failed to create notification: {e}")
            raise

    @staticmethod
    def _dispatch_realtime_notification(notification: dict, recipient_user_ids: List[str]) -> None:
        """Best-effort realtime fanout to connected notification sockets."""
        if not notification or not recipient_user_ids:
            return

        async def _broadcast():
            from app.notifications.websocket import send_notification_to_users

            await send_notification_to_users(recipient_user_ids, notification)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_broadcast())
        except RuntimeError:
            try:
                asyncio.run(_broadcast())
            except Exception as e:
                log.warning(f"Failed realtime notification broadcast for {notification.get('id')}: {e}")

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
                    # Create one notification per recipient user, not per role.
                    # This avoids duplicates for users who hold multiple roles.
                    cur.execute(
                        """
                        SELECT DISTINCT ur.user_id
                        FROM user_roles ur
                        JOIN roles r ON r.id = ur.role_id
                        WHERE r.name IN ('admin', 'manager')
                        """
                    )
                    user_rows = cur.fetchall()

                    if not user_rows:
                        log.warning("No admin/manager recipients found in database")
                        return notifications

                    for (user_id,) in user_rows:
                        try:
                            notification = NotificationDBService.create_notification(
                                title=title,
                                message=message,
                                user_id=user_id,
                                related_entity=related_entity,
                                related_id=related_id,
                            )
                            notifications.append(notification)
                        except Exception as e:
                            log.error(f"Failed to notify user {user_id}: {e}")

                    log.info(f"Notified {len(notifications)} admin/manager users")

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
                    seen_legacy_role_notifications = set()

                    for row in rows:
                        created_at = row[8]

                        # Legacy role-targeted rows can produce duplicate cards for
                        # users who have both admin and manager roles.
                        if row[1] is None and row[2] is not None:
                            timestamp_bucket = (
                                int(created_at.timestamp()) // NotificationDBService.LEGACY_DUPLICATE_WINDOW_SECONDS
                                if created_at
                                else 0
                            )
                            legacy_key = (
                                row[3],
                                row[4],
                                row[5],
                                str(row[6]) if row[6] else None,
                                timestamp_bucket,
                            )
                            if legacy_key in seen_legacy_role_notifications:
                                continue
                            seen_legacy_role_notifications.add(legacy_key)

                        notifications.append(
                            {
                                "id": str(row[0]),
                                "user_id": str(row[1]) if row[1] else None,
                                "role_id": str(row[2]) if row[2] else None,
                                "title": row[3],
                                "message": row[4],
                                "related_entity": row[5],
                                "related_id": str(row[6]) if row[6] else None,
                                "is_read": row[7],
                                "created_at": created_at.isoformat() if created_at else None,
                                "priority": NotificationDBService._derive_priority(row[3], row[4], row[5]),
                            }
                        )

                    log.info(
                        "Retrieved %s notifications for user %s from DB (unread_only=%s, limit=%s, offset=%s)",
                        len(notifications),
                        user_id,
                        unread_only,
                        limit,
                        offset,
                    )
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
                    cur.execute(
                        """
                        SELECT id, user_id, role_id, title, message, related_entity, related_id, created_at
                        FROM notifications
                        WHERE id = %s AND (
                            user_id = %s OR
                            role_id IN (SELECT role_id FROM user_roles WHERE user_id = %s)
                        )
                        """,
                        (notification_id, user_id, user_id),
                    )

                    notification_row = cur.fetchone()
                    if not notification_row:
                        log.warning(f"User {user_id} tried to mark unowned notification {notification_id}")
                        return False

                    (
                        _notification_id,
                        notification_user_id,
                        notification_role_id,
                        title,
                        message,
                        related_entity,
                        related_id,
                        created_at,
                    ) = notification_row

                    if notification_role_id and not notification_user_id and created_at:
                        duplicate_window = timedelta(
                            seconds=NotificationDBService.LEGACY_DUPLICATE_WINDOW_SECONDS
                        )
                        cur.execute(
                            """
                            UPDATE notifications
                            SET is_read = TRUE
                            WHERE is_read = FALSE
                              AND (
                                user_id = %s OR
                                role_id IN (SELECT role_id FROM user_roles WHERE user_id = %s)
                              )
                              AND title = %s
                              AND message = %s
                              AND related_entity IS NOT DISTINCT FROM %s
                              AND related_id IS NOT DISTINCT FROM %s
                              AND created_at BETWEEN %s AND %s
                            """,
                            (
                                user_id,
                                user_id,
                                title,
                                message,
                                related_entity,
                                related_id,
                                created_at - duplicate_window,
                                created_at + duplicate_window,
                            ),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE notifications
                            SET is_read = TRUE
                            WHERE id = %s
                            """,
                            (notification_id,),
                        )

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
                    cur.execute(
                        """
                        UPDATE notifications SET is_read = TRUE
                        WHERE (user_id = %s OR role_id IN (
                            SELECT role_id FROM user_roles WHERE user_id = %s
                        )) AND is_read = FALSE
                        RETURNING id
                        """,
                        (user_id, user_id),
                    )

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
                    cur.execute(
                        """
                        SELECT COUNT(*) FROM notifications
                        WHERE (user_id = %s OR role_id IN (
                            SELECT role_id FROM user_roles WHERE user_id = %s
                        )) AND is_read = FALSE
                        """,
                        (user_id, user_id),
                    )

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
        title = "âš ï¸ Checklist Item Skipped"
        message = (
            f"Item '{item_title}' was skipped on {checklist_date} ({shift} shift).\n"
            f"Reason: {skipped_reason}"
        )

        return NotificationDBService.notify_admin_and_managers(
            title=title,
            message=message,
            related_entity="checklist_item",
            related_id=item_id,
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
        title = "ðŸš¨ CRITICAL: Checklist Item Escalated"
        message = (
            f"Item '{item_title}' FAILED on {checklist_date} ({shift} shift).\n"
            f"Issue: {failure_reason}\n"
            f"Requires immediate attention."
        )

        return NotificationDBService.notify_admin_and_managers(
            title=title,
            message=message,
            related_entity="checklist_item",
            related_id=item_id,
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
        title = f"âœ… Checklist Completed - {shift} Shift ({completion_rate:.0f}%)"
        message = (
            f"Shift checklist for {checklist_date} ({shift}) "
            f"completed by {completed_by_username}.\n"
            f"Completion rate: {completion_rate:.1f}%"
        )

        return NotificationDBService.notify_admin_and_managers(
            title=title,
            message=message,
            related_entity="checklist_instance",
            related_id=instance_id,
        )

    @staticmethod
    def create_participant_joined_notification(
        instance_id: UUID,
        participant_username: str,
        checklist_date: str,
        shift: str
    ) -> List[dict]:
        """
        Notify current participants and managers when someone joins a checklist.
        Social/collaboration event.
        """
        title = f"ðŸ‘‹ {participant_username} joined the shift"
        message = (
            f"{participant_username} joined the {shift} shift checklist for {checklist_date}.\n"
            f"Team collaboration is now active."
        )

        notifications = []

        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT user_id FROM checklist_participants
                        WHERE instance_id = %s AND user_id != (
                            SELECT id FROM users WHERE username = %s
                        )
                        """,
                        (instance_id, participant_username),
                    )

                    participant_rows = cur.fetchall()

                    for (user_id,) in participant_rows:
                        try:
                            notification = NotificationDBService.create_notification(
                                title=title,
                                message=message,
                                user_id=user_id,
                                related_entity="checklist_instance",
                                related_id=instance_id,
                            )
                            notifications.append(notification)
                        except Exception as e:
                            log.error(f"Failed to notify participant {user_id}: {e}")

                    manager_notifications = NotificationDBService.notify_admin_and_managers(
                        title=title,
                        message=message,
                        related_entity="checklist_instance",
                        related_id=instance_id,
                    )
                    notifications.extend(manager_notifications)

                    log.info(f"Notified {len(notifications)} people about {participant_username} joining")

        except Exception as e:
            log.error(f"Failed to notify about participant join: {e}")

        return notifications

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
        title = f"ðŸ”’ Checklist Override - {shift} Shift"
        message = (
            f"Supervisor {supervisor_username} overrode checklist for {checklist_date} ({shift}).\n"
            f"Reason: {override_reason}"
        )

        return NotificationDBService.notify_admin_and_managers(
            title=title,
            message=message,
            related_entity="checklist_override",
            related_id=instance_id,
        )
