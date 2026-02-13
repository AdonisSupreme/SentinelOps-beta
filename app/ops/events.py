# app/ops/events.py
"""
Operational Events Logging Service
Logs important system events to database for auditing and monitoring.

Events logged (only high-signal):
- Checklist created
- Checklist completed / completed_with_exceptions / closed_by_exception
- Checklist participant joined
- Item status change (SKIP/FAIL/COMPLETE only)
- Supervisor override
- Handover note created
- Auth events are handled separately (see auth/events.py)
"""

from typing import Optional, Any
from uuid import UUID
from datetime import datetime, timezone
import json

from app.db.database import get_connection
from app.core.logging import get_logger

log = get_logger("ops-events")


def _safe_load_json(val: Any) -> dict:
    """Return a dict from a JSON string/bytes or return dict as-is.

    Protects against drivers already returning parsed dicts.
    """
    if not val:
        return {}
    if isinstance(val, dict):
        return val
    if isinstance(val, (bytes, bytearray)):
        try:
            return json.loads(val.decode("utf-8"))
        except Exception:
            return {}
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return {}
    return {}


class OpsEventLogger:
    """Logs operational events to database"""
    
    @staticmethod
    def log_event(
        event_type: str,
        entity_type: str,
        entity_id: UUID,
        payload: dict
    ) -> dict:
        """
        Log an operational event
        
        Args:
            event_type: e.g., 'CHECKLIST_CREATED', 'ITEM_FAILED', 'OVERRIDE_APPLIED'
            entity_type: e.g., 'CHECKLIST_INSTANCE', 'CHECKLIST_ITEM'
            entity_id: ID of the affected entity
            payload: Event-specific data (JSON-serializable dict)
        """
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO ops_events (
                            event_type, entity_type, entity_id, payload, created_at
                        ) VALUES (
                            %s, %s, %s, %s, %s
                        ) RETURNING id, event_type, entity_type, entity_id, payload, created_at
                    """, (
                        event_type, entity_type, entity_id, json.dumps(payload),
                        datetime.now(timezone.utc)
                    ))
                    
                    result = cur.fetchone()
                    conn.commit()
                    
                    log.info(f"📊 Event logged: {event_type}/{entity_type}/{entity_id}")
                    
                    return {
                        'id': str(result[0]),
                        'event_type': result[1],
                        'entity_type': result[2],
                        'entity_id': str(result[3]),
                        'payload': _safe_load_json(result[4]),
                        'created_at': result[5].isoformat() if result[5] else None
                    }
        except Exception as e:
            log.error(f"Failed to log ops event: {e}")
            raise
    
    @staticmethod
    def log_checklist_created(
        instance_id: UUID,
        checklist_date: str,
        shift: str,
        template_id: UUID,
        created_by: UUID,
        created_by_username: str
    ) -> dict:
        """Log checklist instance creation"""
        payload = {
            "checklist_date": checklist_date,
            "shift": shift,
            "template_id": str(template_id),
            "created_by": str(created_by),
            "created_by_username": created_by_username,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        return OpsEventLogger.log_event(
            event_type="CHECKLIST_CREATED",
            entity_type="CHECKLIST_INSTANCE",
            entity_id=instance_id,
            payload=payload
        )
    
    @staticmethod
    def log_checklist_completed(
        instance_id: UUID,
        checklist_date: str,
        shift: str,
        completion_rate: float,
        completed_by: UUID,
        completed_by_username: str,
        total_items: int,
        completed_items: int
    ) -> dict:
        """Log successful checklist completion"""
        payload = {
            "checklist_date": checklist_date,
            "shift": shift,
            "status": "COMPLETED",
            "completion_rate": completion_rate,
            "completed_by": str(completed_by),
            "completed_by_username": completed_by_username,
            "total_items": total_items,
            "completed_items": completed_items,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        return OpsEventLogger.log_event(
            event_type="CHECKLIST_COMPLETED",
            entity_type="CHECKLIST_INSTANCE",
            entity_id=instance_id,
            payload=payload
        )
    
    @staticmethod
    def log_checklist_completed_with_exceptions(
        instance_id: UUID,
        checklist_date: str,
        shift: str,
        completion_rate: float,
        completed_by: UUID,
        completed_by_username: str,
        total_items: int,
        completed_items: int,
        skipped_items: int,
        failed_items: int
    ) -> dict:
        """Log checklist completion with exceptions (skips/fails)"""
        payload = {
            "checklist_date": checklist_date,
            "shift": shift,
            "status": "COMPLETED_WITH_EXCEPTIONS",
            "completion_rate": completion_rate,
            "completed_by": str(completed_by),
            "completed_by_username": completed_by_username,
            "total_items": total_items,
            "completed_items": completed_items,
            "skipped_items": skipped_items,
            "failed_items": failed_items,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        return OpsEventLogger.log_event(
            event_type="CHECKLIST_COMPLETED_WITH_EXCEPTIONS",
            entity_type="CHECKLIST_INSTANCE",
            entity_id=instance_id,
            payload=payload
        )
    
    @staticmethod
    def log_item_skipped(
        item_id: UUID,
        instance_id: UUID,
        item_title: str,
        skipped_by: UUID,
        skipped_by_username: str,
        reason: str
    ) -> dict:
        """Log item skip"""
        payload = {
            "instance_id": str(instance_id),
            "item_title": item_title,
            "skipped_by": str(skipped_by),
            "skipped_by_username": skipped_by_username,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        return OpsEventLogger.log_event(
            event_type="ITEM_SKIPPED",
            entity_type="CHECKLIST_ITEM",
            entity_id=item_id,
            payload=payload
        )
    
    @staticmethod
    def log_item_failed(
        item_id: UUID,
        instance_id: UUID,
        item_title: str,
        failed_by: UUID,
        failed_by_username: str,
        reason: str
    ) -> dict:
        """Log item failure (escalation)"""
        payload = {
            "instance_id": str(instance_id),
            "item_title": item_title,
            "failed_by": str(failed_by),
            "failed_by_username": failed_by_username,
            "reason": reason,
            "severity": "HIGH",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        return OpsEventLogger.log_event(
            event_type="ITEM_FAILED",
            entity_type="CHECKLIST_ITEM",
            entity_id=item_id,
            payload=payload
        )
    
    @staticmethod
    def log_item_completed(
        item_id: UUID,
        instance_id: UUID,
        item_title: str,
        completed_by: UUID,
        completed_by_username: str
    ) -> dict:
        """Log item completion"""
        payload = {
            "instance_id": str(instance_id),
            "item_title": item_title,
            "completed_by": str(completed_by),
            "completed_by_username": completed_by_username,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        return OpsEventLogger.log_event(
            event_type="ITEM_COMPLETED",
            entity_type="CHECKLIST_ITEM",
            entity_id=item_id,
            payload=payload
        )
    
    @staticmethod
    def log_participant_joined(
        instance_id: UUID,
        user_id: UUID,
        username: str,
        checklist_date: str,
        shift: str
    ) -> dict:
        """Log team member joined checklist"""
        payload = {
            "user_id": str(user_id),
            "username": username,
            "checklist_date": checklist_date,
            "shift": shift,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        return OpsEventLogger.log_event(
            event_type="PARTICIPANT_JOINED",
            entity_type="CHECKLIST_INSTANCE",
            entity_id=instance_id,
            payload=payload
        )
    
    @staticmethod
    def log_override_applied(
        instance_id: UUID,
        supervisor_id: UUID,
        supervisor_username: str,
        reason: str
    ) -> dict:
        """Log supervisor override decision"""
        payload = {
            "supervisor_id": str(supervisor_id),
            "supervisor_username": supervisor_username,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        return OpsEventLogger.log_event(
            event_type="OVERRIDE_APPLIED",
            entity_type="CHECKLIST_OVERRIDE",
            entity_id=instance_id,
            payload=payload
        )
    
    @staticmethod
    def log_handover_created(
        handover_id: UUID,
        from_instance_id: UUID,
        to_instance_id: Optional[UUID],
        created_by: UUID,
        created_by_username: str,
        priority: int,
        summary: str
    ) -> dict:
        """Log handover note creation"""
        payload = {
            "from_instance_id": str(from_instance_id),
            "to_instance_id": str(to_instance_id) if to_instance_id else None,
            "created_by": str(created_by),
            "created_by_username": created_by_username,
            "priority": priority,
            "summary": summary,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        return OpsEventLogger.log_event(
            event_type="HANDOVER_CREATED",
            entity_type="HANDOVER_NOTE",
            entity_id=handover_id,
            payload=payload
        )
    
    @staticmethod
    def get_recent_events(
        limit: int = 100,
        event_type: Optional[str] = None,
        entity_type: Optional[str] = None
    ) -> list[dict]:
        """Retrieve recent operational events from database"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    query = "SELECT id, event_type, entity_type, entity_id, payload, created_at FROM ops_events WHERE 1=1"
                    params = []
                    
                    if event_type:
                        query += " AND event_type = %s"
                        params.append(event_type)
                    
                    if entity_type:
                        query += " AND entity_type = %s"
                        params.append(entity_type)
                    
                    query += " ORDER BY created_at DESC LIMIT %s"
                    params.append(limit)
                    
                    cur.execute(query, params)
                    rows = cur.fetchall()
                    
                    events = []
                    for row in rows:
                        events.append({
                            'id': str(row[0]),
                            'event_type': row[1],
                            'entity_type': row[2],
                            'entity_id': str(row[3]),
                            'payload': _safe_load_json(row[4]),
                            'created_at': row[5].isoformat() if row[5] else None
                        })
                    
                    return events
        except Exception as e:
            log.error(f"Failed to retrieve recent events: {e}")
            return []
