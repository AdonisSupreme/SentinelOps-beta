# app/checklists/db_service.py
"""
Database-backed Checklist Service

Complete replacement for file-based services.
All templates, instances, items, activities are DB-driven.
Uses state_transition_rules table for validation.
"""

from typing import Dict, List, Optional, Any
from uuid import UUID, uuid4
from datetime import datetime, date, time, timedelta, timezone
import json
from zoneinfo import ZoneInfo

from app.db.database import get_connection
from app.core.config import settings
from app.core.logging import get_logger
from app.notifications.db_service import NotificationDBService
from app.ops.events import OpsEventLogger
from app.services.websocket import websocket_manager
from psycopg.rows import dict_row

log = get_logger("checklist-db-service")


class ChecklistDBService:
    """Database-backed checklist service - full replacement for file-based logic"""

    DEFAULT_SHIFT_WINDOWS = {
        'MORNING': (time(7, 0), time(15, 0)),
        'AFTERNOON': (time(15, 0), time(23, 0)),
        'NIGHT': (time(23, 0), time(7, 0)),
    }
    INSTANCE_LATE_INIT_GRACE = timedelta(hours=2)

    @staticmethod
    def _serialize_time(value: Optional[time]) -> Optional[str]:
        return value.isoformat() if value else None

    @staticmethod
    def _serialize_datetime(value: Optional[datetime]) -> Optional[str]:
        return value.isoformat() if value else None

    @staticmethod
    def _serialize_uuid_list(values: Optional[List[UUID]]) -> Optional[List[str]]:
        if values is None:
            return None
        return [str(value) for value in values]

    @staticmethod
    def _coerce_uuid(value: Optional[Any]) -> Optional[UUID]:
        if value in (None, ""):
            return None
        if isinstance(value, UUID):
            return value
        return UUID(str(value))

    @staticmethod
    def _normalize_shift_name(shift: str) -> str:
        return str(getattr(shift, "value", shift or "")).upper()

    @staticmethod
    def _normalize_item_type(item_type: Optional[str]) -> Optional[str]:
        if item_type is None:
            return None
        return str(getattr(item_type, "value", item_type)).upper()

    @staticmethod
    def _get_business_timezone() -> ZoneInfo:
        return ZoneInfo(settings.TRUSTLINK_SCHEDULE_TIMEZONE)

    @staticmethod
    def _get_shift_window(cur, shift: str) -> tuple[str, time, time]:
        shift_key = ChecklistDBService._normalize_shift_name(shift)

        cur.execute("""
            SELECT start_time, end_time
            FROM shifts
            WHERE UPPER(name) = %s
            ORDER BY id ASC
            LIMIT 1
        """, (shift_key,))
        row = cur.fetchone()
        if row and row[0] and row[1]:
            return shift_key, row[0], row[1]

        fallback = ChecklistDBService.DEFAULT_SHIFT_WINDOWS.get(shift_key)
        if fallback:
            log.warning("Using fallback shift window for %s because shifts table had no match", shift_key)
            return shift_key, fallback[0], fallback[1]

        raise ValueError(f"Invalid shift type: {shift}")

    @staticmethod
    def _is_overnight_window(start_time: time, end_time: time) -> bool:
        return end_time <= start_time

    @staticmethod
    def _is_time_within_shift_window(scheduled_time: time, start_time: time, end_time: time) -> bool:
        if start_time == end_time:
            return True
        if start_time < end_time:
            return start_time <= scheduled_time < end_time
        return scheduled_time >= start_time or scheduled_time < end_time

    @staticmethod
    def _normalize_schedule_fields(
        item_type: Optional[str],
        scheduled_time: Optional[time],
        notify_before_minutes: Optional[int],
    ) -> tuple[Optional[time], Optional[int]]:
        if ChecklistDBService._normalize_item_type(item_type) == 'TIMED':
            return scheduled_time, notify_before_minutes
        return None, None

    @staticmethod
    def _resolve_shift_datetimes(
        checklist_date: date,
        start_time: time,
        end_time: time,
    ) -> tuple[datetime, datetime]:
        business_tz = ChecklistDBService._get_business_timezone()
        shift_start_local = datetime.combine(checklist_date, start_time, tzinfo=business_tz)
        shift_end_date = checklist_date + timedelta(days=1) if ChecklistDBService._is_overnight_window(start_time, end_time) else checklist_date
        shift_end_local = datetime.combine(shift_end_date, end_time, tzinfo=business_tz)
        return shift_start_local.astimezone(timezone.utc), shift_end_local.astimezone(timezone.utc)

    @staticmethod
    def _resolve_reminder_snapshot(
        checklist_date: date,
        scheduled_time: Optional[time],
        notify_before_minutes: Optional[int],
        shift_start: datetime,
        shift_end: datetime,
    ) -> tuple[Optional[datetime], Optional[datetime]]:
        if scheduled_time is None:
            return None, None

        business_tz = ChecklistDBService._get_business_timezone()
        localized_start = shift_start.astimezone(business_tz)
        localized_end = shift_end.astimezone(business_tz)
        scheduled_local = datetime.combine(checklist_date, scheduled_time, tzinfo=business_tz)

        if localized_end.date() > localized_start.date() and scheduled_time < localized_start.timetz().replace(tzinfo=None):
            scheduled_local += timedelta(days=1)

        reminder_local = scheduled_local - timedelta(minutes=int(notify_before_minutes or 0))
        return scheduled_local.astimezone(timezone.utc), reminder_local.astimezone(timezone.utc)

    @staticmethod
    def _coerce_datetime(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        return None

    @staticmethod
    def _resolve_first_timed_deadline(
        checklist_date: date,
        shift_start: datetime,
        shift_end: datetime,
        item_snapshots: List[dict],
    ) -> Optional[datetime]:
        deadlines: List[datetime] = []

        for item_snapshot in item_snapshots:
            item_scheduled_time = item_snapshot.get('scheduled_time')
            if ChecklistDBService._normalize_item_type(item_snapshot.get('item_type')) == 'TIMED' and item_scheduled_time:
                scheduled_at, _ = ChecklistDBService._resolve_reminder_snapshot(
                    checklist_date,
                    item_scheduled_time,
                    item_snapshot.get('notify_before_minutes'),
                    shift_start,
                    shift_end,
                )
                if scheduled_at:
                    deadlines.append(scheduled_at)

            for subitem_snapshot in item_snapshot.get('subitems') or []:
                subitem_scheduled_time = subitem_snapshot.get('scheduled_time')
                if ChecklistDBService._normalize_item_type(subitem_snapshot.get('item_type')) == 'TIMED' and subitem_scheduled_time:
                    scheduled_at, _ = ChecklistDBService._resolve_reminder_snapshot(
                        checklist_date,
                        subitem_scheduled_time,
                        subitem_snapshot.get('notify_before_minutes'),
                        shift_start,
                        shift_end,
                    )
                    if scheduled_at:
                        deadlines.append(scheduled_at)

            for scheduled_event in item_snapshot.get('scheduled_events') or []:
                event_datetime = ChecklistDBService._coerce_datetime(scheduled_event.get('event_datetime'))
                if event_datetime:
                    deadlines.append(event_datetime.astimezone(timezone.utc))

        return min(deadlines) if deadlines else None

    @staticmethod
    def _enforce_instance_initialization_window(
        checklist_date: date,
        shift_start: datetime,
        shift_end: datetime,
        item_snapshots: List[dict],
    ) -> None:
        first_deadline = ChecklistDBService._resolve_first_timed_deadline(
            checklist_date,
            shift_start,
            shift_end,
            item_snapshots,
        )
        if first_deadline is None:
            return

        now_utc = datetime.now(timezone.utc)
        allowed_until = first_deadline + ChecklistDBService.INSTANCE_LATE_INIT_GRACE
        if now_utc <= allowed_until:
            return

        business_tz = ChecklistDBService._get_business_timezone()
        raise ValueError(
            "Checklist instance can only be initialized up to 2 hours after the first timed deadline. "
            f"First deadline: {first_deadline.astimezone(business_tz).strftime('%Y-%m-%d %H:%M %Z')}. "
            f"Grace ends: {allowed_until.astimezone(business_tz).strftime('%Y-%m-%d %H:%M %Z')}."
        )

    @staticmethod
    def _validate_timed_entry(
        *,
        label: str,
        shift: str,
        shift_start_time: time,
        shift_end_time: time,
        item_type: Optional[str],
        scheduled_time: Optional[time],
    ) -> None:
        if ChecklistDBService._normalize_item_type(item_type) != 'TIMED':
            return

        if isinstance(scheduled_time, str):
            scheduled_time = time.fromisoformat(scheduled_time)

        if scheduled_time is None:
            raise ValueError(f"{label} is TIMED and requires a scheduled_time for the {shift} shift")

        if not ChecklistDBService._is_time_within_shift_window(scheduled_time, shift_start_time, shift_end_time):
            raise ValueError(
                f"{label} scheduled_time {scheduled_time.strftime('%H:%M')} falls outside the {shift} shift window "
                f"({shift_start_time.strftime('%H:%M')} - {shift_end_time.strftime('%H:%M')})"
            )

    @staticmethod
    def _validate_template_items_against_shift(cur, shift: str, items_data: Optional[List[dict]]) -> None:
        if not items_data:
            return

        shift_key, shift_start_time, shift_end_time = ChecklistDBService._get_shift_window(cur, shift)
        for item_index, item_data in enumerate(items_data, start=1):
            item_label = item_data.get('title') or f"Template item #{item_index}"
            ChecklistDBService._validate_timed_entry(
                label=item_label,
                shift=shift_key,
                shift_start_time=shift_start_time,
                shift_end_time=shift_end_time,
                item_type=item_data.get('item_type'),
                scheduled_time=item_data.get('scheduled_time'),
            )

            for subitem_index, subitem_data in enumerate(item_data.get('subitems') or [], start=1):
                ChecklistDBService._validate_timed_entry(
                    label=f"{item_label} / {subitem_data.get('title') or f'Subitem #{subitem_index}'}",
                    shift=shift_key,
                    shift_start_time=shift_start_time,
                    shift_end_time=shift_end_time,
                    item_type=subitem_data.get('item_type'),
                    scheduled_time=subitem_data.get('scheduled_time'),
                )

    @staticmethod
    def validate_template_payload_for_shift(shift: str, items_data: Optional[List[dict]]) -> None:
        with get_connection() as conn:
            with conn.cursor() as cur:
                ChecklistDBService._validate_template_items_against_shift(cur, shift, items_data)

    @staticmethod
    def _get_instance_scheduled_events_for_item(cur, instance_item_id: UUID) -> List[dict]:
        cur.execute("""
            SELECT id, instance_item_id, template_event_id, event_datetime,
                   notify_before_minutes, remind_at, created_at
            FROM checklist_instance_scheduled_events
            WHERE instance_item_id = %s
            ORDER BY event_datetime ASC, created_at ASC
        """, (instance_item_id,))

        events = []
        for event_row in cur.fetchall():
            events.append({
                'id': str(event_row[0]),
                'instance_item_id': str(event_row[1]),
                'template_event_id': str(event_row[2]) if event_row[2] else None,
                'event_datetime': ChecklistDBService._serialize_datetime(event_row[3]),
                'notify_before_minutes': event_row[4],
                'remind_at': ChecklistDBService._serialize_datetime(event_row[5]),
                'created_at': ChecklistDBService._serialize_datetime(event_row[6]),
            })

        return events

    @staticmethod
    def _get_scheduled_events_for_item(cur, item_id: UUID) -> List[dict]:
        cur.execute("""
            SELECT id, template_item_id, event_datetime, notify_before_minutes,
                   notify_roles, notify_all, created_by, created_at
            FROM checklist_scheduled_events
            WHERE template_item_id = %s
            ORDER BY event_datetime ASC, created_at ASC
        """, (item_id,))

        events = []
        for event_row in cur.fetchall():
            events.append({
                'id': str(event_row[0]),
                'template_item_id': str(event_row[1]),
                'event_datetime': ChecklistDBService._serialize_datetime(event_row[2]),
                'notify_before_minutes': event_row[3],
                'notify_roles': ChecklistDBService._serialize_uuid_list(event_row[4]),
                'notify_all': bool(event_row[5]),
                'created_by': str(event_row[6]) if event_row[6] else None,
                'created_at': ChecklistDBService._serialize_datetime(event_row[7]),
            })

        return events

    @staticmethod
    def _sync_scheduled_events(
        cur,
        item_id: UUID,
        scheduled_events: Optional[List[dict]],
        created_by: Optional[UUID] = None,
    ) -> List[dict]:
        if scheduled_events is None:
            return ChecklistDBService._get_scheduled_events_for_item(cur, item_id)

        cur.execute("""
            SELECT id
            FROM checklist_scheduled_events
            WHERE template_item_id = %s
        """, (item_id,))
        existing_ids = {str(row[0]) for row in cur.fetchall()}
        mentioned_ids = {str(event.get('id')) for event in scheduled_events if event.get('id')}

        ids_to_delete = existing_ids - mentioned_ids
        if ids_to_delete:
            cur.execute("""
                DELETE FROM checklist_scheduled_events
                WHERE template_item_id = %s AND id = ANY(%s)
            """, (item_id, list(ids_to_delete)))

        for event_data in scheduled_events:
            event_id = event_data.get('id')
            event_datetime = event_data.get('event_datetime')
            notify_before_minutes = event_data.get('notify_before_minutes', 30)
            notify_roles = event_data.get('notify_roles')
            notify_all = event_data.get('notify_all', False)

            if event_id and str(event_id) in existing_ids:
                cur.execute("""
                    UPDATE checklist_scheduled_events
                    SET event_datetime = %s,
                        notify_before_minutes = %s,
                        notify_roles = %s,
                        notify_all = %s
                    WHERE id = %s
                """, (
                    event_datetime,
                    notify_before_minutes,
                    notify_roles,
                    notify_all,
                    UUID(str(event_id)),
                ))
            else:
                cur.execute("""
                    INSERT INTO checklist_scheduled_events (
                        id, template_item_id, event_datetime, notify_before_minutes,
                        notify_roles, notify_all, created_by, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    uuid4(),
                    item_id,
                    event_datetime,
                    notify_before_minutes,
                    notify_roles,
                    notify_all,
                    created_by,
                    datetime.now(timezone.utc),
                ))

        return ChecklistDBService._get_scheduled_events_for_item(cur, item_id)
    
    # =====================================================
    # INSTANCE MANAGEMENT
    # =====================================================
    
    @staticmethod
    def delete_checklist_instance(instance_id: UUID) -> bool:
        """Delete a checklist instance and all related data (cascade)"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        DELETE FROM checklist_instances WHERE id = %s
                    """, (instance_id,))
                    conn.commit()
                    log.info(f"✅ Checklist instance deleted: {instance_id}")
                    return cur.rowcount > 0
        except Exception as e:
            log.error(f"Failed to delete checklist instance {instance_id}: {e}")
            return False
    
    # =====================================================
    # TEMPLATE MANAGEMENT
    # =====================================================
    
    @staticmethod
    def get_template(template_id: UUID) -> Optional[dict]:
        """Get a checklist template by ID with all nested items and subitems"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, name, description, shift, is_active, version, 
                               created_by, created_at, section_id
                        FROM checklist_templates
                        WHERE id = %s
                    """, (template_id,))
                    
                    row = cur.fetchone()
                    if not row:
                        return None
                    
                    # Get template items with subitems (only active items)
                    cur.execute("""
                        SELECT id, template_id, title, description, item_type, is_required,
                               scheduled_time, notify_before_minutes, severity, sort_order, created_at
                        FROM checklist_template_items
                        WHERE template_id = %s AND is_active = true
                        ORDER BY sort_order
                    """, (template_id,))
                    
                    items = []
                    for item_row in cur.fetchall():
                        item_id = item_row[0]
                        
                        # Get subitems for this item
                        cur.execute("""
                            SELECT id, template_item_id, title, description, item_type, is_required,
                                   scheduled_time, notify_before_minutes, severity, sort_order, created_at
                            FROM checklist_template_subitems
                            WHERE template_item_id = %s
                            ORDER BY sort_order
                        """, (item_id,))
                        
                        subitems = []
                        for subitem_row in cur.fetchall():
                            subitems.append({
                                'id': str(subitem_row[0]),
                                'template_item_id': str(subitem_row[1]),
                                'title': subitem_row[2],
                                'description': subitem_row[3],
                                'item_type': subitem_row[4],
                                'is_required': subitem_row[5],
                                'scheduled_time': ChecklistDBService._serialize_time(subitem_row[6]),
                                'notify_before_minutes': subitem_row[7],
                                'severity': subitem_row[8],
                                'sort_order': subitem_row[9],
                                'created_at': ChecklistDBService._serialize_datetime(subitem_row[10])
                            })

                        scheduled_events = ChecklistDBService._get_scheduled_events_for_item(cur, item_id)
                        
                        items.append({
                            'id': str(item_row[0]),
                            'template_id': str(item_row[1]),
                            'title': item_row[2],
                            'description': item_row[3],
                            'item_type': item_row[4],
                            'is_required': item_row[5],
                            'scheduled_time': ChecklistDBService._serialize_time(item_row[6]),
                            'notify_before_minutes': item_row[7],
                            'severity': item_row[8],
                            'sort_order': item_row[9],
                            'created_at': ChecklistDBService._serialize_datetime(item_row[10]),
                            'subitems': subitems,
                            'scheduled_events': scheduled_events
                        })
                    
                    return {
                        'id': str(row[0]),
                        'name': row[1],
                        'description': row[2],
                        'shift': row[3],
                        'is_active': row[4],
                        'version': row[5],
                        'created_by': str(row[6]) if row[6] else None,
                        'created_at': row[7].isoformat() if row[7] else None,
                        'section_id': str(row[8]) if row[8] else None,
                        'items': items
                    }
        except Exception as e:
            log.error(f"Failed to get template {template_id}: {e}")
            return None
    
    @staticmethod
    def get_active_template_for_shift(shift: str, section_id: Optional[str] = None) -> Optional[dict]:
        """Get the active template for a given shift type, optionally scoped to a section."""
        try:
            shift = ChecklistDBService._normalize_shift_name(shift)
            with get_connection() as conn:
                with conn.cursor() as cur:
                    query = """
                        SELECT id FROM checklist_templates
                        WHERE shift = %s AND is_active = TRUE
                    """
                    params = [shift]
                    if section_id:
                        query += " AND section_id = %s"
                        params.append(section_id)
                    query += " ORDER BY version DESC LIMIT 1"

                    cur.execute(query, params)
                    
                    row = cur.fetchone()
                    if not row:
                        if section_id:
                            log.warning(f"No active template found for shift {shift} in section {section_id}")
                        else:
                            log.warning(f"No active template found for shift: {shift}")
                        return None
                    
                    template_id = row[0]
                    return ChecklistDBService.get_template(template_id)
        except Exception as e:
            log.error(f"Failed to get active template for shift {shift}: {e}")
            return None
    
    @staticmethod
    def update_template(
        template_id: UUID,
        name: Optional[str] = None,
        description: Optional[str] = None,
        shift: Optional[str] = None,
        is_active: Optional[bool] = None,
        section_id: Optional[str] = None
    ) -> bool:
        """Update template properties"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    updates = []
                    params = []
                    
                    if name is not None:
                        updates.append("name = %s")
                        params.append(name)
                    if description is not None:
                        updates.append("description = %s")
                        params.append(description)
                    if shift is not None:
                        updates.append("shift = %s")
                        params.append(shift)
                    if is_active is not None:
                        updates.append("is_active = %s")
                        params.append(is_active)
                    if section_id is not None:
                        updates.append("section_id = %s")
                        params.append(section_id)
                    
                    if not updates:
                        return True  # Nothing to update
                    
                    params.append(template_id)
                    query = f"UPDATE checklist_templates SET {', '.join(updates)} WHERE id = %s"
                    cur.execute(query, params)
                    conn.commit()
                    
                    log.info(f"✅ Template updated: {template_id}")
                    return cur.rowcount > 0
        
        except Exception as e:
            log.error(f"Failed to update template: {e}")
            raise
    
    @staticmethod
    def list_templates(shift: Optional[str] = None, active_only: bool = True, section_id: Optional[str] = None) -> List[dict]:
        """List templates, optionally filtered by shift"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    query = "SELECT id FROM checklist_templates WHERE 1=1"
                    params = []
                    
                    if active_only:
                        query += " AND is_active = TRUE"
                    
                    if shift:
                        query += " AND shift = %s"
                        params.append(shift)

                    # Section scoping: if provided, only return templates for that section
                    if section_id:
                        query += " AND section_id = %s"
                        params.append(section_id)
                    
                    query += " ORDER BY version DESC"
                    
                    cur.execute(query, params)
                    rows = cur.fetchall()
                    
                    templates = []
                    for (template_id,) in rows:
                        template = ChecklistDBService.get_template(template_id)
                        if template:
                            templates.append(template)
                    
                    return templates
        except Exception as e:
            log.error(f"Failed to list templates: {e}")
            return []
    
    # =====================================================
    # TEMPLATE CREATION & MODIFICATION
    # =====================================================
    
    @staticmethod
    def create_template(
        name: str,
        shift: str,
        description: Optional[str] = None,
        is_active: bool = True,
        created_by: Optional[UUID] = None,
        section_id: Optional[str] = None,
        items_data: Optional[List[dict]] = None
    ) -> Optional[dict]:
        """
        Create a new checklist template with items and subitems.
        
        items_data format: [
            {
                "title": "Item 1",
                "description": "...",
                "item_type": "ROUTINE",
                "is_required": true,
                "severity": 2,
                "sort_order": 0,
                "subitems": [
                    {
                        "title": "Subitem 1",
                        "item_type": "ROUTINE",
                        "severity": 1,
                        "sort_order": 0
                    }
                ]
            }
        ]
        """
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    ChecklistDBService._validate_template_items_against_shift(cur, shift, items_data)

                    # Create template
                    template_id = uuid4()
                    cur.execute("""
                        INSERT INTO checklist_templates (
                            id, name, description, shift, is_active, version,
                            created_by, created_at, section_id
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id, name, description, shift, is_active, version,
                                  created_by, created_at, section_id
                    """, (
                        template_id, name, description, shift, is_active, 1,
                        created_by, datetime.now(timezone.utc), section_id
                    ))
                    
                    template_row = cur.fetchone()
                    items = []
                    
                    # Add items and subitems
                    if items_data:
                        for item_data in items_data:
                            item_type = ChecklistDBService._normalize_item_type(item_data.get('item_type', 'ROUTINE')) or 'ROUTINE'
                            scheduled_time, notify_before_minutes = ChecklistDBService._normalize_schedule_fields(
                                item_type,
                                item_data.get('scheduled_time'),
                                item_data.get('notify_before_minutes'),
                            )
                            item_id = uuid4()
                            cur.execute("""
                                INSERT INTO checklist_template_items (
                                    id, template_id, title, description, item_type,
                                    is_required, scheduled_time, notify_before_minutes,
                                    severity, sort_order, created_at
                                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """, (
                                item_id,
                                template_id,
                                item_data.get('title'),
                                item_data.get('description'),
                                item_type,
                                item_data.get('is_required', True),
                                scheduled_time,
                                notify_before_minutes,
                                item_data.get('severity', 1),
                                item_data.get('sort_order', 0),
                                datetime.now(timezone.utc)
                            ))
                            
                            scheduled_events = ChecklistDBService._sync_scheduled_events(
                                cur,
                                item_id,
                                item_data.get('scheduled_events') if ChecklistDBService._normalize_item_type(item_type) == 'SCHEDULED_EVENT' else [],
                                created_by=created_by,
                            )

                            # Add subitems
                            subitems = []
                            if item_data.get('subitems'):
                                for subitem_data in item_data['subitems']:
                                    subitem_type = ChecklistDBService._normalize_item_type(subitem_data.get('item_type', 'ROUTINE')) or 'ROUTINE'
                                    subitem_scheduled_time, subitem_notify_before_minutes = ChecklistDBService._normalize_schedule_fields(
                                        subitem_type,
                                        subitem_data.get('scheduled_time'),
                                        subitem_data.get('notify_before_minutes'),
                                    )
                                    subitem_id = uuid4()
                                    cur.execute("""
                                        INSERT INTO checklist_template_subitems (
                                            id, template_item_id, title, description,
                                            item_type, is_required, scheduled_time,
                                            notify_before_minutes, severity, sort_order,
                                            created_at
                                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                    """, (
                                        subitem_id,
                                        item_id,
                                        subitem_data.get('title'),
                                        subitem_data.get('description'),
                                        subitem_type,
                                        subitem_data.get('is_required', True),
                                        subitem_scheduled_time,
                                        subitem_notify_before_minutes,
                                        subitem_data.get('severity', 1),
                                        subitem_data.get('sort_order', 0),
                                        datetime.now(timezone.utc)
                                    ))
                                    
                                    subitems.append({
                                        'id': str(subitem_id),
                                        'template_item_id': str(item_id),
                                        'title': subitem_data.get('title'),
                                        'description': subitem_data.get('description'),
                                        'item_type': subitem_type,
                                        'is_required': subitem_data.get('is_required', True),
                                        'scheduled_time': ChecklistDBService._serialize_time(subitem_scheduled_time),
                                        'notify_before_minutes': subitem_notify_before_minutes,
                                        'severity': subitem_data.get('severity', 1),
                                        'sort_order': subitem_data.get('sort_order', 0),
                                        'created_at': datetime.now(timezone.utc).isoformat()
                                    })
                            
                            items.append({
                                'id': str(item_id),
                                'template_id': str(template_id),
                                'title': item_data.get('title'),
                                'description': item_data.get('description'),
                                'item_type': item_type,
                                'is_required': item_data.get('is_required', True),
                                'scheduled_time': ChecklistDBService._serialize_time(scheduled_time),
                                'notify_before_minutes': notify_before_minutes,
                                'severity': item_data.get('severity', 1),
                                'sort_order': item_data.get('sort_order', 0),
                                'created_at': datetime.now(timezone.utc).isoformat(),
                                'subitems': subitems,
                                'scheduled_events': scheduled_events,
                            })
                    
                    conn.commit()
                    
                    log.info(f"✅ Template created: {template_id} ({shift} shift, {len(items)} items)")
                    
                    return {
                        'id': str(template_row[0]),
                        'name': template_row[1],
                        'description': template_row[2],
                        'shift': template_row[3],
                        'is_active': template_row[4],
                        'version': template_row[5],
                        'created_by': str(template_row[6]) if template_row[6] else None,
                        'created_at': template_row[7].isoformat() if template_row[7] else None,
                        'section_id': str(template_row[8]) if template_row[8] else None,
                        'items': items
                    }
        
        except Exception as e:
            log.error(f"Failed to create template: {e}")
            raise
    
    @staticmethod
    def add_template_item(
        template_id: UUID,
        title: str,
        description: Optional[str] = None,
        item_type: str = 'ROUTINE',
        is_required: bool = True,
        scheduled_time: Optional[time] = None,
        notify_before_minutes: Optional[int] = None,
        severity: int = 1,
        sort_order: int = 0,
        subitems_data: Optional[List[dict]] = None,
        scheduled_events_data: Optional[List[dict]] = None,
        created_by: Optional[UUID] = None,
    ) -> Optional[dict]:
        """Add a new item to a template with optional subitems"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT shift
                        FROM checklist_templates
                        WHERE id = %s
                    """, (template_id,))
                    template_row = cur.fetchone()
                    if not template_row:
                        raise ValueError(f"Template not found: {template_id}")

                    item_payload = [{
                        'title': title,
                        'item_type': item_type,
                        'scheduled_time': scheduled_time,
                        'subitems': subitems_data or [],
                    }]
                    ChecklistDBService._validate_template_items_against_shift(cur, template_row[0], item_payload)

                    item_type = ChecklistDBService._normalize_item_type(item_type) or 'ROUTINE'
                    scheduled_time, notify_before_minutes = ChecklistDBService._normalize_schedule_fields(
                        item_type,
                        scheduled_time,
                        notify_before_minutes,
                    )
                    item_id = uuid4()
                    cur.execute("""
                        INSERT INTO checklist_template_items (
                            id, template_id, title, description, item_type,
                            is_required, scheduled_time, notify_before_minutes,
                            severity, sort_order, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        item_id, template_id, title, description, item_type,
                        is_required, scheduled_time, notify_before_minutes,
                        severity, sort_order, datetime.now(timezone.utc)
                    ))
                    
                    scheduled_events = ChecklistDBService._sync_scheduled_events(
                        cur,
                        item_id,
                        scheduled_events_data if ChecklistDBService._normalize_item_type(item_type) == 'SCHEDULED_EVENT' else [],
                        created_by=created_by,
                    )

                    subitems = []
                    if subitems_data:
                        for subitem_data in subitems_data:
                            subitem_type = subitem_data.get('item_type', 'ROUTINE')
                            subitem_scheduled_time, subitem_notify_before_minutes = ChecklistDBService._normalize_schedule_fields(
                                subitem_type,
                                subitem_data.get('scheduled_time'),
                                subitem_data.get('notify_before_minutes'),
                            )
                            subitem_id = uuid4()
                            cur.execute("""
                                INSERT INTO checklist_template_subitems (
                                    id, template_item_id, title, description,
                                    item_type, is_required, scheduled_time,
                                    notify_before_minutes, severity, sort_order,
                                    created_at
                                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """, (
                                subitem_id, item_id,
                                subitem_data.get('title'),
                                subitem_data.get('description'),
                                subitem_type,
                                subitem_data.get('is_required', True),
                                subitem_scheduled_time,
                                subitem_notify_before_minutes,
                                subitem_data.get('severity', 1),
                                subitem_data.get('sort_order', 0),
                                datetime.now(timezone.utc)
                            ))
                            
                            subitems.append({
                                'id': str(subitem_id),
                                'template_item_id': str(item_id),
                                'title': subitem_data.get('title'),
                                'item_type': subitem_type,
                                'scheduled_time': ChecklistDBService._serialize_time(subitem_scheduled_time),
                                'notify_before_minutes': subitem_notify_before_minutes,
                                'severity': subitem_data.get('severity', 1),
                                'sort_order': subitem_data.get('sort_order', 0)
                            })
                    
                    conn.commit()
                    log.info(f"✅ Item added to template {template_id}: {item_id}")
                    
                    return {
                        'id': str(item_id),
                        'template_id': str(template_id),
                        'title': title,
                        'description': description,
                        'item_type': item_type,
                        'is_required': is_required,
                        'scheduled_time': ChecklistDBService._serialize_time(scheduled_time),
                        'notify_before_minutes': notify_before_minutes,
                        'severity': severity,
                        'sort_order': sort_order,
                        'subitems': subitems,
                        'scheduled_events': scheduled_events,
                    }
        except Exception as e:
            log.error(f"Failed to add item to template: {e}")
            raise
    
    @staticmethod
    def update_template_item(
        item_id: UUID,
        title: Optional[str] = None,
        description: Optional[str] = None,
        item_type: Optional[str] = None,
        is_required: Optional[bool] = None,
        scheduled_time: Optional[time] = None,
        notify_before_minutes: Optional[int] = None,
        severity: Optional[int] = None,
        sort_order: Optional[int] = None,
        subitems: Optional[List[dict]] = None,
        scheduled_events: Optional[List[dict]] = None,
        created_by: Optional[UUID] = None,
    ) -> bool:
        """Update a template item and optionally its subitems"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT cti.template_id, ct.shift, cti.title, cti.item_type,
                               cti.scheduled_time, cti.notify_before_minutes
                        FROM checklist_template_items cti
                        JOIN checklist_templates ct ON ct.id = cti.template_id
                        WHERE cti.id = %s
                    """, (item_id,))
                    current_item_row = cur.fetchone()
                    if not current_item_row:
                        raise ValueError(f"Template item not found: {item_id}")

                    _, template_shift, current_title, current_item_type, current_scheduled_time, current_notify_before_minutes = current_item_row
                    effective_title = title if title is not None else current_title
                    effective_item_type = item_type if item_type is not None else current_item_type
                    effective_scheduled_time = scheduled_time if (scheduled_time is not None or item_type is not None) else current_scheduled_time
                    effective_notify_before_minutes = (
                        notify_before_minutes
                        if (notify_before_minutes is not None or item_type is not None)
                        else current_notify_before_minutes
                    )
                    normalized_scheduled_time, normalized_notify_before_minutes = ChecklistDBService._normalize_schedule_fields(
                        effective_item_type,
                        effective_scheduled_time,
                        effective_notify_before_minutes,
                    )

                    cur.execute("""
                        SELECT id, title, item_type, scheduled_time, notify_before_minutes
                        FROM checklist_template_subitems
                        WHERE template_item_id = %s
                    """, (item_id,))
                    existing_subitems = {
                        str(row[0]): {
                            'title': row[1],
                            'item_type': row[2],
                            'scheduled_time': row[3],
                            'notify_before_minutes': row[4],
                        }
                        for row in cur.fetchall()
                    }

                    validation_subitems = []
                    for subitem_data in subitems or []:
                        existing_subitem = existing_subitems.get(str(subitem_data.get('id')))
                        subitem_type_value = ChecklistDBService._normalize_item_type(
                            subitem_data.get('item_type')
                            if subitem_data.get('item_type') is not None
                            else (existing_subitem or {}).get('item_type', 'ROUTINE')
                        ) or 'ROUTINE'
                        subitem_scheduled_time_value = (
                            subitem_data.get('scheduled_time')
                            if ('scheduled_time' in subitem_data or subitem_data.get('item_type') is not None)
                            else (existing_subitem or {}).get('scheduled_time')
                        )
                        subitem_notify_value = (
                            subitem_data.get('notify_before_minutes')
                            if ('notify_before_minutes' in subitem_data or subitem_data.get('item_type') is not None)
                            else (existing_subitem or {}).get('notify_before_minutes')
                        )
                        normalized_subitem_scheduled_time, _ = ChecklistDBService._normalize_schedule_fields(
                            subitem_type_value,
                            subitem_scheduled_time_value,
                            subitem_notify_value,
                        )
                        validation_subitems.append({
                            'title': subitem_data.get('title') or (existing_subitem or {}).get('title'),
                            'item_type': subitem_type_value,
                            'scheduled_time': normalized_subitem_scheduled_time,
                        })

                    ChecklistDBService._validate_template_items_against_shift(cur, template_shift, [{
                        'title': effective_title,
                        'item_type': effective_item_type,
                        'scheduled_time': normalized_scheduled_time,
                        'subitems': validation_subitems,
                    }])

                    # Build update query dynamically
                    updates = []
                    params = []
                    
                    if title is not None:
                        updates.append("title = %s")
                        params.append(title)
                    if description is not None:
                        updates.append("description = %s")
                        params.append(description)
                    if item_type is not None:
                        updates.append("item_type = %s")
                        params.append(ChecklistDBService._normalize_item_type(item_type))
                    if is_required is not None:
                        updates.append("is_required = %s")
                        params.append(is_required)
                    if scheduled_time is not None or item_type is not None:
                        updates.append("scheduled_time = %s")
                        params.append(normalized_scheduled_time)
                    if notify_before_minutes is not None or item_type is not None:
                        updates.append("notify_before_minutes = %s")
                        params.append(normalized_notify_before_minutes)
                    if severity is not None:
                        updates.append("severity = %s")
                        params.append(severity)
                    if sort_order is not None:
                        updates.append("sort_order = %s")
                        params.append(sort_order)
                    
                    if not updates and subitems is None and scheduled_events is None:
                        return True  # Nothing to update
                    
                    # Update item properties
                    if updates:
                        params.append(item_id)
                        query = f"UPDATE checklist_template_items SET {', '.join(updates)} WHERE id = %s"
                        cur.execute(query, params)

                    events_payload = scheduled_events
                    if ChecklistDBService._normalize_item_type(effective_item_type) != 'SCHEDULED_EVENT':
                        events_payload = [] if (scheduled_events is not None or item_type is not None) else None

                    if events_payload is not None:
                        ChecklistDBService._sync_scheduled_events(
                            cur,
                            item_id,
                            events_payload,
                            created_by=created_by,
                        )
                    
                    # Handle subitems update if provided
                    if subitems is not None:
                        # Process subitems by ID
                        for subitem_data in subitems:
                            existing_subitem = existing_subitems.get(str(subitem_data.get('id')))
                            subitem_type_value = ChecklistDBService._normalize_item_type(
                                subitem_data.get('item_type')
                                if subitem_data.get('item_type') is not None
                                else (existing_subitem or {}).get('item_type', 'ROUTINE')
                            ) or 'ROUTINE'
                            subitem_scheduled_time_value = (
                                subitem_data.get('scheduled_time')
                                if ('scheduled_time' in subitem_data or subitem_data.get('item_type') is not None)
                                else (existing_subitem or {}).get('scheduled_time')
                            )
                            subitem_notify_value = (
                                subitem_data.get('notify_before_minutes')
                                if ('notify_before_minutes' in subitem_data or subitem_data.get('item_type') is not None)
                                else (existing_subitem or {}).get('notify_before_minutes')
                            )
                            normalized_subitem_scheduled_time, normalized_subitem_notify = ChecklistDBService._normalize_schedule_fields(
                                subitem_type_value,
                                subitem_scheduled_time_value,
                                subitem_notify_value,
                            )

                            if subitem_data.get('id') and str(subitem_data['id']) in existing_subitems:
                                # Update existing subitem
                                subitem_id = ChecklistDBService._coerce_uuid(subitem_data['id'])
                                updates = []
                                params = []
                                
                                if subitem_data.get('title') is not None:
                                    updates.append("title = %s")
                                    params.append(subitem_data['title'])
                                if subitem_data.get('description') is not None:
                                    updates.append("description = %s")
                                    params.append(subitem_data['description'])
                                if subitem_data.get('item_type') is not None:
                                    updates.append("item_type = %s")
                                    params.append(ChecklistDBService._normalize_item_type(subitem_data['item_type']))
                                if subitem_data.get('is_required') is not None:
                                    updates.append("is_required = %s")
                                    params.append(subitem_data['is_required'])
                                if 'scheduled_time' in subitem_data or subitem_data.get('item_type') is not None:
                                    updates.append("scheduled_time = %s")
                                    params.append(normalized_subitem_scheduled_time)
                                if 'notify_before_minutes' in subitem_data or subitem_data.get('item_type') is not None:
                                    updates.append("notify_before_minutes = %s")
                                    params.append(normalized_subitem_notify)
                                if subitem_data.get('severity') is not None:
                                    updates.append("severity = %s")
                                    params.append(subitem_data['severity'])
                                if subitem_data.get('sort_order') is not None:
                                    updates.append("sort_order = %s")
                                    params.append(subitem_data['sort_order'])
                                
                                if updates:
                                    params.append(subitem_id)
                                    query = f"UPDATE checklist_template_subitems SET {', '.join(updates)} WHERE id = %s"
                                    cur.execute(query, params)
                            else:
                                # Create new subitem
                                subitem_id = uuid4()
                                cur.execute("""
                                    INSERT INTO checklist_template_subitems (
                                        id, template_item_id, title, description,
                                        item_type, is_required, scheduled_time,
                                        notify_before_minutes, severity, sort_order,
                                        created_at
                                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                """, (
                                    subitem_id,
                                    item_id,
                                    subitem_data.get('title'),
                                    subitem_data.get('description'),
                                    subitem_type_value,
                                    subitem_data.get('is_required', True),
                                    normalized_subitem_scheduled_time,
                                    normalized_subitem_notify,
                                    subitem_data.get('severity', 1),
                                    subitem_data.get('sort_order', 0),
                                    datetime.now(timezone.utc)
                                ))
                        
                        # Note: Subitems not mentioned in the request are left unchanged
                    
                    conn.commit()
                    
                    log.info(f"✅ Item updated: {item_id}" + (" with subitems" if subitems is not None else ""))
                    return cur.rowcount > 0 or subitems is not None
        
        except Exception as e:
            log.error(f"Failed to update item: {e}")
            raise
    
    @staticmethod
    def delete_template_item(item_id: UUID) -> bool:
        """Delete a template item (cascades to subitems)"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Subitems should cascade delete due to FK constraint
                    cur.execute("""
                        DELETE FROM checklist_template_items WHERE id = %s
                    """, (item_id,))
                    
                    conn.commit()
                    log.info(f"✅ Item deleted: {item_id}")
                    return cur.rowcount > 0
        
        except Exception as e:
            log.error(f"Failed to delete item: {e}")
            raise
    
    @staticmethod
    def soft_delete_template_item(item_id: UUID) -> bool:
        """Soft delete a template item by setting is_active to false"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE checklist_template_items 
                        SET is_active = false 
                        WHERE id = %s
                    """, (item_id,))
                    
                    conn.commit()
                    log.info(f"✅ Item soft deleted: {item_id}")
                    return cur.rowcount > 0
        
        except Exception as e:
            log.error(f"Failed to soft delete item: {e}")
            raise
    
    @staticmethod
    def add_template_subitem(
        item_id: UUID,
        title: str,
        description: Optional[str] = None,
        item_type: str = 'ROUTINE',
        is_required: bool = True,
        scheduled_time: Optional[time] = None,
        notify_before_minutes: Optional[int] = None,
        severity: int = 1,
        sort_order: int = 0
    ) -> Optional[dict]:
        """Add a subitem to a template item"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT ct.shift, cti.title
                        FROM checklist_template_items cti
                        JOIN checklist_templates ct ON ct.id = cti.template_id
                        WHERE cti.id = %s
                    """, (item_id,))
                    item_row = cur.fetchone()
                    if not item_row:
                        raise ValueError(f"Template item not found: {item_id}")

                    ChecklistDBService._validate_template_items_against_shift(cur, item_row[0], [{
                        'title': item_row[1],
                        'subitems': [{
                            'title': title,
                            'item_type': item_type,
                            'scheduled_time': scheduled_time,
                        }],
                    }])

                    item_type = ChecklistDBService._normalize_item_type(item_type) or 'ROUTINE'
                    scheduled_time, notify_before_minutes = ChecklistDBService._normalize_schedule_fields(
                        item_type,
                        scheduled_time,
                        notify_before_minutes,
                    )
                    subitem_id = uuid4()
                    cur.execute("""
                        INSERT INTO checklist_template_subitems (
                            id, template_item_id, title, description,
                            item_type, is_required, scheduled_time,
                            notify_before_minutes, severity, sort_order,
                            created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        subitem_id, item_id, title, description, item_type,
                        is_required, scheduled_time, notify_before_minutes, severity, sort_order, datetime.now(timezone.utc)
                    ))
                    
                    conn.commit()
                    log.info(f"✅ Subitem added to item {item_id}: {subitem_id}")
                    
                    return {
                        'id': str(subitem_id),
                        'template_item_id': str(item_id),
                        'title': title,
                        'description': description,
                        'item_type': item_type,
                        'is_required': is_required,
                        'scheduled_time': ChecklistDBService._serialize_time(scheduled_time),
                        'notify_before_minutes': notify_before_minutes,
                        'severity': severity,
                        'sort_order': sort_order,
                        'created_at': datetime.now(timezone.utc).isoformat()
                    }
        except Exception as e:
            log.error(f"Failed to add subitem: {e}")
            raise
    
    @staticmethod
    def update_template_subitem(
        subitem_id: UUID,
        title: Optional[str] = None,
        description: Optional[str] = None,
        item_type: Optional[str] = None,
        is_required: Optional[bool] = None,
        scheduled_time: Optional[time] = None,
        notify_before_minutes: Optional[int] = None,
        severity: Optional[int] = None,
        sort_order: Optional[int] = None
    ) -> bool:
        """Update a template subitem"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT ct.shift, cti.title, cts.title, cts.item_type,
                               cts.scheduled_time, cts.notify_before_minutes
                        FROM checklist_template_subitems cts
                        JOIN checklist_template_items cti ON cti.id = cts.template_item_id
                        JOIN checklist_templates ct ON ct.id = cti.template_id
                        WHERE cts.id = %s
                    """, (subitem_id,))
                    current_subitem_row = cur.fetchone()
                    if not current_subitem_row:
                        raise ValueError(f"Template subitem not found: {subitem_id}")

                    template_shift, parent_title, current_title, current_item_type, current_scheduled_time, current_notify_before_minutes = current_subitem_row
                    effective_title = title if title is not None else current_title
                    effective_item_type = item_type if item_type is not None else current_item_type
                    effective_scheduled_time = scheduled_time if (scheduled_time is not None or item_type is not None) else current_scheduled_time
                    effective_notify_before_minutes = (
                        notify_before_minutes
                        if (notify_before_minutes is not None or item_type is not None)
                        else current_notify_before_minutes
                    )
                    normalized_scheduled_time, normalized_notify_before_minutes = ChecklistDBService._normalize_schedule_fields(
                        effective_item_type,
                        effective_scheduled_time,
                        effective_notify_before_minutes,
                    )
                    ChecklistDBService._validate_template_items_against_shift(cur, template_shift, [{
                        'title': parent_title,
                        'subitems': [{
                            'title': effective_title,
                            'item_type': effective_item_type,
                            'scheduled_time': normalized_scheduled_time,
                        }],
                    }])

                    updates = []
                    params = []
                    
                    if title is not None:
                        updates.append("title = %s")
                        params.append(title)
                    if description is not None:
                        updates.append("description = %s")
                        params.append(description)
                    if item_type is not None:
                        updates.append("item_type = %s")
                        params.append(ChecklistDBService._normalize_item_type(item_type))
                    if is_required is not None:
                        updates.append("is_required = %s")
                        params.append(is_required)
                    if scheduled_time is not None or item_type is not None:
                        updates.append("scheduled_time = %s")
                        params.append(normalized_scheduled_time)
                    if notify_before_minutes is not None or item_type is not None:
                        updates.append("notify_before_minutes = %s")
                        params.append(normalized_notify_before_minutes)
                    if severity is not None:
                        updates.append("severity = %s")
                        params.append(severity)
                    if sort_order is not None:
                        updates.append("sort_order = %s")
                        params.append(sort_order)
                    
                    if not updates:
                        return True
                    
                    params.append(subitem_id)
                    query = f"UPDATE checklist_template_subitems SET {', '.join(updates)} WHERE id = %s"
                    
                    cur.execute(query, params)
                    conn.commit()
                    
                    log.info(f"✅ Subitem updated: {subitem_id}")
                    return cur.rowcount > 0
        
        except Exception as e:
            log.error(f"Failed to update subitem: {e}")
            raise
    
    @staticmethod
    def delete_template_subitem(subitem_id: UUID) -> bool:
        """Delete a template subitem"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        DELETE FROM checklist_template_subitems WHERE id = %s
                    """, (subitem_id,))
                    
                    conn.commit()
                    log.info(f"✅ Subitem deleted: {subitem_id}")
                    return cur.rowcount > 0
        
        except Exception as e:
            log.error(f"Failed to delete subitem: {e}")
            raise
    
    @staticmethod
    def delete_all_template_items(template_id: UUID) -> bool:
        """Delete all items and subitems for a template"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Subitems should cascade delete due to FK constraint
                    cur.execute("""
                        DELETE FROM checklist_template_items WHERE template_id = %s
                    """, (template_id,))
                    
                    conn.commit()
                    log.info(f"✅ All items deleted for template: {template_id}")
                    return True
        
        except Exception as e:
            log.error(f"Failed to delete all template items: {e}")
            raise
    
    @staticmethod
    def create_template_items(template_id: UUID, items_data: List[dict]) -> None:
        """Create items and subitems for a template (separate from template creation)"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT shift
                        FROM checklist_templates
                        WHERE id = %s
                    """, (template_id,))
                    template_row = cur.fetchone()
                    if not template_row:
                        raise ValueError(f"Template not found: {template_id}")

                    ChecklistDBService._validate_template_items_against_shift(cur, template_row[0], items_data)

                    # Add items and subitems
                    for item_data in items_data:
                        item_type = ChecklistDBService._normalize_item_type(item_data.get('item_type', 'ROUTINE')) or 'ROUTINE'
                        scheduled_time, notify_before_minutes = ChecklistDBService._normalize_schedule_fields(
                            item_type,
                            item_data.get('scheduled_time'),
                            item_data.get('notify_before_minutes'),
                        )
                        item_id = uuid4()
                        cur.execute("""
                            INSERT INTO checklist_template_items (
                                id, template_id, title, description, item_type,
                                is_required, scheduled_time, notify_before_minutes,
                                severity, sort_order, created_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            item_id,
                            template_id,
                            item_data.get('title'),
                            item_data.get('description'),
                            item_type,
                            item_data.get('is_required', True),
                            scheduled_time,
                            notify_before_minutes,
                            item_data.get('severity', 1),
                            item_data.get('sort_order', 0),
                            datetime.now(timezone.utc)
                        ))
                        
                        # Add subitems
                        if item_data.get('subitems'):
                            for subitem_data in item_data['subitems']:
                                subitem_type = ChecklistDBService._normalize_item_type(subitem_data.get('item_type', 'ROUTINE')) or 'ROUTINE'
                                subitem_scheduled_time, subitem_notify_before_minutes = ChecklistDBService._normalize_schedule_fields(
                                    subitem_type,
                                    subitem_data.get('scheduled_time'),
                                    subitem_data.get('notify_before_minutes'),
                                )
                                subitem_id = uuid4()
                                cur.execute("""
                                    INSERT INTO checklist_template_subitems (
                                        id, template_item_id, title, description,
                                        item_type, is_required, scheduled_time,
                                        notify_before_minutes, severity, sort_order,
                                        created_at
                                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                """, (
                                    subitem_id,
                                    item_id,
                                    subitem_data.get('title'),
                                    subitem_data.get('description'),
                                    subitem_type,
                                    subitem_data.get('is_required', True),
                                    subitem_scheduled_time,
                                    subitem_notify_before_minutes,
                                    subitem_data.get('severity', 1),
                                    subitem_data.get('sort_order', 0),
                                    datetime.now(timezone.utc)
                                ))

                        ChecklistDBService._sync_scheduled_events(
                            cur,
                            item_id,
                            item_data.get('scheduled_events') if ChecklistDBService._normalize_item_type(item_type) == 'SCHEDULED_EVENT' else [],
                        )
                    
                    conn.commit()
                    log.info(f"✅ Created {len(items_data)} items for template {template_id}")
        
        except Exception as e:
            log.error(f"Failed to create template items: {e}")
            raise
    
    @staticmethod
    def duplicate_template(
        template_id: UUID,
        new_name: str,
        created_by: Optional[UUID] = None,
        new_version: int = 1
    ) -> Optional[dict]:
        """Duplicate a template with all its items and subitems"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Get source template
                    cur.execute("""
                        SELECT name, description, shift, is_active, section_id
                        FROM checklist_templates WHERE id = %s
                    """, (template_id,))
                    
                    row = cur.fetchone()
                    if not row:
                        return None
                    
                    _, description, shift, is_active, section_id = row
                    
                    # Create new template
                    new_template_id = uuid4()
                    cur.execute("""
                        INSERT INTO checklist_templates (
                            id, name, description, shift, is_active, version,
                            created_by, created_at, section_id
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        new_template_id, new_name, description, shift, is_active,
                        new_version, created_by, datetime.now(timezone.utc), section_id
                    ))
                    
                    # Copy items and subitems (only active items)
                    cur.execute("""
                        SELECT id FROM checklist_template_items
                        WHERE template_id = %s AND is_active = true ORDER BY sort_order
                    """, (template_id,))
                    
                    item_mapping = {}  # old_id -> new_id
                    
                    for (old_item_id,) in cur.fetchall():
                        # Get old item details
                        cur.execute("""
                            SELECT title, description, item_type, is_required,
                                   scheduled_time, notify_before_minutes, severity, sort_order
                            FROM checklist_template_items WHERE id = %s
                        """, (old_item_id,))
                        
                        item_row = cur.fetchone()
                        new_item_id = uuid4()
                        
                        cur.execute("""
                            INSERT INTO checklist_template_items (
                                id, template_id, title, description, item_type,
                                is_required, scheduled_time, notify_before_minutes,
                                severity, sort_order, created_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            new_item_id, new_template_id, item_row[0], item_row[1],
                            item_row[2], item_row[3], item_row[4], item_row[5],
                            item_row[6], item_row[7], datetime.now(timezone.utc)
                        ))
                        
                        item_mapping[old_item_id] = new_item_id
                        
                        # Copy subitems
                        cur.execute("""
                            SELECT title, description, item_type, is_required,
                                   scheduled_time, notify_before_minutes, severity, sort_order
                            FROM checklist_template_subitems
                            WHERE template_item_id = %s ORDER BY sort_order
                        """, (old_item_id,))
                        
                        for (s_title, s_desc, s_type, s_required, s_time, s_notify, s_severity, s_order) in cur.fetchall():
                            cur.execute("""
                                INSERT INTO checklist_template_subitems (
                                    id, template_item_id, title, description,
                                    item_type, is_required, scheduled_time,
                                    notify_before_minutes, severity, sort_order,
                                    created_at
                                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """, (
                                uuid4(), new_item_id, s_title, s_desc, s_type,
                                s_required, s_time, s_notify, s_severity, s_order, datetime.now(timezone.utc)
                            ))

                        ChecklistDBService._sync_scheduled_events(
                            cur,
                            new_item_id,
                            ChecklistDBService._get_scheduled_events_for_item(cur, old_item_id),
                            created_by=created_by,
                        )
                    
                    conn.commit()
                    
                    # Return the new template
                    return ChecklistDBService.get_template(new_template_id)
        
        except Exception as e:
            log.error(f"Failed to duplicate template: {e}")
            raise
    
    # =====================================================
    # INSTANCE MANAGEMENT
    # =====================================================
    
    @staticmethod
    def create_checklist_instance(
        checklist_date: date,
        shift: str,
        created_by: UUID,
        created_by_username: str,
        template_id: Optional[UUID] = None,
        section_id: Optional[str] = None
    ) -> Optional[dict]:
        """
        Create a new checklist instance
        Uses active template for shift if template_id not provided
        """
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    shift = ChecklistDBService._normalize_shift_name(shift)
                    resolved_section_id = str(section_id) if section_id else None

                    # Get template (use provided or get active)
                    if template_id:
                        cur.execute(
                            """
                            SELECT id, section_id
                            FROM checklist_templates
                            WHERE id = %s
                            """,
                            (template_id,),
                        )
                        row = cur.fetchone()
                        if not row:
                            raise ValueError(f"Template {template_id} not found")
                        template_id = UUID(str(row[0]))
                        template_section_id = str(row[1]) if row[1] else None
                        if not template_section_id:
                            raise ValueError(f"Template {template_id} is missing a section_id")
                        if resolved_section_id and resolved_section_id != template_section_id:
                            raise ValueError("Checklist instance section must match the template section")
                        resolved_section_id = template_section_id
                    else:
                        if not resolved_section_id:
                            raise ValueError("section_id is required when template_id is not provided")

                        cur.execute(
                            """
                            SELECT id FROM checklist_templates
                            WHERE shift = %s
                              AND is_active = TRUE
                              AND section_id = %s
                            ORDER BY version DESC
                            LIMIT 1
                            """,
                            (shift, resolved_section_id),
                        )

                        row = cur.fetchone()
                        if not row:
                            raise ValueError(
                                f"No active template found for shift {shift} in section {resolved_section_id}"
                            )
                        template_id = UUID(str(row[0]))

                    _, start_time, end_time = ChecklistDBService._get_shift_window(cur, shift)
                    shift_start, shift_end = ChecklistDBService._resolve_shift_datetimes(
                        checklist_date,
                        start_time,
                        end_time,
                    )
                    
                    # Check if instance already exists for this template, date, and shift
                    cur.execute("""
                        SELECT id FROM checklist_instances 
                        WHERE template_id = %s AND checklist_date = %s AND shift = %s
                    """, (template_id, checklist_date, shift))
                    
                    existing_instance = cur.fetchone()
                    if existing_instance:
                        log.info(f"Instance already exists for template {template_id} on {checklist_date} {shift} shift")
                        existing_id = existing_instance[0]
                        instance_data = ChecklistDBService.get_instance(existing_id)
                        if instance_data:
                            return {
                                'id': existing_id,
                                'message': 'Existing instance returned',
                                'instance': instance_data
                            }
                        else:
                            raise ValueError(f"Failed to retrieve existing instance {existing_id}")

                    cur.execute("""
                        SELECT id, title, description, item_type, is_required,
                               scheduled_time, notify_before_minutes, severity, sort_order
                        FROM checklist_template_items
                        WHERE template_id = %s AND is_active = true
                        ORDER BY sort_order
                    """, (template_id,))

                    template_item_snapshots = []
                    for template_item_row in cur.fetchall():
                        template_item_id = template_item_row[0]
                        item_snapshot = {
                            'id': template_item_id,
                            'title': template_item_row[1],
                            'description': template_item_row[2],
                            'item_type': template_item_row[3],
                            'is_required': template_item_row[4],
                            'scheduled_time': template_item_row[5],
                            'notify_before_minutes': template_item_row[6],
                            'severity': template_item_row[7],
                            'sort_order': template_item_row[8],
                            'subitems': [],
                            'scheduled_events': ChecklistDBService._get_scheduled_events_for_item(cur, template_item_id),
                        }

                        if ChecklistDBService._normalize_item_type(item_snapshot['item_type']) == 'TIMED' and not item_snapshot['scheduled_time']:
                            log.warning(
                                "Timed template item %s on template %s has no scheduled_time; allowing instance creation without a reminder snapshot",
                                item_snapshot['title'],
                                template_id,
                            )
                        else:
                            ChecklistDBService._validate_timed_entry(
                                label=item_snapshot['title'],
                                shift=shift,
                                shift_start_time=start_time,
                                shift_end_time=end_time,
                                item_type=item_snapshot['item_type'],
                                scheduled_time=item_snapshot['scheduled_time'],
                            )

                        cur.execute("""
                            SELECT title, description, item_type, is_required,
                                   scheduled_time, notify_before_minutes, severity, sort_order
                            FROM checklist_template_subitems
                            WHERE template_item_id = %s
                            ORDER BY sort_order
                        """, (template_item_id,))

                        for subitem_row in cur.fetchall():
                            subitem_snapshot = {
                                'title': subitem_row[0],
                                'description': subitem_row[1],
                                'item_type': subitem_row[2],
                                'is_required': subitem_row[3],
                                'scheduled_time': subitem_row[4],
                                'notify_before_minutes': subitem_row[5],
                                'severity': subitem_row[6],
                                'sort_order': subitem_row[7],
                            }
                            if ChecklistDBService._normalize_item_type(subitem_snapshot['item_type']) == 'TIMED' and not subitem_snapshot['scheduled_time']:
                                log.warning(
                                    "Timed template subitem %s / %s on template %s has no scheduled_time; allowing instance creation without a reminder snapshot",
                                    item_snapshot['title'],
                                    subitem_snapshot['title'],
                                    template_id,
                                )
                            else:
                                ChecklistDBService._validate_timed_entry(
                                    label=f"{item_snapshot['title']} / {subitem_snapshot['title']}",
                                    shift=shift,
                                    shift_start_time=start_time,
                                    shift_end_time=end_time,
                                    item_type=subitem_snapshot['item_type'],
                                    scheduled_time=subitem_snapshot['scheduled_time'],
                                )
                            item_snapshot['subitems'].append(subitem_snapshot)

                        template_item_snapshots.append(item_snapshot)

                    ChecklistDBService._enforce_instance_initialization_window(
                        checklist_date,
                        shift_start,
                        shift_end,
                        template_item_snapshots,
                    )
                    
                    # Create instance
                    instance_id = uuid4()
                    cur.execute("""
                        INSERT INTO checklist_instances (
                            id, template_id, checklist_date, shift,
                            shift_start, shift_end, status,
                            created_by, created_at, section_id
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        ) RETURNING id, template_id, checklist_date, shift,
                                  shift_start, shift_end, status, created_by, created_at, section_id
                    """, (
                        instance_id, template_id, checklist_date, shift,
                        shift_start, shift_end, 'OPEN',
                        created_by, datetime.now(timezone.utc), resolved_section_id
                    ))
                    
                    instance_row = cur.fetchone()
                    
                    # Populate instance items from frozen template timing snapshot
                    for template_item_snapshot in template_item_snapshots:
                        template_item_id = template_item_snapshot['id']
                        item_instance_id = uuid4()
                        item_scheduled_time, item_notify_before_minutes = ChecklistDBService._normalize_schedule_fields(
                            template_item_snapshot['item_type'],
                            template_item_snapshot['scheduled_time'],
                            template_item_snapshot['notify_before_minutes'],
                        )
                        item_scheduled_at, item_remind_at = ChecklistDBService._resolve_reminder_snapshot(
                            checklist_date,
                            item_scheduled_time,
                            item_notify_before_minutes,
                            shift_start,
                            shift_end,
                        )
                        cur.execute("""
                            INSERT INTO checklist_instance_items (
                                id, instance_id, template_item_id, status,
                                scheduled_time, notify_before_minutes, scheduled_at, remind_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            item_instance_id,
                            instance_id,
                            template_item_id,
                            'PENDING',
                            item_scheduled_time,
                            item_notify_before_minutes,
                            item_scheduled_at,
                            item_remind_at,
                        ))

                        for subitem_snapshot in template_item_snapshot['subitems']:
                            subitem_scheduled_time, subitem_notify_before_minutes = ChecklistDBService._normalize_schedule_fields(
                                subitem_snapshot['item_type'],
                                subitem_snapshot['scheduled_time'],
                                subitem_snapshot['notify_before_minutes'],
                            )
                            subitem_scheduled_at, subitem_remind_at = ChecklistDBService._resolve_reminder_snapshot(
                                checklist_date,
                                subitem_scheduled_time,
                                subitem_notify_before_minutes,
                                shift_start,
                                shift_end,
                            )
                            cur.execute("""
                                INSERT INTO checklist_instance_subitems (
                                    instance_item_id, title, description, item_type,
                                    is_required, severity, sort_order, status,
                                    scheduled_time, notify_before_minutes, scheduled_at, remind_at
                                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """, (
                                item_instance_id,
                                subitem_snapshot['title'],
                                subitem_snapshot['description'],
                                subitem_snapshot['item_type'],
                                subitem_snapshot['is_required'],
                                subitem_snapshot['severity'],
                                subitem_snapshot['sort_order'],
                                'PENDING',
                                subitem_scheduled_time,
                                subitem_notify_before_minutes,
                                subitem_scheduled_at,
                                subitem_remind_at,
                            ))

                        for scheduled_event in template_item_snapshot['scheduled_events']:
                            event_datetime = scheduled_event.get('event_datetime')
                            if isinstance(event_datetime, str):
                                event_datetime = datetime.fromisoformat(event_datetime)
                            if event_datetime is None:
                                continue

                            remind_at = event_datetime - timedelta(minutes=int(scheduled_event.get('notify_before_minutes') or 0))
                            cur.execute("""
                                INSERT INTO checklist_instance_scheduled_events (
                                    id, instance_item_id, template_event_id, event_datetime,
                                    notify_before_minutes, remind_at, created_at
                                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """, (
                                uuid4(),
                                item_instance_id,
                                ChecklistDBService._coerce_uuid(scheduled_event.get('id')),
                                event_datetime,
                                scheduled_event.get('notify_before_minutes', 30),
                                remind_at,
                                datetime.now(timezone.utc),
                            ))
                        
                        # Copy subitems from template to instance
                        try:
                            cur.execute("""
                                INSERT INTO checklist_instance_subitems (
                                    instance_item_id, title, description, item_type,
                                    is_required, severity, sort_order, status
                                )
                                SELECT %s, title, description, item_type, is_required,
                                       severity, sort_order, 'PENDING'
                                FROM checklist_template_subitems
                                WHERE 1 = 0 AND template_item_id = %s
                                ORDER BY sort_order
                            """, (item_instance_id, template_item_id))
                            
                            subitem_count = cur.rowcount
                            if subitem_count > 0:
                                log.info(f"📦 Copied {subitem_count} subitems for instance item {item_instance_id}")
                        except Exception as e:
                            log.warning(f"⚠️  Failed to copy subitems for template item {template_item_id}: {e}")
                            # Don't fail the whole checklist creation if subitems copy fails
                    
                    # CRITICAL FIX: Auto-populate participants from scheduled_shifts
                    # When a checklist instance is created for a date/shift, automatically add all users
                    # who are scheduled to work that shift on that date
                    try:
                        # Get shift_id for this shift name from the shifts table
                        cur.execute("""
                            SELECT id FROM shifts WHERE UPPER(name) = %s LIMIT 1
                        """, (shift,))
                        
                        shift_row = cur.fetchone()
                        if shift_row:
                            shift_id = shift_row[0]
                            
                            # Query all users scheduled for this shift on this date
                            cur.execute("""
                                SELECT DISTINCT ss.user_id
                                FROM scheduled_shifts ss
                                WHERE ss.date = %s AND ss.shift_id = %s
                                AND ss.status != 'CANCELLED'
                            """, (checklist_date, shift_id))
                            
                            scheduled_users = cur.fetchall()
                            
                            # Add all scheduled users as participants (auto-populating the team)
                            for (user_id,) in scheduled_users:
                                try:
                                    cur.execute("""
                                        INSERT INTO checklist_participants (instance_id, user_id)
                                        VALUES (%s, %s)
                                        ON CONFLICT DO NOTHING
                                    """, (instance_id, user_id))
                                except Exception as participant_error:
                                    log.warning(f"Failed to add participant {user_id}: {participant_error}")
                            
                            if scheduled_users:
                                log.info(f"✨ Auto-populated {len(scheduled_users)} scheduled shift participants for instance {instance_id}")
                    except Exception as e:
                        log.warning(f"⚠️  Failed to auto-populate scheduled shift participants: {e}")
                        # Don't fail the whole checklist creation if this step fails
                        pass
                    
                    conn.commit()
                    
                    # Log event
                    OpsEventLogger.log_checklist_created(
                        instance_id=instance_id,
                        checklist_date=str(checklist_date),
                        shift=shift,
                        template_id=template_id,
                        created_by=created_by,
                        created_by_username=created_by_username
                    )
                    
                    log.info(f"✅ Checklist instance created: {instance_id} ({shift} shift on {checklist_date})")
                    
                    instance_data = ChecklistDBService.get_instance(instance_id)
                    if instance_data:
                        return {
                            'id': instance_id,
                            'message': 'New instance created',
                            'instance': instance_data
                        }
                    else:
                        raise ValueError(f"Failed to retrieve newly created instance {instance_id}")
        
        except Exception as e:
            log.error(f"Failed to create checklist instance: {e}")
            raise
    
    @staticmethod
    def get_instance(instance_id: UUID) -> Optional[dict]:
        """Get a checklist instance with all items and activities"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, template_id, checklist_date, shift,
                               shift_start, shift_end, status,
                               created_by, closed_by, closed_at, created_at, section_id
                        FROM checklist_instances
                        WHERE id = %s
                    """, (instance_id,))
                    
                    row = cur.fetchone()
                    if not row:
                        return None

                    def get_user_info(user_id):
                        if not user_id:
                            return None
                        cur.execute("""
                            SELECT u.id, u.username, u.email, u.first_name, u.last_name, r.name
                            FROM users u
                            LEFT JOIN user_roles ur ON u.id = ur.user_id
                            LEFT JOIN roles r ON ur.role_id = r.id
                            WHERE u.id = %s
                        """, (user_id,))
                        u = cur.fetchone()
                        if u:
                            return {
                                'id': str(u[0]),
                                'username': u[1],
                                'email': u[2],
                                'first_name': u[3] or '',
                                'last_name': u[4] or '',
                                'role': u[5] or 'Member'
                            }
                        return None
                    
                    # Get items
                    cur.execute("""
                        SELECT cii.id, cii.template_item_id, cii.status,
                               cii.completed_by, cii.completed_at,
                               cii.skipped_reason, cii.failure_reason,
                               cii.scheduled_time, cii.notify_before_minutes,
                               cii.scheduled_at, cii.remind_at
                        FROM checklist_instance_items cii
                        LEFT JOIN checklist_template_items cti ON cii.template_item_id = cti.id
                        WHERE cii.instance_id = %s
                        ORDER BY COALESCE(cti.sort_order, 999), cii.template_item_id
                    """, (instance_id,))
                    
                    items = []
                    for item_row in cur.fetchall():
                        item_id = item_row[0]
                        
                        # Get item template details
                        cur.execute("""
                            SELECT id, template_id, title, description, item_type, is_required,
                                   scheduled_time, notify_before_minutes, severity, sort_order, created_at
                            FROM checklist_template_items
                            WHERE id = %s
                        """, (item_row[1],))
                        template_item_row = cur.fetchone()
                        template_item = None
                        if template_item_row:
                            scheduled_events = ChecklistDBService._get_instance_scheduled_events_for_item(cur, item_id)
                            template_item = {
                                'id': str(template_item_row[0]),
                                'template_id': str(template_item_row[1]),
                                'title': template_item_row[2],
                                'description': template_item_row[3],
                                'item_type': template_item_row[4],
                                'is_required': template_item_row[5],
                                'scheduled_time': ChecklistDBService._serialize_time(item_row[7]),
                                'notify_before_minutes': item_row[8],
                                'scheduled_at': ChecklistDBService._serialize_datetime(item_row[9]),
                                'remind_at': ChecklistDBService._serialize_datetime(item_row[10]),
                                'severity': template_item_row[8],
                                'sort_order': template_item_row[9],
                                'created_at': ChecklistDBService._serialize_datetime(template_item_row[10]),
                                'subitems': [],
                                'scheduled_events': scheduled_events,
                            }
                        
                        # Get item activities
                        activities = []
                        if template_item:
                            cur.execute("""
                                SELECT id, action, comment, created_at, user_id, instance_item_id
                                FROM checklist_item_activity
                                WHERE instance_item_id = %s
                                ORDER BY created_at DESC
                            """, (item_id,))
                            for activity_row in cur.fetchall():
                                # Fetch user details for the activity
                                cur.execute("""
                                    SELECT u.id, u.username, u.email, u.first_name, u.last_name, r.name
                                    FROM users u
                                    LEFT JOIN user_roles ur ON u.id = ur.user_id
                                    LEFT JOIN roles r ON ur.role_id = r.id
                                    WHERE u.id = %s
                                """, (activity_row[4],))
                                user_row = cur.fetchone()
                                user = None
                                if user_row:
                                    user = {
                                        'id': str(user_row[0]),
                                        'username': user_row[1],
                                        'email': user_row[2],
                                        'first_name': user_row[3] or '',
                                        'last_name': user_row[4] or '',
                                        'role': user_row[5] or 'Member'
                                    }
                                activities.append({
                                    'id': str(activity_row[0]),
                                    'instance_item_id': str(activity_row[5]),
                                    'action': activity_row[1],
                                    'comment': activity_row[2],
                                    'created_at': activity_row[3].isoformat() if activity_row[3] else None,
                                    'user': user
                                })
                        
                        if template_item:
                            # Get subitems for this item
                            cur.execute("""
                                SELECT id, title, description, item_type, is_required,
                                       severity, sort_order, status,
                                       completed_by, completed_at,
                                       skipped_reason, failure_reason, created_at,
                                       scheduled_time, notify_before_minutes, scheduled_at, remind_at
                                FROM checklist_instance_subitems
                                WHERE instance_item_id = %s
                                ORDER BY sort_order
                            """, (item_id,))
                            subitems = []
                            for subitem_row in cur.fetchall():
                                subitem_id, subitem_title, subitem_desc, subitem_type, subitem_required, \
                                subitem_severity, subitem_sort, subitem_status, subitem_completed_by, \
                                subitem_completed_at, subitem_skipped_reason, subitem_failure_reason, subitem_created_at, \
                                subitem_scheduled_time, subitem_notify_before_minutes, subitem_scheduled_at, subitem_remind_at = subitem_row
                                # Get user who completed the subitem
                                subitem_completed_by_user = None
                                if subitem_completed_by:
                                    cur.execute("""
                                        SELECT u.id, u.username, u.email, u.first_name, u.last_name, r.name
                                        FROM users u
                                        LEFT JOIN user_roles ur ON u.id = ur.user_id
                                        LEFT JOIN roles r ON ur.role_id = r.id
                                        WHERE u.id = %s
                                    """, (subitem_completed_by,))
                                    subitem_user_row = cur.fetchone()
                                    if subitem_user_row:
                                        subitem_completed_by_user = {
                                            'id': str(subitem_user_row[0]),
                                            'username': subitem_user_row[1],
                                            'email': subitem_user_row[2],
                                            'first_name': subitem_user_row[3] or '',
                                            'last_name': subitem_user_row[4] or '',
                                            'role': subitem_user_row[5] or 'Member'
                                        }
                                subitems.append({
                                    'id': str(subitem_id),
                                    'instance_item_id': str(item_id),
                                    'title': subitem_title,
                                    'description': subitem_desc,
                                    'item_type': subitem_type,
                                    'is_required': subitem_required,
                                    'scheduled_time': ChecklistDBService._serialize_time(subitem_scheduled_time),
                                    'notify_before_minutes': subitem_notify_before_minutes,
                                    'scheduled_at': ChecklistDBService._serialize_datetime(subitem_scheduled_at),
                                    'remind_at': ChecklistDBService._serialize_datetime(subitem_remind_at),
                                    'severity': subitem_severity,
                                    'sort_order': subitem_sort,
                                    'status': subitem_status,
                                    'completed_by': subitem_completed_by_user,
                                    'completed_at': subitem_completed_at.isoformat() if subitem_completed_at else None,
                                    'skipped_reason': subitem_skipped_reason,
                                    'failure_reason': subitem_failure_reason,
                                    'created_at': subitem_created_at.isoformat() if subitem_created_at else None
                                })
                            # Get subitem completion status
                            if len(subitems) > 0:
                                completed_subitems = sum(1 for s in subitems if s['status'] == 'COMPLETED')
                                skipped_subitems = sum(1 for s in subitems if s['status'] == 'SKIPPED')
                                failed_subitems = sum(1 for s in subitems if s['status'] == 'FAILED')
                                actioned = completed_subitems + skipped_subitems + failed_subitems
                                if failed_subitems > 0:
                                    subitems_status = 'COMPLETED_WITH_EXCEPTIONS'
                                elif actioned == len(subitems):
                                    subitems_status = 'COMPLETED'
                                elif actioned > 0:
                                    subitems_status = 'IN_PROGRESS'
                                else:
                                    subitems_status = 'PENDING'
                            else:
                                subitems_status = None
                            # Create item object with flattened template fields for frontend compatibility
                            item_completed_by = None
                            if item_row[3]:
                                item_completed_by = get_user_info(item_row[3])

                            latest_comment = next(
                                (activity.get('comment') for activity in activities if activity.get('comment')),
                                None,
                            )

                            item_data = {
                                'id': str(item_id),
                                'template_item_id': str(item_row[1]),
                                'template_item': template_item,
                                'status': item_row[2],
                                'completed_by': item_completed_by,
                                'completed_at': ChecklistDBService._serialize_datetime(item_row[4]),
                                'skipped_reason': item_row[5],
                                'failure_reason': item_row[6],
                                'scheduled_at': ChecklistDBService._serialize_datetime(item_row[9]),
                                'remind_at': ChecklistDBService._serialize_datetime(item_row[10]),
                                'notes': latest_comment,
                                'activities': activities,
                                'subitems': subitems,
                                'subitems_status': subitems_status
                            }
                            
                            # Flatten template fields to root level for frontend compatibility
                            if template_item:
                                item_data.update({
                                    'title': template_item['title'],
                                    'description': template_item['description'],
                                    'item_type': template_item['item_type'],
                                    'is_required': template_item['is_required'],
                                    'scheduled_time': template_item['scheduled_time'],
                                    'notify_before_minutes': template_item['notify_before_minutes'],
                                    'scheduled_at': template_item.get('scheduled_at'),
                                    'remind_at': template_item.get('remind_at'),
                                    'severity': template_item['severity'],
                                    'sort_order': template_item['sort_order'],
                                    'scheduled_events': template_item['scheduled_events'],
                                })
                            
                            items.append(item_data)
                    
                    # Get participants with user details (id, username, email, first_name, last_name, role)
                    cur.execute("""
                        SELECT u.id, u.username, u.email, u.first_name, u.last_name, r.name
                        FROM checklist_participants cp
                        JOIN users u ON cp.user_id = u.id
                        LEFT JOIN user_roles ur ON u.id = ur.user_id
                        LEFT JOIN roles r ON ur.role_id = r.id
                        WHERE cp.instance_id = %s
                        ORDER BY u.username
                    """, (instance_id,))
                    participants = []
                    for user_row in cur.fetchall():
                        uid = user_row[0]
                        username = user_row[1] if user_row[1] else 'Unknown User'
                        email = user_row[2] if user_row[2] else ''
                        first_name = user_row[3] if user_row[3] else ''
                        last_name = user_row[4] if user_row[4] else ''
                        role = user_row[5] if user_row[5] else 'Member'
                        participants.append({
                            'id': str(uid),
                            'username': username,
                            'email': email,
                            'first_name': first_name,
                            'last_name': last_name,
                            'role': role,
                            'is_online': websocket_manager.get_user_connection_count(str(uid)) > 0
                        })
                    
                    # Calculate stats
                    total = len(items)
                    completed = sum(1 for i in items if i['status'] == 'COMPLETED')
                    skipped = sum(1 for i in items if i['status'] == 'SKIPPED')
                    failed = sum(1 for i in items if i['status'] == 'FAILED')
                    
                    completion_rate = (completed / total * 100) if total > 0 else 0
                    
                    # Get template details for this instance
                    template = ChecklistDBService.get_template(row[1]) if row[1] else None
                    return {
                        'id': str(row[0]),
                        'template': template,
                        'checklist_date': str(row[2]),
                        'shift': row[3],
                        'shift_start': row[4].isoformat() if row[4] else None,
                        'shift_end': row[5].isoformat() if row[5] else None,
                        'status': row[6],
                        'created_by': get_user_info(row[7]),
                        'closed_by': get_user_info(row[8]),
                        'closed_at': row[9].isoformat() if row[9] else None,
                        'created_at': row[10].isoformat() if row[10] else None,
                        'section_id': str(row[11]) if row[11] else None,
                        'items': items,
                        'participants': participants,
                        'completion_percentage': round(completion_rate, 2),
                        'time_remaining_minutes': None
                    }
        except Exception as e:
            log.error(f"Failed to get instance {instance_id}: {e}")
            return None
    
    @staticmethod
    def get_instances_by_date(checklist_date: date, shift: Optional[str] = None) -> List[dict]:
        """Get all instances for a given date, optionally filtered by shift"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    query = """
                        SELECT id FROM checklist_instances
                        WHERE checklist_date = %s
                    """
                    params = [checklist_date]

                    if shift:
                        query += " AND shift = %s"
                        params.append(shift)

                    query += " ORDER BY shift"

                    cur.execute(query, params)
                    rows = cur.fetchall()

                    instances = []
                    for (instance_id,) in rows:
                        instance = ChecklistDBService.get_instance(instance_id)
                        if instance:
                            instances.append(instance)

                    return instances
        except Exception as e:
            log.error(f"Failed to get instances for {checklist_date}: {e}")
            return []

    @staticmethod
    def get_instances_by_date_range(
        start_date: date,
        end_date: date,
        shift: Optional[str] = None
    ) -> List[dict]:
        """Get all instances in a date range, optionally filtered by shift."""
        instances, _ = ChecklistDBService._list_instance_summaries(
            start_date,
            end_date,
            shift=shift,
        )
        return instances

    @staticmethod
    def get_paginated_instance_summaries(
        start_date: date,
        end_date: date,
        shift: Optional[str] = None,
        *,
        status: Optional[str] = None,
        search: Optional[str] = None,
        section_id: Optional[str] = None,
        page: int = 1,
        limit: int = 18,
        sort_by: str = "checklist_date",
        sort_order: str = "desc",
        ) -> tuple[List[dict], int]:
        return ChecklistDBService._list_instance_summaries(
            start_date,
            end_date,
            shift=shift,
            status=status,
            search=search,
            section_id=section_id,
            page=page,
            limit=limit,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    @staticmethod
    def get_shift_coverage_for_date(checklist_date: date, section_id: Optional[str] = None) -> dict:
        coverage = {
            "MORNING": 0,
            "AFTERNOON": 0,
            "NIGHT": 0,
        }
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    query = """
                        SELECT shift::text, COUNT(*)
                        FROM checklist_instances
                        WHERE checklist_date = %s
                    """
                    params: List[Any] = [checklist_date]

                    if section_id:
                        query += " AND section_id = %s"
                        params.append(section_id)

                    query += " GROUP BY shift"

                    cur.execute(query, params)
                    for shift_name, count in cur.fetchall():
                        if shift_name in coverage:
                            coverage[shift_name] = int(count)
        except Exception as e:
            log.error(f"Failed to get shift coverage for {checklist_date}: {e}")

        return coverage

    @staticmethod
    def _build_instance_summary(row: dict) -> dict:
        template_created_at = row.get("template_created_at") or row.get("created_at")
        template_section_id = row.get("template_section_id") or row.get("section_id")

        return {
            "id": str(row["id"]),
            "template_id": str(row["template_id"]) if row.get("template_id") else None,
            "template": {
                "id": str(row["template_id"]) if row.get("template_id") else None,
                "name": row.get("template_name") or "Unknown Checklist",
                "description": row.get("template_description"),
                "shift": row.get("template_shift") or row.get("shift"),
                "is_active": bool(row.get("template_is_active", True)),
                "version": row.get("template_version") or 1,
                "created_by": None,
                "created_at": ChecklistDBService._serialize_datetime(template_created_at),
                "section_id": str(template_section_id) if template_section_id else None,
                "items": [],
            },
            "checklist_date": str(row["checklist_date"]),
            "shift": row["shift"],
            "shift_start": ChecklistDBService._serialize_datetime(row.get("shift_start")),
            "shift_end": ChecklistDBService._serialize_datetime(row.get("shift_end")),
            "status": row["status"],
            "created_by": None,
            "closed_by": None,
            "closed_at": ChecklistDBService._serialize_datetime(row.get("closed_at")),
            "created_at": ChecklistDBService._serialize_datetime(row.get("created_at")),
            "section_id": str(row["section_id"]) if row.get("section_id") else None,
            "items": [],
            "participants": [],
            "completion_percentage": 0.0,
            "time_remaining_minutes": None,
        }

    @staticmethod
    def _get_instance_summary_order_clause(sort_by: str, sort_order: str) -> str:
        direction = "ASC" if str(sort_order).lower() == "asc" else "DESC"
        shift_rank = (
            "CASE UPPER(ci.shift::text) "
            "WHEN 'MORNING' THEN 0 "
            "WHEN 'AFTERNOON' THEN 1 "
            "WHEN 'NIGHT' THEN 2 "
            "ELSE 99 END"
        )

        if sort_by == "shift":
            return f"ORDER BY {shift_rank} {direction}, ci.checklist_date DESC, ci.created_at DESC"
        if sort_by == "status":
            return f"ORDER BY ci.status::text {direction}, ci.checklist_date DESC, {shift_rank} ASC"
        return f"ORDER BY ci.checklist_date {direction}, {shift_rank} ASC, ci.created_at DESC"

    @staticmethod
    def _list_instance_summaries(
        start_date: date,
        end_date: date,
        shift: Optional[str] = None,
        *,
        status: Optional[str] = None,
        search: Optional[str] = None,
        section_id: Optional[str] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
        sort_by: str = "checklist_date",
        sort_order: str = "desc",
    ) -> tuple[List[dict], int]:
        """Return lightweight checklist-instance summaries for list views."""
        try:
            filters = ["ci.checklist_date BETWEEN %s AND %s"]
            params: List[Any] = [start_date, end_date]

            if shift:
                filters.append("ci.shift = %s")
                params.append(ChecklistDBService._normalize_shift_name(shift))

            if status:
                filters.append("ci.status::text = %s")
                params.append(status)

            normalized_search = (search or "").strip()
            if normalized_search:
                like_term = f"%{normalized_search}%"
                filters.append(
                    """
                    (
                        ct.name ILIKE %s
                        OR ci.shift::text ILIKE %s
                        OR ci.status::text ILIKE %s
                        OR ci.checklist_date::text ILIKE %s
                        OR ci.id::text ILIKE %s
                    )
                    """
                )
                params.extend([like_term, like_term, like_term, like_term, like_term])

            if section_id:
                filters.append("ci.section_id = %s")
                params.append(section_id)

            where_clause = " AND ".join(filters)
            base_query = f"""
                FROM checklist_instances ci
                LEFT JOIN checklist_templates ct ON ct.id = ci.template_id
                WHERE {where_clause}
            """

            with get_connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(f"SELECT COUNT(*) AS total {base_query}", params)
                    total = int(cur.fetchone()["total"])

                    select_query = f"""
                        SELECT
                            ci.id,
                            ci.template_id,
                            ci.checklist_date,
                            ci.shift::text AS shift,
                            ci.shift_start,
                            ci.shift_end,
                            ci.status::text AS status,
                            ci.closed_at,
                            ci.created_at,
                            ci.section_id,
                            ct.name AS template_name,
                            COALESCE(ct.description, '') AS template_description,
                            COALESCE(ct.shift::text, ci.shift::text) AS template_shift,
                            COALESCE(ct.is_active, TRUE) AS template_is_active,
                            COALESCE(ct.version, 1) AS template_version,
                            ct.created_at AS template_created_at,
                            ct.section_id AS template_section_id
                        {base_query}
                        {ChecklistDBService._get_instance_summary_order_clause(sort_by, sort_order)}
                    """

                    query_params = list(params)
                    if limit is not None:
                        select_query += " LIMIT %s"
                        query_params.append(limit)
                        if page is not None:
                            select_query += " OFFSET %s"
                            query_params.append(max(page - 1, 0) * limit)

                    cur.execute(select_query, query_params)
                    items = [ChecklistDBService._build_instance_summary(row) for row in cur.fetchall()]
                    return items, total
        except Exception as e:
            log.error(f"Failed to list instance summaries for range {start_date} to {end_date}: {e}")
            return [], 0
    
    # =====================================================
    # ITEM STATUS UPDATES
    # =====================================================
    
    @staticmethod
    def update_item_status(
        item_id: UUID,
        new_status: str,
        user_id: UUID,
        username: str,
        reason: Optional[str] = None,
        comment: Optional[str] = None
    ) -> bool:
        """
        Update checklist item status
        Logs activity, creates notifications on skip/fail
        """
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Get current item state
                    cur.execute("""
                        SELECT status, instance_id, template_item_id
                        FROM checklist_instance_items
                        WHERE id = %s
                    """, (item_id,))
                    
                    row = cur.fetchone()
                    if not row:
                        log.warning(f"Item not found: {item_id}")
                        return False
                    
                    old_status, instance_id, template_item_id = row
                    
                    # Get item and instance details for logging
                    cur.execute("""
                        SELECT title FROM checklist_template_items WHERE id = %s
                    """, (template_item_id,))
                    (item_title,) = cur.fetchone()
                    
                    cur.execute("""
                        SELECT checklist_date, shift FROM checklist_instances WHERE id = %s
                    """, (instance_id,))
                    (checklist_date, shift) = cur.fetchone()
                    
                    # Update item
                    if new_status == 'COMPLETED':
                        cur.execute("""
                            UPDATE checklist_instance_items
                            SET status = %s, completed_by = %s, completed_at = %s
                            WHERE id = %s
                        """, (new_status, user_id, datetime.now(timezone.utc), item_id))
                        
                        # Log ops event
                        OpsEventLogger.log_item_completed(
                            item_id=item_id,
                            instance_id=instance_id,
                            item_title=item_title,
                            completed_by=user_id,
                            completed_by_username=username
                        )
                    
                    elif new_status == 'SKIPPED':
                        cur.execute("""
                            UPDATE checklist_instance_items
                            SET status = %s, skipped_reason = %s
                            WHERE id = %s
                        """, (new_status, reason, item_id))
                        
                        # Log ops event
                        OpsEventLogger.log_item_skipped(
                            item_id=item_id,
                            instance_id=instance_id,
                            item_title=item_title,
                            skipped_by=user_id,
                            skipped_by_username=username,
                            reason=reason or "No reason provided"
                        )
                        
                        # Notify admin/manager
                        NotificationDBService.create_item_skipped_notification(
                            item_id=item_id,
                            item_title=item_title,
                            instance_id=instance_id,
                            checklist_date=str(checklist_date),
                            shift=shift,
                            skipped_reason=reason or "No reason provided"
                        )
                    
                    elif new_status == 'FAILED':
                        cur.execute("""
                            UPDATE checklist_instance_items
                            SET status = %s, failure_reason = %s
                            WHERE id = %s
                        """, (new_status, reason, item_id))
                        
                        # Log ops event
                        OpsEventLogger.log_item_failed(
                            item_id=item_id,
                            instance_id=instance_id,
                            item_title=item_title,
                            failed_by=user_id,
                            failed_by_username=username,
                            reason=reason or "No reason provided"
                        )
                        
                        # Notify admin/manager (CRITICAL)
                        NotificationDBService.create_item_failed_notification(
                            item_id=item_id,
                            item_title=item_title,
                            instance_id=instance_id,
                            checklist_date=str(checklist_date),
                            shift=shift,
                            failure_reason=reason or "No reason provided"
                        )
                    
                    else:
                        cur.execute("""
                            UPDATE checklist_instance_items
                            SET status = %s
                            WHERE id = %s
                        """, (new_status, item_id))

                    if new_status == 'IN_PROGRESS':
                        cur.execute("""
                            UPDATE checklist_instances
                            SET status = 'IN_PROGRESS'
                            WHERE id = %s AND status = 'OPEN'
                        """, (instance_id,))
                    
                    # Log activity
                    activity_action = {
                        'IN_PROGRESS': 'STARTED',
                        'COMPLETED': 'COMPLETED',
                        'SKIPPED': 'SKIPPED',
                        'FAILED': 'ESCALATED'
                    }.get(new_status, 'COMMENTED')
                    
                    cur.execute("""
                        INSERT INTO checklist_item_activity (
                            id, instance_item_id, user_id, action, comment, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                    """, (
                        uuid4(), item_id, user_id, activity_action, comment,
                        datetime.now(timezone.utc)
                    ))
                    
                    conn.commit()
                    
                    log.info(f"✅ Item {item_id} status updated: {old_status} → {new_status}")
                    # Return a structured result so callers can inspect the updated item
                    return {
                        'item': {
                            'id': item_id,
                            'title': item_title,
                            'status': new_status,
                            'previous_status': old_status,
                        }
                    }
        
        except Exception as e:
            log.error(f"Failed to update item {item_id} status: {e}")
            raise
    
    @staticmethod
    def complete_checklist_instance(
        instance_id: UUID,
        user_id: UUID,
        with_exceptions: bool = False
    ) -> dict:
        """
        Complete a checklist instance (admin/supervisor action).
        
        Args:
            instance_id: The checklist instance ID
            user_id: The user completing the checklist
            with_exceptions: If True, marks as COMPLETED_WITH_EXCEPTIONS when not 100% done
            
        Returns:
            Dict with instance data and ops event
        """
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Get current instance data
                    cur.execute("""
                        SELECT id, template_id, checklist_date, shift,
                               shift_start, shift_end, status,
                               created_by, closed_by, closed_at, created_at
                        FROM checklist_instances
                        WHERE id = %s
                    """, (instance_id,))
                    
                    instance_row = cur.fetchone()
                    if not instance_row:
                        raise ValueError(f"Checklist instance {instance_id} not found")
                    
                    # Get items with completion stats
                    cur.execute("""
                        SELECT cii.id, cii.template_item_id, cii.status,
                               cii.completed_by, cii.completed_at,
                               cii.skipped_reason, cii.failure_reason
                        FROM checklist_instance_items cii
                        WHERE cii.instance_id = %s
                    """, (instance_id,))
                    
                    items = []
                    total_items = 0
                    completed_items = 0
                    skipped_items = 0
                    failed_items = 0
                    
                    for item_row in cur.fetchall():
                        total_items += 1
                        item_status = item_row[2]
                        
                        if item_status == 'COMPLETED':
                            completed_items += 1
                        elif item_status == 'SKIPPED':
                            skipped_items += 1
                        elif item_status == 'FAILED':
                            failed_items += 1
                        
                        items.append({
                            'id': str(item_row[0]),
                            'template_item_id': str(item_row[1]),
                            'status': item_status,
                            'completed_by': item_row[3],
                            'completed_at': item_row[4].isoformat() if item_row[4] else None,
                            'skipped_reason': item_row[5],
                            'failure_reason': item_row[6]
                        })
                    
                    # Calculate completion stats
                    completion_percentage = (completed_items / total_items * 100) if total_items > 0 else 0
                    has_exceptions = skipped_items > 0 or failed_items > 0 or completion_percentage < 100
                    
                    # Determine final status
                    if has_exceptions and with_exceptions:
                        final_status = 'COMPLETED_WITH_EXCEPTIONS'
                    elif completion_percentage == 100:
                        final_status = 'COMPLETED'
                    elif with_exceptions:
                        # Allow completion with exceptions even if not all items done
                        final_status = 'COMPLETED_WITH_EXCEPTIONS'
                    else:
                        raise ValueError(f"Cannot complete checklist: only {completion_percentage:.1f}% complete. Use with_exceptions=True to force completion.")
                    
                    # Update instance status
                    cur.execute("""
                        UPDATE checklist_instances 
                        SET status = %s, 
                            closed_by = %s, 
                            closed_at = %s
                        WHERE id = %s
                    """, (
                        final_status,
                        user_id,
                        datetime.now(timezone.utc),
                        instance_id
                    ))
                    
                    conn.commit()
                    
                    log.info(f"✅ Checklist instance {instance_id} completed with status {final_status}")
                    
                    # Get user info for response
                    cur.execute("""
                        SELECT id, username, email, first_name, last_name
                        FROM users
                        WHERE id = %s
                    """, (user_id,))
                    
                    user_row = cur.fetchone()
                    closed_by_user = {
                        'id': str(user_row[0]),
                        'username': user_row[1],
                        'email': user_row[2] or '',
                        'first_name': user_row[3] or '',
                        'last_name': user_row[4] or ''
                    } if user_row else None
                    
                    # Get template info
                    cur.execute("""
                        SELECT name, description, shift
                        FROM checklist_templates
                        WHERE id = %s
                    """, (instance_row[1],))
                    
                    template_row = cur.fetchone()
                    template = {
                        'id': str(instance_row[1]),
                        'name': template_row[0] if template_row else 'Unknown',
                        'description': template_row[1] if template_row else '',
                        'shift': template_row[2] if template_row else 'UNKNOWN'
                    }
                    
                    # Build response instance
                    response_instance = {
                        'id': str(instance_row[0]),  # UUID -> string
                        'template': template,
                        'checklist_date': instance_row[2].isoformat() if instance_row[2] else None,  # date -> isoformat
                        'shift': instance_row[3],
                        'shift_start': instance_row[4].isoformat() if instance_row[4] else None,  # datetime -> isoformat
                        'shift_end': instance_row[5].isoformat() if instance_row[5] else None,  # datetime -> isoformat
                        'status': final_status,
                        'created_by': str(instance_row[6]) if instance_row[6] else None,  # UUID -> string
                        'closed_by': closed_by_user,
                        'closed_at': datetime.now(timezone.utc).isoformat(),
                        'created_at': instance_row[9].isoformat() if instance_row[9] else None,  # datetime -> isoformat
                        'items': items,
                        'participants': [],  # Could be populated if needed
                        'notes': [],
                        'attachments': [],
                        'exceptions': [],
                        'handover_notes': []
                    }
                    
                    return {
                        'instance': response_instance,
                        'ops_event': {
                            'event_type': 'CHECKLIST_COMPLETED',
                            'entity_type': 'CHECKLIST_INSTANCE',
                            'entity_id': str(instance_id),
                            'payload': {
                                'instance_id': str(instance_id),
                                'completed_by': str(user_id),
                                'status': final_status,
                                'completion_percentage': completion_percentage,
                                'has_exceptions': has_exceptions,
                                'completed_items': completed_items,
                                'total_items': total_items,
                                'skipped_items': skipped_items,
                                'failed_items': failed_items,
                                'completed_with_exceptions': with_exceptions
                            }
                        }
                    }
                    
        except Exception as e:
            log.error(f"Failed to complete checklist instance {instance_id}: {e}")
            raise ValueError(f"Failed to complete checklist: {e}")
    
    @staticmethod
    def update_instance_status(
        instance_id: UUID,
        new_status: str,
        user_id: UUID,
        username: str,
        comment: Optional[str] = None
    ) -> bool:
        """Update checklist instance status"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Get instance details
                    cur.execute("""
                        SELECT status, checklist_date, shift FROM checklist_instances
                        WHERE id = %s
                    """, (instance_id,))
                    
                    row = cur.fetchone()
                    if not row:
                        return False
                    
                    old_status, checklist_date, shift = row
                    
                    # Update instance
                    if new_status in ['COMPLETED', 'COMPLETED_WITH_EXCEPTIONS']:
                        cur.execute("""
                            UPDATE checklist_instances
                            SET status = %s, closed_by = %s, closed_at = %s
                            WHERE id = %s
                        """, (new_status, user_id, datetime.now(timezone.utc), instance_id))
                        
                        # Calculate stats for notification
                        cur.execute("""
                            SELECT 
                                COUNT(*),
                                SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END)
                            FROM checklist_instance_items
                            WHERE instance_id = %s
                        """, (instance_id,))
                        
                        (total, completed) = cur.fetchone()
                        completed = completed or 0
                        completion_rate = (completed / total * 100) if total > 0 else 0
                        
                        # Log event and notify
                        if new_status == 'COMPLETED':
                            OpsEventLogger.log_checklist_completed(
                                instance_id=instance_id,
                                checklist_date=str(checklist_date),
                                shift=shift,
                                completion_rate=completion_rate,
                                completed_by=user_id,
                                completed_by_username=username,
                                total_items=total,
                                completed_items=completed
                            )
                            
                            NotificationDBService.create_checklist_completed_notification(
                                instance_id=instance_id,
                                checklist_date=str(checklist_date),
                                shift=shift,
                                completion_rate=completion_rate,
                                completed_by_username=username
                            )
                        
                        else:  # COMPLETED_WITH_EXCEPTIONS
                            skipped = sum(1 for _ in cur.execute(
                                "SELECT 1 FROM checklist_instance_items WHERE instance_id = %s AND status = 'SKIPPED'",
                                (instance_id,)
                            ).fetchall())
                            failed = sum(1 for _ in cur.execute(
                                "SELECT 1 FROM checklist_instance_items WHERE instance_id = %s AND status = 'FAILED'",
                                (instance_id,)
                            ).fetchall())
                            
                            OpsEventLogger.log_checklist_completed_with_exceptions(
                                instance_id=instance_id,
                                checklist_date=str(checklist_date),
                                shift=shift,
                                completion_rate=completion_rate,
                                completed_by=user_id,
                                completed_by_username=username,
                                total_items=total,
                                completed_items=completed,
                                skipped_items=skipped,
                                failed_items=failed
                            )
                    else:
                        cur.execute("""
                            UPDATE checklist_instances SET status = %s WHERE id = %s
                        """, (new_status, instance_id))
                    
                    conn.commit()
                    log.info(f"✅ Instance {instance_id} status updated: {old_status} → {new_status}")
                    return True
        
        except Exception as e:
            log.error(f"Failed to update instance {instance_id} status: {e}")
            raise
    
    @staticmethod
    def add_participant(instance_id: UUID, user_id: UUID, username: str) -> bool:
        """Add a user to checklist participants"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Check if already a participant
                    cur.execute("""
                        SELECT id FROM checklist_participants
                        WHERE instance_id = %s AND user_id = %s
                    """, (instance_id, user_id))
                    
                    if cur.fetchone():
                        log.info(f"User {user_id} already a participant in {instance_id}")
                        return True
                    
                    # Add participant
                    cur.execute("""
                        INSERT INTO checklist_participants (
                            id, instance_id, user_id, joined_at
                        ) VALUES (%s, %s, %s, %s)
                    """, (uuid4(), instance_id, user_id, datetime.now(timezone.utc)))
                    
                    # Get instance details for log
                    cur.execute("""
                        SELECT checklist_date, shift FROM checklist_instances WHERE id = %s
                    """, (instance_id,))
                    (checklist_date, shift) = cur.fetchone()
                    
                    conn.commit()
                    
                    # Log event
                    OpsEventLogger.log_participant_joined(
                        instance_id=instance_id,
                        user_id=user_id,
                        username=username,
                        checklist_date=str(checklist_date),
                        shift=shift
                    )
                    
                    # Notify current participants and managers
                    try:
                        from app.notifications.db_service import NotificationDBService
                        NotificationDBService.create_participant_joined_notification(
                            instance_id=instance_id,
                            participant_username=username,
                            checklist_date=str(checklist_date),
                            shift=shift
                        )
                    except Exception as e:
                        log.error(f"Failed to create participant joined notification: {e}")
                    
                    log.info(f"✅ User {username} joined checklist {instance_id}")
                    return True
        
        except Exception as e:
            log.error(f"Failed to add participant: {e}")
            return False

    # =====================================================
    # SUBITEM MANAGEMENT (HIERARCHICAL CHECKLISTS)
    # =====================================================
    
    @staticmethod
    def get_subitems_for_item(instance_item_id: UUID) -> List[dict]:
        """Get all subitems for a checklist instance item"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, title, description, item_type, is_required,
                               severity, sort_order, status,
                               completed_by, completed_at,
                               skipped_reason, failure_reason, created_at,
                               scheduled_time, notify_before_minutes, scheduled_at, remind_at
                        FROM checklist_instance_subitems
                        WHERE instance_item_id = %s
                        ORDER BY sort_order
                    """, (instance_item_id,))
                    
                    subitems = []
                    for row in cur.fetchall():
                        subitem_id, title, description, item_type, is_required, \
                        severity, sort_order, status, completed_by, completed_at, \
                        skipped_reason, failure_reason, created_at, scheduled_time, \
                        notify_before_minutes, scheduled_at, remind_at = row
                        
                        # Get user details if completed
                        completed_by_user = None
                        if completed_by:
                            cur.execute("""
                                SELECT id, username, email FROM users WHERE id = %s
                            """, (completed_by,))
                            user_row = cur.fetchone()
                            if user_row:
                                completed_by_user = {
                                    'id': str(user_row[0]),
                                    'username': user_row[1],
                                    'email': user_row[2]
                                }
                        
                        subitems.append({
                            'id': str(subitem_id),
                            'instance_item_id': str(instance_item_id),
                            'title': title,
                            'description': description,
                            'item_type': item_type,
                            'is_required': is_required,
                            'scheduled_time': ChecklistDBService._serialize_time(scheduled_time),
                            'notify_before_minutes': notify_before_minutes,
                            'scheduled_at': ChecklistDBService._serialize_datetime(scheduled_at),
                            'remind_at': ChecklistDBService._serialize_datetime(remind_at),
                            'severity': severity,
                            'sort_order': sort_order,
                            'status': status,
                            'completed_by': completed_by_user,
                            'completed_at': completed_at.isoformat() if completed_at else None,
                            'skipped_reason': skipped_reason,
                            'failure_reason': failure_reason,
                            'created_at': created_at.isoformat() if created_at else None
                        })
                    
                    return subitems
        except Exception as e:
            log.error(f"Failed to get subitems for item {instance_item_id}: {e}")
            return []
    
    @staticmethod
    def get_next_pending_subitem(instance_item_id: UUID) -> Optional[dict]:
        """Get the first pending subitem for an item"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, title, description, item_type, is_required,
                               severity, sort_order, status, created_at,
                               scheduled_time, notify_before_minutes, scheduled_at, remind_at
                        FROM checklist_instance_subitems
                        WHERE instance_item_id = %s AND status = 'PENDING'
                        ORDER BY sort_order
                        LIMIT 1
                    """, (instance_item_id,))
                    
                    row = cur.fetchone()
                    if not row:
                        return None
                    
                    return {
                        'id': str(row[0]),
                        'instance_item_id': str(instance_item_id),
                        'title': row[1],
                        'description': row[2],
                        'item_type': row[3],
                        'is_required': row[4],
                        'scheduled_time': ChecklistDBService._serialize_time(row[9]),
                        'notify_before_minutes': row[10],
                        'scheduled_at': ChecklistDBService._serialize_datetime(row[11]),
                        'remind_at': ChecklistDBService._serialize_datetime(row[12]),
                        'severity': row[5],
                        'sort_order': row[6],
                        'status': row[7],
                        'created_at': row[8].isoformat() if row[8] else None
                    }
        except Exception as e:
            log.error(f"Failed to get next pending subitem for {instance_item_id}: {e}")
            return None
    
    @staticmethod
    def get_subitem_by_id(subitem_id: UUID) -> dict:
        """Get a specific subitem by ID"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, instance_item_id, title, description, item_type, 
                               is_required, status, completed_by, completed_at, 
                               skipped_reason, failure_reason, severity, sort_order, created_at,
                               scheduled_time, notify_before_minutes, scheduled_at, remind_at
                        FROM checklist_instance_subitems
                        WHERE id = %s
                    """, (subitem_id,))
                    
                    row = cur.fetchone()
                    if not row:
                        return None
                    
                    return {
                        'id': str(row[0]),
                        'instance_item_id': str(row[1]),
                        'title': row[2],
                        'description': row[3],
                        'item_type': row[4],
                        'is_required': row[5],
                        'scheduled_time': ChecklistDBService._serialize_time(row[14]),
                        'notify_before_minutes': row[15],
                        'scheduled_at': ChecklistDBService._serialize_datetime(row[16]),
                        'remind_at': ChecklistDBService._serialize_datetime(row[17]),
                        'status': row[6],
                        'completed_by': str(row[7]) if row[7] else None,
                        'completed_at': row[8].isoformat() if row[8] else None,
                        'skipped_reason': row[9],
                        'failure_reason': row[10],
                        'severity': row[11],
                        'sort_order': row[12],
                        'created_at': row[13].isoformat() if row[13] else None
                    }
        except Exception as e:
            log.error(f"Failed to get subitem {subitem_id}: {e}")
            return None
    
    @staticmethod
    def update_subitem_status(
        subitem_id: UUID,
        new_status: str,
        user_id: UUID,
        username: str,
        reason: Optional[str] = None,
        comment: Optional[str] = None
    ) -> dict:
        """Update a subitem status (COMPLETED, SKIPPED, or FAILED)"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Get current subitem state
                    cur.execute("""
                        SELECT status, instance_item_id FROM checklist_instance_subitems
                        WHERE id = %s
                    """, (subitem_id,))
                    
                    row = cur.fetchone()
                    if not row:
                        raise ValueError(f"Subitem not found: {subitem_id}")
                    
                    old_status, instance_item_id = row
                    
                    # Update subitem status
                    if new_status == 'IN_PROGRESS':
                        cur.execute("""
                            UPDATE checklist_instance_subitems
                            SET status = %s
                            WHERE id = %s
                        """, (new_status, subitem_id))
                    
                    elif new_status == 'COMPLETED':
                        cur.execute("""
                            UPDATE checklist_instance_subitems
                            SET status = %s, completed_by = %s, completed_at = %s
                            WHERE id = %s
                        """, (new_status, user_id, datetime.now(timezone.utc), subitem_id))
                    
                    elif new_status == 'SKIPPED':
                        cur.execute("""
                            UPDATE checklist_instance_subitems
                            SET status = %s, skipped_reason = %s
                            WHERE id = %s
                        """, (new_status, reason, subitem_id))
                    
                    elif new_status == 'FAILED':
                        cur.execute("""
                            UPDATE checklist_instance_subitems
                            SET status = %s, failure_reason = %s
                            WHERE id = %s
                        """, (new_status, reason, subitem_id))
                    
                    else:
                        raise ValueError(f"Invalid subitem status: {new_status}")
                    
                    conn.commit()
                    log.info(f"✅ Subitem {subitem_id} status updated: {old_status} → {new_status}")
                    
                    # Return complete subitem data
                    return {
                        'id': str(subitem_id),
                        'instance_item_id': str(instance_item_id),
                        'status': new_status,
                        'completed_by': str(user_id) if new_status == 'COMPLETED' else None,
                        'completed_at': datetime.now(timezone.utc).isoformat() if new_status == 'COMPLETED' else None,
                        'reason': reason if new_status in ['SKIPPED', 'FAILED'] else None
                    }
        
        except Exception as e:
            log.error(f"Failed to update subitem {subitem_id} status: {e}")
            raise
    
    @staticmethod
    def get_subitem_completion_status(instance_item_id: UUID) -> dict:
        """Get completion status for all subitems of an item"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT 
                            COUNT(*) as total,
                            SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) as completed,
                            SUM(CASE WHEN status = 'SKIPPED' THEN 1 ELSE 0 END) as skipped,
                            SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as failed,
                            SUM(CASE WHEN status = 'IN_PROGRESS' THEN 1 ELSE 0 END) as in_progress,
                            SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) as pending
                        FROM checklist_instance_subitems
                        WHERE instance_item_id = %s
                    """, (instance_item_id,))
                    
                    row = cur.fetchone()
                    if not row or row[0] == 0:
                        # No subitems
                        return {
                            'has_subitems': False,
                            'total': 0,
                            'completed': 0,
                            'skipped': 0,
                            'failed': 0,
                            'in_progress': 0,
                            'pending': 0,
                            'all_actioned': False,
                            'status': None
                        }
                    
                    total, completed, skipped, failed, in_progress, pending = row
                    completed = completed or 0
                    skipped = skipped or 0
                    failed = failed or 0
                    in_progress = in_progress or 0
                    pending = pending or 0
                    
                    # Determine overall subitems status
                    actioned = completed + skipped + failed
                    all_actioned = (actioned == total)
                    
                    if pending > 0:
                        subitems_status = 'PENDING'
                    elif in_progress > 0:
                        subitems_status = 'IN_PROGRESS'
                    elif all_actioned:
                        if failed > 0:
                            subitems_status = 'COMPLETED_WITH_EXCEPTIONS'
                        else:
                            subitems_status = 'COMPLETED'
                    else:
                        subitems_status = 'PENDING'
                    
                    return {
                        'has_subitems': True,
                        'total': total,
                        'completed': completed,
                        'skipped': skipped,
                        'failed': failed,
                        'in_progress': in_progress,
                        'pending': pending,
                        'all_actioned': all_actioned,
                        'status': subitems_status
                    }
        except Exception as e:
            log.error(f"Failed to get subitem completion status for {instance_item_id}: {e}")
            return {
                'has_subitems': False,
                'total': 0,
                'completed': 0,
                'skipped': 0,
                'failed': 0,
                'in_progress': 0,
                'pending': 0,
                'all_actioned': False,
                'status': None
            }
    
    @staticmethod
    def copy_template_subitems_to_instance(instance_item_id: UUID, template_item_id: UUID) -> bool:
        """Copy subitems from template definition to instance item"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT ci.checklist_date, ci.shift_start, ci.shift_end
                        FROM checklist_instance_items cii
                        JOIN checklist_instances ci ON ci.id = cii.instance_id
                        WHERE cii.id = %s
                    """, (instance_item_id,))
                    instance_row = cur.fetchone()
                    if not instance_row:
                        return False

                    inserted = 0
                    cur.execute("""
                        SELECT title, description, item_type, is_required,
                               scheduled_time, notify_before_minutes, severity, sort_order
                        FROM checklist_template_subitems
                        WHERE template_item_id = %s
                        ORDER BY sort_order
                    """, (template_item_id,))

                    for row in cur.fetchall():
                        scheduled_time, notify_before_minutes = ChecklistDBService._normalize_schedule_fields(
                            row[2],
                            row[4],
                            row[5],
                        )
                        scheduled_at, remind_at = ChecklistDBService._resolve_reminder_snapshot(
                            instance_row[0],
                            scheduled_time,
                            notify_before_minutes,
                            instance_row[1],
                            instance_row[2],
                        )
                        cur.execute("""
                            INSERT INTO checklist_instance_subitems (
                                instance_item_id, title, description, item_type,
                                is_required, severity, sort_order, status,
                                scheduled_time, notify_before_minutes, scheduled_at, remind_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            instance_item_id,
                            row[0],
                            row[1],
                            row[2],
                            row[3],
                            row[6],
                            row[7],
                            'PENDING',
                            scheduled_time,
                            notify_before_minutes,
                            scheduled_at,
                            remind_at,
                        ))
                        inserted += 1

                    conn.commit()
                    
                    if inserted > 0:
                        log.info(f"✅ Copied {inserted} subitems from template item {template_item_id} to instance item {instance_item_id}")
                    
                    return inserted > 0
        except Exception as e:
            log.error(f"Failed to copy template subitems: {e}")
            return False
