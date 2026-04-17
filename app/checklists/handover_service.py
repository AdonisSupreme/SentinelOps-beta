# app/checklists/handover_service.py
"""
Handover Notes Service

Handles shift-to-shift handover notes with proper sequence logic:
- Morning → Afternoon → Night → Morning (next day)
- Notes belong to current instance AND next instance in sequence
- Supports both manual and automatic handover notes
"""

from typing import List, Optional, Dict, Any, Tuple
from uuid import UUID, uuid4
from datetime import datetime, date, timedelta

from app.db.database import get_async_connection
from app.core.frontend_links import build_frontend_url
from app.core.logging import get_logger
from app.core.emailer import send_email_fire_and_forget
from app.gamification.performance_service import PerformanceCommandService
from app.notifications.db_service import NotificationDBService
from app.checklists.db_service import ChecklistDBService
from app.checklists.schemas import (
    HandoverNoteCreate, HandoverNoteResponse, 
    ShiftType, ChecklistStatus
)

log = get_logger("handover-service")

class HandoverService:
    """Service for managing shift handover notes"""
    
    @staticmethod
    def get_shift_sequence() -> List[ShiftType]:
        """Get the standard shift sequence: Morning → Afternoon → Night"""
        return [ShiftType.MORNING, ShiftType.AFTERNOON, ShiftType.NIGHT]
    
    @staticmethod
    def get_next_shift(current_shift: ShiftType) -> tuple[ShiftType, date]:
        """
        Get the next shift in sequence and its date.
        Returns: (next_shift, next_shift_date)
        """
        sequence = HandoverService.get_shift_sequence()
        current_index = sequence.index(current_shift)
        
        # Calculate next shift index (wraps around)
        next_index = (current_index + 1) % len(sequence)
        next_shift = sequence[next_index]
        
        # If we wrap around from NIGHT to MORNING, it's the next day
        if current_shift == ShiftType.NIGHT:
            next_date = date.today() + timedelta(days=1)
        else:
            next_date = date.today()
            
        return next_shift, next_date
    
    @staticmethod
    def get_previous_shift(current_shift: ShiftType) -> tuple[ShiftType, date]:
        """
        Get the previous shift in sequence and its date.
        Returns: (prev_shift, prev_shift_date)
        """
        sequence = HandoverService.get_shift_sequence()
        current_index = sequence.index(current_shift)
        
        # Calculate previous shift index (wraps around)
        prev_index = (current_index - 1) % len(sequence)
        prev_shift = sequence[prev_index]
        
        # If we wrap around from MORNING to NIGHT, it's the previous day
        if current_shift == ShiftType.MORNING:
            prev_date = date.today() - timedelta(days=1)
        else:
            prev_date = date.today()
            
        return prev_shift, prev_date
    
    @staticmethod
    async def create_handover_note(
        from_instance_id: UUID,
        content: str,
        priority: int = 2,
        created_by: Optional[UUID] = None,
        to_shift: Optional[ShiftType] = None,
        to_date: Optional[date] = None
    ) -> Dict[str, Any]:
        """
        Create a handover note that links to the next shift in sequence.
        
        Args:
            from_instance_id: Current checklist instance ID
            content: Handover note content
            priority: Priority level (1-4)
            created_by: User ID creating the note
            to_shift: Optional target shift (defaults to next in sequence)
            to_date: Optional target date (defaults to calculated)
        
        Returns:
            Created handover note data
        """
        async with get_async_connection() as conn:
            # Get current instance details to determine shift sequence
            from_instance = await conn.fetchrow("""
                SELECT id, checklist_date, shift, status, section_id
                FROM checklist_instances
                WHERE id = $1
            """, from_instance_id)
            
            if not from_instance:
                raise ValueError(f"Source instance {from_instance_id} not found")
            
            current_shift = ShiftType(from_instance['shift'])
            current_date = from_instance['checklist_date']
            
            # Determine target shift and date
            if to_shift and to_date:
                # Use provided target
                target_shift = to_shift
                target_date = to_date
            else:
                # Calculate next shift in sequence
                target_shift, target_date = HandoverService.get_next_shift(current_shift)
                # Adjust date based on current instance date
                if current_shift == ShiftType.NIGHT:
                    target_date = current_date + timedelta(days=1)
                else:
                    target_date = current_date
            
            # Find or create the target instance using the same initializer as normal checklist creation
            to_instance_id = await HandoverService._get_or_create_target_instance(
                target_shift=target_shift,
                target_date=target_date,
                created_by=created_by,
                section_id=from_instance['section_id'],
            )
            
            # Create the handover note
            note_id = uuid4()
            note = await conn.fetchrow("""
                INSERT INTO handover_notes 
                (id, from_instance_id, to_instance_id, content, priority, created_by, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING *
            """, note_id, from_instance_id, to_instance_id, content, priority, created_by, datetime.now())

            creator_row = await conn.fetchrow(
                """
                SELECT username, email
                FROM users
                WHERE id = $1
                """,
                created_by,
            )
            creator_username = (creator_row["username"] if creator_row else None) or "SentinelOps"
            notification_summary = await HandoverService._notify_current_and_next_shift_participants(
                conn=conn,
                note_id=note_id,
                from_instance_id=from_instance_id,
                to_instance_id=to_instance_id,
                content=content,
                priority=priority,
                creator_username=creator_username,
                target_shift=target_shift,
                target_date=target_date,
            )
            
            log.info(f"Created handover note {note_id} from {from_instance_id} to {to_instance_id}")
            PerformanceCommandService.schedule_badge_unlock_sync(created_by)
            
            return {
                'id': note['id'],
                'from_instance_id': note['from_instance_id'],
                'to_instance_id': note['to_instance_id'],
                'content': note['content'],
                'priority': note['priority'],
                'created_by': note['created_by'],
                'created_at': note['created_at'],
                'target_shift': target_shift.value,
                'target_date': target_date.isoformat(),
                'notification_summary': notification_summary,
            }
    
    @staticmethod
    async def _get_or_create_target_instance(
        target_shift: ShiftType, 
        target_date: date, 
        created_by: Optional[UUID],
        section_id: Optional[UUID] = None,
    ) -> UUID:
        """
        Get existing target instance or create a new one using
        ChecklistDBService.create_checklist_instance so initialization logic stays identical.
        Returns the target instance ID.
        """
        async with get_async_connection() as conn:
            resolved_section_id = str(section_id) if section_id else None

            # Try to find existing instance
            existing = await conn.fetchrow("""
                SELECT id FROM checklist_instances
                WHERE checklist_date = $1 AND shift = $2
                  AND (
                    ($3::uuid IS NULL AND section_id IS NULL)
                    OR section_id = $3::uuid
                  )
                ORDER BY created_at DESC
                LIMIT 1
            """, target_date, target_shift.value, resolved_section_id)

            if existing:
                return existing['id']

            effective_actor_id, actor_username = await HandoverService._resolve_creation_actor(
                conn=conn,
                created_by=created_by,
            )

        target_template = ChecklistDBService.get_active_template_for_shift(
            target_shift.value,
            resolved_section_id,
        )
        if not target_template:
            scope_label = f" in section {resolved_section_id}" if resolved_section_id else ""
            raise ValueError(
                f"No active template found for shift {target_shift.value} on {target_date}{scope_label}"
            )

        # Reuse existing production initializer: template resolution, item/subitem copy,
        # participant auto-population, section scoping, and event semantics.
        result = ChecklistDBService.create_checklist_instance(
            checklist_date=target_date,
            shift=target_shift.value,
            created_by=effective_actor_id,
            created_by_username=actor_username,
            template_id=UUID(str(target_template["id"])),
            section_id=resolved_section_id,
        )
        if not result:
            raise ValueError("Failed to initialize target handover checklist instance")

        instance_id = result.get("id") or (result.get("instance") or {}).get("id")
        if not instance_id:
            raise ValueError("Target handover checklist instance creation returned no id")

        return UUID(str(instance_id))

    @staticmethod
    async def _resolve_creation_actor(*, conn, created_by: Optional[UUID]) -> Tuple[Optional[UUID], str]:
        if created_by:
            actor = await conn.fetchrow(
                """
                SELECT id, username
                FROM users
                WHERE id = $1
                """,
                created_by,
            )
            if actor:
                return actor["id"], actor["username"] or "sentinel-system"

        fallback = await conn.fetchrow(
            """
            SELECT u.id, u.username
            FROM users u
            LEFT JOIN user_roles ur ON ur.user_id = u.id
            LEFT JOIN roles r ON r.id = ur.role_id
            WHERE u.is_active = TRUE
            ORDER BY
                CASE
                    WHEN LOWER(COALESCE(r.name, '')) = 'admin' THEN 0
                    WHEN LOWER(COALESCE(r.name, '')) = 'manager' THEN 1
                    ELSE 2
                END,
                u.created_at ASC
            LIMIT 1
            """
        )
        if fallback:
            return fallback["id"], fallback["username"] or "sentinel-system"

        return created_by, "sentinel-system"

    @staticmethod
    async def _notify_current_and_next_shift_participants(
        *,
        conn,
        note_id: UUID,
        from_instance_id: UUID,
        to_instance_id: UUID,
        content: str,
        priority: int,
        creator_username: str,
        target_shift: ShiftType,
        target_date: date,
    ) -> Dict[str, int]:
        current_rows = await conn.fetch(
            """
            SELECT DISTINCT u.id, u.email
            FROM checklist_participants cp
            JOIN users u ON u.id = cp.user_id
            WHERE cp.instance_id = $1
            """,
            from_instance_id,
        )
        next_rows = await conn.fetch(
            """
            SELECT DISTINCT u.id, u.email
            FROM checklist_participants cp
            JOIN users u ON u.id = cp.user_id
            WHERE cp.instance_id = $1
            """,
            to_instance_id,
        )

        current_user_ids = {str(row["id"]) for row in current_rows if row.get("id")}
        next_user_ids = {str(row["id"]) for row in next_rows if row.get("id")}

        current_notified = 0
        next_notified = 0

        for user_id in current_user_ids:
            try:
                NotificationDBService.create_notification(
                    title="Handover Logged • Current Shift",
                    message=(
                        f"{creator_username} logged a handover note (priority {priority}) for this shift.\n"
                        "Next-shift participants have been notified."
                    ),
                    user_id=UUID(user_id),
                    related_entity="handover_note",
                    related_id=note_id,
                )
                current_notified += 1
            except Exception as exc:
                log.warning(f"Failed to notify current-shift participant {user_id}: {exc}")

        for user_id in next_user_ids:
            try:
                NotificationDBService.create_notification(
                    title="Incoming Handover • Next Shift",
                    message=(
                        f"Incoming handover for {target_shift.value} on {target_date.isoformat()}.\n"
                        f"{creator_username} submitted a priority {priority} note."
                    ),
                    user_id=UUID(user_id),
                    related_entity="handover_note",
                    related_id=note_id,
                )
                next_notified += 1
            except Exception as exc:
                log.warning(f"Failed to notify next-shift participant {user_id}: {exc}")

        email_recipients = sorted(
            {
                (row.get("email") or "").strip()
                for row in [*current_rows, *next_rows]
                if (row.get("email") or "").strip()
            }
        )
        if email_recipients:
            checklist_link = build_frontend_url(f"/checklist/{to_instance_id}")
            preview = content.strip().replace("\n", " ")
            if len(preview) > 220:
                preview = f"{preview[:217]}..."
            subject = f"SentinelOps // Handover Broadcast • {target_shift.value} Shift"
            text_body = (
                f"Handover Broadcast\n\n"
                f"From: {creator_username}\n"
                f"Priority: {priority}\n"
                f"Target Shift: {target_shift.value}\n"
                f"Target Date: {target_date.isoformat()}\n\n"
                f"Note Preview:\n{preview}\n\n"
                f"Open next-shift checklist: {checklist_link}\n"
            )
            html_body = f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:24px;background:#020617;font-family:'Segoe UI','Helvetica Neue',Arial,sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:700px;margin:0 auto;">
      <tr>
        <td style="background:linear-gradient(145deg,#0f172a 0%,#111827 70%,#1e293b 100%);border:1px solid rgba(34,211,238,0.24);border-radius:20px;overflow:hidden;">
          <div style="padding:24px 28px;background:linear-gradient(135deg,rgba(34,211,238,0.18) 0%,rgba(59,130,246,0.12) 52%,rgba(2,6,23,0.1) 100%);">
            <div style="display:inline-block;padding:6px 12px;border-radius:999px;background:rgba(148,163,184,0.22);color:#e2e8f0;font-size:11px;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;">SentinelOps Handover Pulse</div>
            <h2 style="margin:14px 0 8px;color:#f8fafc;font-size:25px;line-height:1.25;">Incoming Shift Intelligence</h2>
            <p style="margin:0;color:#cbd5e1;font-size:15px;line-height:1.7;">{creator_username} submitted a priority {priority} handover for {target_shift.value} on {target_date.isoformat()}.</p>
          </div>
          <div style="padding:24px 28px 28px;color:#cbd5e1;font-size:14px;line-height:1.65;">
            <p style="margin:0 0 12px;"><strong style="color:#f8fafc;">Note Preview</strong><br>{preview}</p>
            <a href="{checklist_link}" style="display:inline-block;padding:12px 18px;border-radius:12px;background:#22d3ee;color:#020617;text-decoration:none;font-weight:800;">Open Next Shift Checklist</a>
          </div>
        </td>
      </tr>
    </table>
  </body>
</html>
"""
            send_email_fire_and_forget(email_recipients, subject, text_body, html_body)

        return {
            "current_shift_notified": current_notified,
            "next_shift_notified": next_notified,
            "email_recipients": len(email_recipients),
        }
    
    @staticmethod
    async def get_handover_notes_for_instance(
        instance_id: UUID,
        include_outgoing: bool = True,
        include_incoming: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Get handover notes for a specific checklist instance.
        
        Args:
            instance_id: Checklist instance ID
            include_outgoing: Include notes FROM this instance
            include_incoming: Include notes TO this instance
        
        Returns:
            List of handover notes with user details
        """
        async with get_async_connection() as conn:
            notes = []
            
            # Build query based on what to include
            if include_outgoing and include_incoming:
                query = """
                    SELECT 
                        hn.*,
                        from_ci.shift as from_shift,
                        from_ci.checklist_date as from_date,
                        to_ci.shift as to_shift,
                        to_ci.checklist_date as to_date,
                        creator.username as created_by_username,
                        creator.first_name as created_by_first_name,
                        creator.last_name as created_by_last_name,
                        ack_user.username as acknowledged_by_username,
                        resolver.username as resolved_by_username
                    FROM handover_notes hn
                    LEFT JOIN checklist_instances from_ci ON hn.from_instance_id = from_ci.id
                    LEFT JOIN checklist_instances to_ci ON hn.to_instance_id = to_ci.id
                    LEFT JOIN users creator ON hn.created_by = creator.id
                    LEFT JOIN users ack_user ON hn.acknowledged_by = ack_user.id
                    LEFT JOIN users resolver ON hn.resolved_by = resolver.id
                    WHERE hn.from_instance_id = $1 OR hn.to_instance_id = $1
                    ORDER BY hn.created_at DESC
                """
                params = [instance_id]
            elif include_outgoing:
                query = """
                    SELECT 
                        hn.*,
                        from_ci.shift as from_shift,
                        from_ci.checklist_date as from_date,
                        to_ci.shift as to_shift,
                        to_ci.checklist_date as to_date,
                        creator.username as created_by_username,
                        creator.first_name as created_by_first_name,
                        creator.last_name as created_by_last_name,
                        ack_user.username as acknowledged_by_username,
                        resolver.username as resolved_by_username
                    FROM handover_notes hn
                    LEFT JOIN checklist_instances from_ci ON hn.from_instance_id = from_ci.id
                    LEFT JOIN checklist_instances to_ci ON hn.to_instance_id = to_ci.id
                    LEFT JOIN users creator ON hn.created_by = creator.id
                    LEFT JOIN users ack_user ON hn.acknowledged_by = ack_user.id
                    LEFT JOIN users resolver ON hn.resolved_by = resolver.id
                    WHERE hn.from_instance_id = $1
                    ORDER BY hn.created_at DESC
                """
                params = [instance_id]
            else:  # include_incoming only
                query = """
                    SELECT 
                        hn.*,
                        from_ci.shift as from_shift,
                        from_ci.checklist_date as from_date,
                        to_ci.shift as to_shift,
                        to_ci.checklist_date as to_date,
                        creator.username as created_by_username,
                        creator.first_name as created_by_first_name,
                        creator.last_name as created_by_last_name,
                        ack_user.username as acknowledged_by_username,
                        resolver.username as resolved_by_username
                    FROM handover_notes hn
                    LEFT JOIN checklist_instances from_ci ON hn.from_instance_id = from_ci.id
                    LEFT JOIN checklist_instances to_ci ON hn.to_instance_id = to_ci.id
                    LEFT JOIN users creator ON hn.created_by = creator.id
                    LEFT JOIN users ack_user ON hn.acknowledged_by = ack_user.id
                    LEFT JOIN users resolver ON hn.resolved_by = resolver.id
                    WHERE hn.to_instance_id = $1
                    ORDER BY hn.created_at DESC
                """
                params = [instance_id]
            
            rows = await conn.fetch(query, *params)
            
            for row in rows:
                notes.append({
                    'id': row['id'],
                    'from_instance_id': row['from_instance_id'],
                    'to_instance_id': row['to_instance_id'],
                    'content': row['content'],
                    'priority': row['priority'],
                    'acknowledged_by': row['acknowledged_by'],
                    'acknowledged_at': row['acknowledged_at'],
                    'resolved_by': row['resolved_by'],
                    'resolved_at': row['resolved_at'],
                    'resolution_notes': row['resolution_notes'],
                    'created_by': row['created_by'],
                    'created_at': row['created_at'],
                    'from_shift': row['from_shift'],
                    'from_date': row['from_date'],
                    'to_shift': row['to_shift'],
                    'to_date': row['to_date'],
                    'created_by_username': row['created_by_username'],
                    'created_by_first_name': row['created_by_first_name'],
                    'created_by_last_name': row['created_by_last_name'],
                    'acknowledged_by_username': row['acknowledged_by_username'],
                    'resolved_by_username': row['resolved_by_username'],
                    'direction': 'outgoing' if row['from_instance_id'] == instance_id else 'incoming'
                })
            
            return notes
    
    @staticmethod
    async def acknowledge_handover_note(
        note_id: UUID,
        acknowledged_by: UUID
    ) -> Dict[str, Any]:
        """Acknowledge a handover note"""
        async with get_async_connection() as conn:
            note = await conn.fetchrow("""
                UPDATE handover_notes 
                SET acknowledged_by = $1, acknowledged_at = $2
                WHERE id = $3 AND acknowledged_by IS NULL
                RETURNING *
            """, acknowledged_by, datetime.now(), note_id)
            
            if not note:
                raise ValueError(f"Handover note {note_id} not found or already acknowledged")
            
            log.info(f"Handover note {note_id} acknowledged by {acknowledged_by}")
            
            return {
                'id': note['id'],
                'acknowledged_by': note['acknowledged_by'],
                'acknowledged_at': note['acknowledged_at']
            }
    
    @staticmethod
    async def resolve_handover_note(
        note_id: UUID,
        resolved_by: UUID,
        resolution_notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """Resolve a handover note"""
        async with get_async_connection() as conn:
            note = await conn.fetchrow("""
                UPDATE handover_notes 
                SET resolved_by = $1, resolved_at = $2, resolution_notes = $3
                WHERE id = $4 AND resolved_by IS NULL
                RETURNING *
            """, resolved_by, datetime.now(), resolution_notes, note_id)
            
            if not note:
                raise ValueError(f"Handover note {note_id} not found or already resolved")
            
            log.info(f"Handover note {note_id} resolved by {resolved_by}")
            PerformanceCommandService.schedule_badge_unlock_sync(resolved_by)
            
            return {
                'id': note['id'],
                'resolved_by': note['resolved_by'],
                'resolved_at': note['resolved_at'],
                'resolution_notes': note['resolution_notes']
            }
    
    @staticmethod
    async def create_automatic_handover_notes(
        from_instance_id: UUID,
        checklist_date: date,
        shift: ShiftType,
        user_id: UUID
    ):
        """
        Create automatic handover notes from previous shift if there were issues.
        This is called when a new checklist instance is created.
        """
        async with get_async_connection() as conn:
            # Get previous shift details
            prev_shift, prev_date = HandoverService.get_previous_shift(shift)
            
            # Find previous instance
            prev_instance = await conn.fetchrow("""
                SELECT ci.id, ci.status, COUNT(CASE WHEN cii.status = 'FAILED' THEN 1 END) as failed_items,
                       COUNT(CASE WHEN cii.status != 'COMPLETED' THEN 1 END) as incomplete_items
                FROM checklist_instances ci
                LEFT JOIN checklist_instance_items cii ON ci.id = cii.instance_id
                WHERE ci.checklist_date = $1 AND ci.shift = $2
                GROUP BY ci.id
                LIMIT 1
            """, prev_date, prev_shift.value)
            
            if not prev_instance:
                log.info(f"No previous instance found for {prev_shift.value} on {prev_date}")
                return
            
            prev_id = prev_instance['id']
            prev_status = prev_instance['status']
            failed_count = prev_instance['failed_items'] or 0
            incomplete_count = prev_instance['incomplete_items'] or 0
            
            # Only create handover if there were issues
            if (prev_status in [ChecklistStatus.COMPLETED_WITH_EXCEPTIONS.value, 
                              ChecklistStatus.INCOMPLETE.value] or 
                failed_count > 0 or incomplete_count > 0):
                
                content = (
                    f"Automatic handover from {prev_shift.value.lower()} shift. "
                    f"Status: {prev_status}, "
                    f"Failed items: {failed_count}, "
                    f"Incomplete items: {incomplete_count}"
                )
                
                priority = 2 if failed_count == 0 else 3 if failed_count <= 3 else 4
                
                await HandoverService.create_handover_note(
                    from_instance_id=prev_id,
                    content=content,
                    priority=priority,
                    created_by=user_id
                )
                
                log.info(f"Created automatic handover note from {prev_shift.value} shift")

    @staticmethod
    async def create_exception_item_handover_notes(
        *,
        from_instance_id: UUID,
        created_by: UUID,
    ) -> Dict[str, int]:
        """
        Create one outgoing handover note per completed item that contains
        reported/failed subitems.

        Skipped subitems are intentionally excluded from auto carry-over because
        they do not necessarily represent unfinished operational risk. Failed
        subitems do carry forward with their exact subitem titles and captured
        failure reasons so the next shift has actionable context.
        """
        async with get_async_connection() as conn:
            supports_final_verdict = bool(
                await conn.fetchval(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'checklist_instance_items'
                      AND column_name = 'final_verdict'
                    LIMIT 1
                    """
                )
            )
            item_rows = await conn.fetch(
                f"""
                SELECT
                    cii.id,
                    COALESCE(cti.title, 'Checklist item') AS item_title,
                    COALESCE((
                        SELECT cia.comment
                        FROM checklist_item_activity cia
                        WHERE cia.instance_item_id = cii.id
                          AND cia.action = 'COMPLETED'
                          AND cia.comment IS NOT NULL
                          AND BTRIM(cia.comment) <> ''
                        ORDER BY cia.created_at DESC
                        LIMIT 1
                    ), '') AS latest_note,
                    {("COALESCE(NULLIF(BTRIM(cii.final_verdict), ''), '') AS final_verdict," if supports_final_verdict else "'' AS final_verdict,")}
                    COUNT(*) FILTER (WHERE cis.status = 'FAILED') AS failed_subitems
                FROM checklist_instance_items cii
                LEFT JOIN checklist_template_items cti ON cti.id = cii.template_item_id
                LEFT JOIN checklist_instance_subitems cis ON cis.instance_item_id = cii.id
                WHERE cii.instance_id = $1
                  AND cii.status = 'COMPLETED'
                GROUP BY cii.id, cti.title
                HAVING COUNT(*) FILTER (WHERE cis.status = 'FAILED') > 0
                ORDER BY COALESCE(cti.title, 'Checklist item')
                """,
                from_instance_id,
            )

            if not item_rows:
                return {"created": 0, "skipped_existing": 0, "missing_note": 0}

            existing_contents = {
                (row["content"] or "").strip()
                for row in await conn.fetch(
                    """
                    SELECT content
                    FROM handover_notes
                    WHERE from_instance_id = $1
                    """,
                    from_instance_id,
                )
            }

            created_count = 0
            skipped_existing = 0
            missing_note = 0

            for row in item_rows:
                latest_note = (row["latest_note"] or "").strip()
                final_verdict = (row["final_verdict"] or "").strip()
                failed_subitems = row["failed_subitems"] or 0
                failure_rows = await conn.fetch(
                    """
                    SELECT
                        COALESCE(NULLIF(BTRIM(cis.title), ''), 'Subitem') AS subitem_title,
                        COALESCE(NULLIF(BTRIM(cis.failure_reason), ''), 'No failure reason captured.') AS failure_reason
                    FROM checklist_instance_subitems cis
                    WHERE cis.instance_item_id = $1
                      AND cis.status = 'FAILED'
                    ORDER BY cis.sort_order ASC, cis.created_at ASC, cis.title ASC
                    """,
                    row["id"],
                )
                if not failure_rows:
                    continue

                if not latest_note:
                    missing_note += 1

                priority = 4 if failed_subitems > 0 else 3
                failure_lines = [
                    f"- {failure_row['subitem_title']}: {failure_row['failure_reason']}"
                    for failure_row in failure_rows
                ]
                content_lines = [
                    f"Automatic carry-over: {row['item_title']}",
                    "Reported subitems:",
                    *failure_lines,
                    f"Execution note: {latest_note or 'No item completion note captured.'}",
                    f"Final verdict: {final_verdict or 'No final verdict recorded.'}",
                ]
                content = "\n".join(content_lines)

                if content in existing_contents:
                    skipped_existing += 1
                    continue

                await HandoverService.create_handover_note(
                    from_instance_id=from_instance_id,
                    content=content,
                    priority=priority,
                    created_by=created_by,
                )
                existing_contents.add(content)
                created_count += 1

        return {
            "created": created_count,
            "skipped_existing": skipped_existing,
            "missing_note": missing_note,
        }
