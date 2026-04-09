import json
import asyncio
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks, status, WebSocket, WebSocketDisconnect
from typing import List, Optional
from uuid import UUID, uuid4
from datetime import date, timedelta, datetime, timezone, time
from zoneinfo import ZoneInfo
from pydantic import BaseModel

from app.checklists.db_service import ChecklistDBService
from app.checklists.service import ChecklistService
from app.notifications.service import NotificationService
from app.checklists.schemas import (
    ChecklistTemplateCreate, ChecklistTemplateUpdate,
    ChecklistTemplateItemCreate, ChecklistTemplateItemUpdate,
    ChecklistTemplateSubitemBase,
    ChecklistInstanceCreate, ChecklistItemUpdate, ItemStartWorkRequest,
    HandoverNoteCreate, ShiftType, ChecklistStatus,
    ItemStatus, ActivityAction, ChecklistMutationResponse,
    ChecklistTemplateResponse, ChecklistInstanceResponse,
    ChecklistStats, ShiftPerformance, SubitemCompletionRequest, PaginatedResponse,
    TemplateMutationResponse, TemplateItemMutationResponse, TemplateSubitemMutationResponse
)
from app.auth.service import get_current_user
from app.auth.dependencies import get_current_user_websocket
from app.services.websocket import websocket_manager
from app.ops.events import OpsEventLogger
from app.checklists.state_machine import (
    get_item_transition_policy, get_checklist_transition_policy,
    is_item_transition_allowed
)
from app.core.authorization import has_capability, is_admin
from app.core.config import settings
from app.core.effects import EffectType, disclose_effects
from app.db.database import get_async_connection, get_connection
from app.core.logging import get_logger

log = get_logger("checklists-router")

router = APIRouter(prefix="/checklists", tags=["Checklists"])


class ChecklistDateChangeRequest(BaseModel):
    target_date: date


DEFAULT_OPERATIONAL_DAY_START = time(hour=7, minute=0)
DASHBOARD_SHIFT_ORDER = ("MORNING", "AFTERNOON", "NIGHT")
DASHBOARD_SHIFT_WINDOWS = {
    "MORNING": "07:00 - 15:00",
    "AFTERNOON": "15:00 - 23:00",
    "NIGHT": "23:00 - 07:00",
}


async def _get_operational_day_context(conn, now: Optional[datetime] = None) -> dict:
    business_tz = ZoneInfo(settings.TRUSTLINK_SCHEDULE_TIMEZONE)
    current_time = (now or datetime.now(timezone.utc)).astimezone(business_tz)

    try:
        morning_start = await conn.fetchval(
            """
            SELECT start_time
            FROM shifts
            WHERE UPPER(name) = 'MORNING'
            LIMIT 1
            """
        )
    except Exception as exc:
        log.warning("Failed to resolve MORNING shift start for operational-day context: %s", exc)
        morning_start = None

    boundary_time = morning_start or DEFAULT_OPERATIONAL_DAY_START
    local_clock = current_time.timetz().replace(tzinfo=None)
    operational_date = current_time.date() if local_clock >= boundary_time else (current_time.date() - timedelta(days=1))
    window_start = datetime.combine(operational_date, boundary_time, tzinfo=business_tz)
    window_end = window_start + timedelta(days=1)

    return {
        "operational_date": operational_date,
        "boundary_time": boundary_time,
        "window_start": window_start,
        "window_end": window_end,
        "timezone": settings.TRUSTLINK_SCHEDULE_TIMEZONE,
    }


async def _build_shift_window_for_date(conn, shift: Optional[str], target_date: date) -> tuple[datetime, datetime]:
    shift_upper = (shift or "").upper()
    business_tz = ZoneInfo(settings.TRUSTLINK_SCHEDULE_TIMEZONE)

    try:
        shift_row = await conn.fetchrow(
            """
            SELECT start_time, end_time
            FROM shifts
            WHERE UPPER(name) = $1
            LIMIT 1
            """,
            shift_upper,
        )
    except Exception as exc:
        log.warning("Failed to resolve shift window for %s: %s", shift_upper, exc)
        shift_row = None

    start_time, end_time = ChecklistDBService.DEFAULT_SHIFT_WINDOWS.get(
        shift_upper,
        (DEFAULT_OPERATIONAL_DAY_START, time(hour=19, minute=0)),
    )
    if shift_row and shift_row["start_time"] and shift_row["end_time"]:
        start_time = shift_row["start_time"]
        end_time = shift_row["end_time"]

    shift_start = datetime.combine(target_date, start_time, tzinfo=business_tz)
    shift_end_date = target_date + timedelta(days=1) if end_time <= start_time else target_date
    shift_end = datetime.combine(shift_end_date, end_time, tzinfo=business_tz)
    return shift_start.astimezone(timezone.utc), shift_end.astimezone(timezone.utc)


def _shift_sort_value(shift: Optional[str]) -> int:
    shift_order = {"MORNING": 0, "AFTERNOON": 1, "NIGHT": 2}
    return shift_order.get((shift or "").upper(), 99)


def _sort_instances(instances: List[dict], sort_by: str, sort_order: str) -> List[dict]:
    reverse = sort_order.lower() == "desc"

    if sort_by == "shift":
        return sorted(
            instances,
            key=lambda instance: (
                _shift_sort_value(instance.get("shift")),
                instance.get("checklist_date", "")
            ),
            reverse=reverse
        )

    if sort_by == "status":
        return sorted(
            instances,
            key=lambda instance: (
                instance.get("status", ""),
                instance.get("checklist_date", ""),
                _shift_sort_value(instance.get("shift"))
            ),
            reverse=reverse
        )

    return sorted(
        instances,
        key=lambda instance: (
            instance.get("checklist_date", ""),
            _shift_sort_value(instance.get("shift"))
        ),
        reverse=reverse
    )


def _normalize_section_id(section_id) -> Optional[str]:
    return str(section_id) if section_id is not None else None


def _require_user_section_id(current_user: dict) -> str:
    user_section = _normalize_section_id(current_user.get("section_id"))
    if not user_section:
        raise HTTPException(status_code=403, detail="Your profile is not assigned to a section")
    return user_section


def _ensure_section_access(
    resource_section_id,
    current_user: dict,
    *,
    forbidden_detail: str = "Insufficient permissions",
    missing_detail: str = "Checklist resource is missing a section assignment",
) -> None:
    if is_admin(current_user):
        return

    user_section = _require_user_section_id(current_user)
    resource_section = _normalize_section_id(resource_section_id)
    if not resource_section:
        raise HTTPException(status_code=409, detail=missing_detail)
    if resource_section != user_section:
        raise HTTPException(status_code=403, detail=forbidden_detail)


def _ensure_template_access(template: dict, current_user: dict, *, forbidden_detail: str = "Insufficient permissions") -> None:
    _ensure_section_access(
        template.get("section_id"),
        current_user,
        forbidden_detail=forbidden_detail,
        missing_detail="Checklist template is missing a section assignment",
    )


def _ensure_instance_access(instance: dict, current_user: dict, *, forbidden_detail: str = "Insufficient permissions") -> None:
    _ensure_section_access(
        instance.get("section_id"),
        current_user,
        forbidden_detail=forbidden_detail,
        missing_detail="Checklist instance is missing a section assignment",
    )


def _instance_visible_to_user(instance: dict, current_user: dict) -> bool:
    try:
        _ensure_instance_access(instance, current_user)
        return True
    except HTTPException:
        return False


async def _get_handover_note_section_id(note_id: UUID):
    async with get_async_connection() as conn:
        note_row = await conn.fetchrow(
            """
            SELECT COALESCE(fi.section_id, ti.section_id) AS section_id
            FROM handover_notes hn
            LEFT JOIN checklist_instances fi ON hn.from_instance_id = fi.id
            LEFT JOIN checklist_instances ti ON hn.to_instance_id = ti.id
            WHERE hn.id = $1
            """,
            note_id,
        )

    if not note_row:
        raise HTTPException(status_code=404, detail="Handover note not found")

    return note_row["section_id"]


def _build_empty_command_metrics() -> dict:
    return {
        "active_instances": 0,
        "in_progress_count": 0,
        "pending_review_count": 0,
        "completed_count": 0,
        "exception_count": 0,
        "coverage_gap_count": 0,
        "total_items": 0,
        "completed_items": 0,
        "actioned_items": 0,
        "critical_items": 0,
        "open_critical_items": 0,
        "participants": 0,
        "handover_count": 0,
        "execution_rate": 0,
        "completion_rate": 0,
        "critical_containment": 100,
        "posture_label": "Standby",
    }


def _build_empty_dashboard_summary(operational_context: dict, notifications_unread: int = 0) -> dict:
    return {
        "operational_day": {
            "checklist_date": operational_context["operational_date"].isoformat(),
            "window_start": operational_context["window_start"].isoformat(),
            "window_end": operational_context["window_end"].isoformat(),
            "timezone": operational_context["timezone"],
            "boundary_time": operational_context["boundary_time"].isoformat(),
        },
        "command_metrics": _build_empty_command_metrics(),
        "shift_cards": [
            {
                "shift": shift,
                "window": DASHBOARD_SHIFT_WINDOWS[shift],
                "operations": 0,
                "participants": 0,
                "exceptions": 0,
                "readiness": 0,
                "status": "No active thread",
            }
            for shift in DASHBOARD_SHIFT_ORDER
        ],
        "checklist_threads": [],
        "attention_queue": [],
        "handover_feed": [],
        "notifications_unread": int(notifications_unread or 0),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _build_dashboard_summary_payload(
    operational_context: dict,
    thread_rows,
    network_rows,
    notifications_unread: int,
) -> dict:
    if not thread_rows and not network_rows:
        return _build_empty_dashboard_summary(operational_context, notifications_unread)

    metrics = _build_empty_command_metrics()
    shift_rollup = {
        shift: {
            "operations": 0,
            "participants": 0,
            "exceptions": 0,
            "total_items": 0,
            "actioned_items": 0,
        }
        for shift in DASHBOARD_SHIFT_ORDER
    }
    checklist_threads = []
    handover_feed = []
    operation_watch = []

    for row in thread_rows:
        shift = str(row["shift"] or "").upper()
        total_items = int(row["total_items"] or 0)
        completed_items = int(row["completed_items"] or 0)
        actioned_items = int(row["actioned_items"] or 0)
        critical_items = int(row["critical_items"] or 0)
        open_critical_items = int(row["open_critical_items"] or 0)
        exception_items = int(row["exception_items"] or 0)
        participants_count = int(row["participants_count"] or 0)
        handover_count = int(row["handover_count"] or 0)
        execution_percentage = round((actioned_items / total_items) * 100) if total_items > 0 else 0
        has_exception_pressure = row["status"] in {"COMPLETED_WITH_EXCEPTIONS", "INCOMPLETE"} or exception_items > 0

        checklist_threads.append(
            {
                "id": str(row["id"]),
                "template_id": str(row["template_id"]) if row["template_id"] else None,
                "template_name": row["template_name"] or "Checklist",
                "checklist_date": row["checklist_date"].isoformat(),
                "shift": shift,
                "status": row["status"],
                "participant_count": participants_count,
                "user_joined": bool(row["user_joined"]),
                "total_items": total_items,
                "completed_items": completed_items,
                "actioned_items": actioned_items,
                "critical_items": critical_items,
                "open_critical_items": open_critical_items,
                "exception_items": exception_items,
                "handover_count": handover_count,
                "execution_percentage": execution_percentage,
                "has_exception_pressure": has_exception_pressure,
            }
        )

        metrics["active_instances"] += 1
        metrics["in_progress_count"] += 1 if row["status"] == "IN_PROGRESS" else 0
        metrics["pending_review_count"] += 1 if row["status"] == "PENDING_REVIEW" else 0
        metrics["completed_count"] += 1 if row["status"] in {"COMPLETED", "COMPLETED_WITH_EXCEPTIONS"} else 0
        metrics["exception_count"] += 1 if has_exception_pressure else 0
        metrics["coverage_gap_count"] += 1 if participants_count == 0 else 0
        metrics["total_items"] += total_items
        metrics["completed_items"] += completed_items
        metrics["actioned_items"] += actioned_items
        metrics["critical_items"] += critical_items
        metrics["open_critical_items"] += open_critical_items
        metrics["participants"] += participants_count
        metrics["handover_count"] += handover_count

        shift_bucket = shift_rollup.setdefault(
            shift,
            {"operations": 0, "participants": 0, "exceptions": 0, "total_items": 0, "actioned_items": 0},
        )
        shift_bucket["operations"] += 1
        shift_bucket["participants"] += participants_count
        shift_bucket["exceptions"] += 1 if has_exception_pressure else 0
        shift_bucket["total_items"] += total_items
        shift_bucket["actioned_items"] += actioned_items

        if participants_count == 0:
            operation_watch.append(
                {
                    "id": f"ops-{row['id']}",
                    "title": f"{shift} shift",
                    "detail": "No operator joined this checklist yet.",
                    "tone": "warning",
                }
            )
        elif has_exception_pressure:
            attention_count = exception_items or 1
            operation_watch.append(
                {
                    "id": f"ops-{row['id']}",
                    "title": f"{shift} shift",
                    "detail": f"{attention_count} task{'s' if attention_count != 1 else ''} need exception follow-up on this operation.",
                    "tone": "critical",
                }
            )

        if handover_count > 0:
            handover_feed.append(
                {
                    "id": str(row["id"]),
                    "shift": shift,
                    "count": handover_count,
                }
            )

    if metrics["total_items"] > 0:
        metrics["execution_rate"] = round((metrics["actioned_items"] / metrics["total_items"]) * 100)
        metrics["completion_rate"] = round((metrics["completed_items"] / metrics["total_items"]) * 100)

    if metrics["critical_items"] > 0:
        contained_critical = metrics["critical_items"] - metrics["open_critical_items"]
        metrics["critical_containment"] = round((contained_critical / metrics["critical_items"]) * 100)

    if metrics["exception_count"] > 0 or metrics["open_critical_items"] > 0:
        metrics["posture_label"] = "Elevated"
    elif metrics["coverage_gap_count"] > 0 or metrics["pending_review_count"] > 0:
        metrics["posture_label"] = "Guarded"
    elif metrics["active_instances"] > 0:
        metrics["posture_label"] = "Stable"

    shift_cards = []
    for shift in DASHBOARD_SHIFT_ORDER:
        bucket = shift_rollup[shift]
        readiness = round((bucket["actioned_items"] / bucket["total_items"]) * 100) if bucket["total_items"] > 0 else 0
        if bucket["operations"] == 0:
            status_label = "No active thread"
        elif bucket["exceptions"] > 0:
            status_label = "Exceptions tracked"
        elif readiness >= 80:
            status_label = "On cadence"
        else:
            status_label = "Building momentum"

        shift_cards.append(
            {
                "shift": shift,
                "window": DASHBOARD_SHIFT_WINDOWS[shift],
                "operations": bucket["operations"],
                "participants": bucket["participants"],
                "exceptions": bucket["exceptions"],
                "readiness": readiness,
                "status": status_label,
            }
        )

    network_watch = []
    for row in network_rows:
        status_value = row["overall_status"] or "UNKNOWN"
        address = row["address"] or "unknown"
        port_suffix = f":{row['port']}" if row["port"] is not None else ""
        state_since = row["last_state_change_at"].isoformat() if row["last_state_change_at"] else "unknown"
        network_watch.append(
            {
                "id": f"net-{row['id']}",
                "title": f"{row['name']} ({status_value})",
                "detail": f"{address}{port_suffix} | state since {state_since}",
                "tone": "network-down" if status_value == "DOWN" else "network-degraded",
            }
        )

    return {
        "operational_day": {
            "checklist_date": operational_context["operational_date"].isoformat(),
            "window_start": operational_context["window_start"].isoformat(),
            "window_end": operational_context["window_end"].isoformat(),
            "timezone": operational_context["timezone"],
            "boundary_time": operational_context["boundary_time"].isoformat(),
        },
        "command_metrics": metrics,
        "shift_cards": shift_cards,
        "checklist_threads": checklist_threads,
        "attention_queue": (network_watch + operation_watch)[:8],
        "handover_feed": handover_feed[:4],
        "notifications_unread": int(notifications_unread or 0),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

# --- Delete Checklist Instance ---
@router.delete("/instances/{instance_id}", status_code=status.HTTP_200_OK)
async def delete_checklist_instance(
    instance_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Delete a checklist instance (admin/manager only)"""
    try:
        # Only admin or manager can delete
        role = (current_user.get("role") or "").upper()
        if role not in ("ADMIN", "MANAGER"):
            raise HTTPException(status_code=403, detail="Insufficient permissions")

        # Check instance exists
        instance = ChecklistDBService.get_instance(instance_id)
        if not instance:
            raise HTTPException(status_code=404, detail="Checklist instance not found")

        _ensure_instance_access(instance, current_user)

        # Delete instance
        success = ChecklistDBService.delete_checklist_instance(instance_id)
        if not success:
            raise HTTPException(status_code=404, detail="Checklist instance not found or already deleted")

        # Emit ops event
        background_tasks.add_task(
            _emit_ops_event_async,
            {
                "event_type": "CHECKLIST_INSTANCE_DELETED",
                "entity_type": "CHECKLIST_INSTANCE",
                "entity_id": str(instance_id),
                "payload": {
                    "user_id": current_user["id"],
                    "username": current_user["username"]
                }
            }
        )

        return {
            "id": str(instance_id),
            "action": "deleted",
            "message": f"Checklist instance {instance_id} deleted successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error deleting checklist instance: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Helper function for async ops event emission (database version)
async def _emit_ops_event_async(ops_event: dict):
    """Emit ops event asynchronously (database version)"""
    try:
        OpsEventLogger.log_event(
            event_type=ops_event['event_type'],
            entity_type=ops_event['entity_type'],
            entity_id=UUID(ops_event['entity_id']),
            payload=ops_event.get('payload', {})
        )
        log.info(f"Ops event logged: {ops_event['event_type']}/{ops_event['entity_type']}/{ops_event['entity_id']}")
    except Exception as e:
        log.error(f"Failed to log ops event: {e}")

# --- Template Management ---
@router.get("/templates", response_model=List[ChecklistTemplateResponse])
async def get_templates(
    shift: Optional[str] = Query(None, regex="^(MORNING|AFTERNOON|NIGHT)$"),
    active_only: bool = True,
    section_id: Optional[str] = Query(None, description="Scope templates to a section (non-admins)") ,
    current_user: dict = Depends(get_current_user)
):
    """Get checklist templates"""
    try:
        if is_admin(current_user):
            effective_section = section_id
        else:
            effective_section = _require_user_section_id(current_user)

        templates = ChecklistDBService.list_templates(shift, active_only, effective_section)
        return templates

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error getting templates: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/templates/{template_id}", response_model=ChecklistTemplateResponse)
async def get_template_by_id(
    template_id: UUID,
    current_user: dict = Depends(get_current_user)
):
    """Get a specific checklist template by ID"""
    try:
        template = ChecklistDBService.get_template(template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")

        _ensure_template_access(template, current_user)

        return template
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error getting template: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/templates")
async def create_template(
    data: ChecklistTemplateCreate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Create a new checklist template with items and subitems"""
    try:
        if not is_admin(current_user) and not has_capability(current_user.get("role"), "MANAGE_TEMPLATES"):
            raise HTTPException(status_code=403, detail="Insufficient permissions to create templates")

        if is_admin(current_user):
            effective_section = _normalize_section_id(data.section_id)
            if not effective_section:
                raise HTTPException(status_code=400, detail="section_id is required for admin-created templates")
        else:
            effective_section = _require_user_section_id(current_user)

        # Prepare items data
        items_data = []
        if data.items:
            for item in data.items:
                item_dict = item.dict()
                items_data.append(item_dict)
        
        # Create template
        result = ChecklistDBService.create_template(
            name=data.name,
            shift=data.shift.value if hasattr(data.shift, 'value') else data.shift,
            description=data.description,
            is_active=data.is_active,
            created_by=current_user["id"],
            section_id=effective_section,
            items_data=items_data if items_data else None
        )
        
        # Emit ops event
        background_tasks.add_task(
            _emit_ops_event_async,
            {
                "event_type": "TEMPLATE_CREATED",
                "entity_type": "CHECKLIST_TEMPLATE",
                "entity_id": str(result['id']),
                "payload": {
                    "user_id": current_user["id"],
                    "username": current_user["username"],
                    "template_name": result['name'],
                    "shift": result['shift'],
                    "item_count": len(result.get('items', []))
                }
            }
        )
        
        return {
            "id": result['id'],
            "action": "created",
            "template": result,
            "message": f"Template '{result['name']}' created successfully with {len(result.get('items', []))} items"
        }
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error creating template: {e}")
        import traceback
        log.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/templates/{template_id}")
async def update_template(
    template_id: UUID,
    data: ChecklistTemplateUpdate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Update a checklist template (full or partial update)"""
    try:
        if not is_admin(current_user) and not has_capability(current_user.get("role"), "MANAGE_TEMPLATES"):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        
        # Verify template exists and check permissions
        template = ChecklistDBService.get_template(template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")

        _ensure_template_access(template, current_user)

        if is_admin(current_user):
            effective_section = _normalize_section_id(data.section_id) or _normalize_section_id(template.get("section_id"))
            if not effective_section:
                raise HTTPException(status_code=400, detail="section_id is required for checklist templates")
        else:
            effective_section = _require_user_section_id(current_user)

        effective_shift = data.shift.value if hasattr(data.shift, 'value') else (data.shift or template.get('shift'))
        if data.items is not None or data.shift is not None:
            effective_items = data.items if data.items is not None else template.get('items', [])
            ChecklistDBService.validate_template_payload_for_shift(
                effective_shift,
                [item.dict() if hasattr(item, 'dict') else item for item in effective_items],
            )
        
        # Update template fields
        success = ChecklistDBService.update_template(
            template_id=template_id,
            name=data.name,
            description=data.description,
            shift=data.shift.value if hasattr(data.shift, 'value') else data.shift,
            is_active=data.is_active,
            section_id=effective_section
        )
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update template")
        
        # Handle items and subitems update if provided
        if data.items is not None:
            # Get existing items to compare (including inactive ones for proper tracking)
            existing_template = ChecklistDBService.get_template(template_id)
            existing_items = {item['id']: item for item in existing_template.get('items', [])}
            
            # Get all items from database (including inactive) to identify items to soft delete
            try:
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT id FROM checklist_template_items 
                            WHERE template_id = %s
                        """, (template_id,))
                        all_item_ids = {str(row[0]) for row in cur.fetchall()}
            except Exception as e:
                log.error(f"Error fetching all item IDs: {e}")
                all_item_ids = set()
            
            # Get IDs of items mentioned in the update request
            mentioned_item_ids = {item.id for item in data.items if item.id}
            
            # Items to soft delete (exist in DB but not mentioned in update)
            items_to_soft_delete = all_item_ids - mentioned_item_ids
            
            # Soft delete items not mentioned in the request
            for item_id in items_to_soft_delete:
                try:
                    ChecklistDBService.soft_delete_template_item(UUID(item_id))
                    log.info(f"Soft deleted item {item_id} not mentioned in update")
                except Exception as e:
                    log.warning(f"Could not soft delete item {item_id}: {e}")
            
            # Process items by ID
            for item in data.items:
                if item.id and item.id in existing_items:
                    # Update existing item
                    existing_item = existing_items[item.id]
                    subitems_data = None
                    if item.subitems:
                        subitems_data = [subitem.dict() for subitem in item.subitems]
                    
                    ChecklistDBService.update_template_item(
                        item_id=UUID(item.id),
                        title=item.title,
                        description=item.description,
                        item_type=item.item_type,
                        is_required=item.is_required,
                        scheduled_time=item.scheduled_time,
                        notify_before_minutes=item.notify_before_minutes,
                        severity=item.severity,
                        sort_order=item.sort_order,
                        subitems=subitems_data,
                        scheduled_events=[event.dict() for event in item.scheduled_events] if item.scheduled_events is not None else None,
                        created_by=current_user["id"],
                    )
                else:
                    # Create new item
                    ChecklistDBService.create_template_items(
                        template_id=template_id,
                        items_data=[item.dict()]
                    )
            
            # Note: Items not mentioned in the request are soft deleted
            # This maintains data integrity while allowing clean template management
        
        # Fetch updated template
        updated_template = ChecklistDBService.get_template(template_id)
        
        # Emit ops event
        background_tasks.add_task(
            _emit_ops_event_async,
            {
                "event_type": "TEMPLATE_UPDATED",
                "entity_type": "CHECKLIST_TEMPLATE",
                "entity_id": str(template_id),
                "payload": {
                    "user_id": current_user["id"],
                    "username": current_user["username"],
                    "template_name": data.name or template['name'],
                    "items_updated": data.items is not None
                }
            }
        )
        
        return {
            "id": str(template_id),
            "action": "updated",
            "template": updated_template,
            "message": f"Template updated successfully" + (" with items and subitems" if data.items is not None else "")
        }
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error updating template: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/templates/{template_id}")
async def delete_template(
    template_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Delete a checklist template (soft delete - archives template)"""
    try:
        if not is_admin(current_user) and not has_capability(current_user.get("role"), "MANAGE_TEMPLATES"):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        
        template = ChecklistDBService.get_template(template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")

        _ensure_template_access(template, current_user)
        
        # Deactivate template instead of hard delete (safer - preserves audit trail)
        success = ChecklistDBService.update_template(
            template_id=template_id,
            is_active=False
        )
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to delete template")
        
        template_name = template['name']
        
        # Emit ops event
        background_tasks.add_task(
            _emit_ops_event_async,
            {
                "event_type": "TEMPLATE_DELETED",
                "entity_type": "CHECKLIST_TEMPLATE",
                "entity_id": str(template_id),
                "payload": {
                    "user_id": current_user["id"],
                    "username": current_user["username"],
                    "template_name": template_name
                }
            }
        )
        
        return {
            "id": str(template_id),
            "action": "deleted",
            "message": f"Template '{template_name}' archived successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error deleting template: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/templates/{template_id}/items")
async def add_template_item(
    template_id: UUID,
    data: ChecklistTemplateItemCreate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Add a new item to a template"""
    try:
        if not is_admin(current_user) and not has_capability(current_user.get("role"), "MANAGE_TEMPLATES"):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        
        # Verify template exists
        template = ChecklistDBService.get_template(template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")

        _ensure_template_access(template, current_user)

        # Prepare subitems data
        subitems_data = None
        if data.subitems:
            subitems_data = [s.dict() for s in data.subitems]
        
        result = ChecklistDBService.add_template_item(
            template_id=template_id,
            title=data.title,
            description=data.description,
            item_type=data.item_type.value if hasattr(data.item_type, 'value') else data.item_type,
            is_required=data.is_required,
            scheduled_time=data.scheduled_time,
            notify_before_minutes=data.notify_before_minutes,
            severity=data.severity,
            sort_order=data.sort_order,
            subitems_data=subitems_data,
            scheduled_events_data=[event.dict() for event in data.scheduled_events] if data.scheduled_events else None,
            created_by=current_user["id"],
        )
        
        # Emit ops event
        background_tasks.add_task(
            _emit_ops_event_async,
            {
                "event_type": "TEMPLATE_ITEM_ADDED",
                "entity_type": "CHECKLIST_ITEM",
                "entity_id": str(result['id']),
                "payload": {
                    "template_id": str(template_id),
                    "user_id": current_user["id"],
                    "username": current_user["username"],
                    "item_title": result['title'],
                    "subitem_count": len(result.get('subitems', []))
                }
            }
        )
        
        return {
            "id": result['id'],
            "template_id": str(template_id),
            "action": "created",
            "item": result,
            "message": f"Item '{result['title']}' added with {len(result.get('subitems', []))} subitems"
        }
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error adding item: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/templates/{template_id}/items/{item_id}")
async def update_template_item(
    template_id: UUID,
    item_id: UUID,
    data: ChecklistTemplateItemUpdate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Update a template item"""
    try:
        if not is_admin(current_user) and not has_capability(current_user.get("role"), "MANAGE_TEMPLATES"):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        
        # Verify template exists
        template = ChecklistDBService.get_template(template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")

        _ensure_template_access(template, current_user)

        # Update item
        success = ChecklistDBService.update_template_item(
            item_id=item_id,
            title=data.title,
            description=data.description,
            item_type=data.item_type.value if hasattr(data.item_type, 'value') else data.item_type if data.item_type else None,
            is_required=data.is_required,
            scheduled_time=data.scheduled_time,
            notify_before_minutes=data.notify_before_minutes,
            severity=data.severity,
            sort_order=data.sort_order,
            scheduled_events=[event.dict() for event in data.scheduled_events] if data.scheduled_events is not None else None,
            created_by=current_user["id"],
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="Item not found")
        
        # Emit ops event
        background_tasks.add_task(
            _emit_ops_event_async,
            {
                "event_type": "TEMPLATE_ITEM_UPDATED",
                "entity_type": "CHECKLIST_ITEM",
                "entity_id": str(item_id),
                "payload": {
                    "template_id": str(template_id),
                    "user_id": current_user["id"],
                    "username": current_user["username"]
                }
            }
        )
        
        return {
            "id": str(item_id),
            "template_id": str(template_id),
            "action": "updated",
            "message": "Item updated successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error updating item: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/templates/{template_id}/items/{item_id}")
async def delete_template_item(
    template_id: UUID,
    item_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Soft delete a template item (sets is_active to false)"""
    try:
        if not is_admin(current_user) and not has_capability(current_user.get("role"), "MANAGE_TEMPLATES"):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        
        # Verify template exists
        template = ChecklistDBService.get_template(template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")

        _ensure_template_access(template, current_user)

        # Soft delete item
        success = ChecklistDBService.soft_delete_template_item(item_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="Item not found")
        
        # Emit ops event
        background_tasks.add_task(
            _emit_ops_event_async,
            {
                "event_type": "TEMPLATE_ITEM_SOFT_DELETED",
                "entity_type": "CHECKLIST_ITEM",
                "entity_id": str(item_id),
                "payload": {
                    "template_id": str(template_id),
                    "user_id": current_user["id"],
                    "username": current_user["username"]
                }
            }
        )
        
        return {
            "id": str(item_id),
            "template_id": str(template_id),
            "action": "deleted",
            "message": "Item deleted successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error deleting item: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/templates/{template_id}/items/{item_id}/subitems")
async def add_subitem(
    template_id: UUID,
    item_id: UUID,
    data: ChecklistTemplateSubitemBase,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Add a subitem to a template item"""
    try:
        if not is_admin(current_user) and not has_capability(current_user.get("role"), "MANAGE_TEMPLATES"):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        
        # Verify template exists
        template = ChecklistDBService.get_template(template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")

        _ensure_template_access(template, current_user)

        result = ChecklistDBService.add_template_subitem(
            item_id=item_id,
            title=data.title,
            description=data.description,
            item_type=data.item_type.value if hasattr(data.item_type, 'value') else data.item_type,
            is_required=data.is_required,
            scheduled_time=data.scheduled_time,
            notify_before_minutes=data.notify_before_minutes,
            severity=data.severity,
            sort_order=data.sort_order
        )
        
        # Emit ops event
        background_tasks.add_task(
            _emit_ops_event_async,
            {
                "event_type": "TEMPLATE_SUBITEM_ADDED",
                "entity_type": "CHECKLIST_SUBITEM",
                "entity_id": str(result['id']),
                "payload": {
                    "template_id": str(template_id),
                    "item_id": str(item_id),
                    "user_id": current_user["id"],
                    "username": current_user["username"]
                }
            }
        )
        
        return {
            "id": result['id'],
            "item_id": str(item_id),
            "action": "created",
            "subitem": result,
            "message": f"Subitem '{result['title']}' added successfully"
        }
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error adding subitem: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/templates/{template_id}/items/{item_id}/subitems/{subitem_id}")
async def update_subitem(
    template_id: UUID,
    item_id: UUID,
    subitem_id: UUID,
    data: ChecklistTemplateSubitemBase,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Update a template subitem"""
    try:
        if not is_admin(current_user) and not has_capability(current_user.get("role"), "MANAGE_TEMPLATES"):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        
        # Verify template exists
        template = ChecklistDBService.get_template(template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")

        _ensure_template_access(template, current_user)

        success = ChecklistDBService.update_template_subitem(
            subitem_id=subitem_id,
            title=data.title,
            description=data.description,
            item_type=data.item_type.value if hasattr(data.item_type, 'value') else data.item_type,
            is_required=data.is_required,
            scheduled_time=data.scheduled_time,
            notify_before_minutes=data.notify_before_minutes,
            severity=data.severity,
            sort_order=data.sort_order
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="Subitem not found")
        
        # Emit ops event
        background_tasks.add_task(
            _emit_ops_event_async,
            {
                "event_type": "TEMPLATE_SUBITEM_UPDATED",
                "entity_type": "CHECKLIST_SUBITEM",
                "entity_id": str(subitem_id),
                "payload": {
                    "template_id": str(template_id),
                    "item_id": str(item_id),
                    "user_id": current_user["id"],
                    "username": current_user["username"]
                }
            }
        )
        
        return {
            "id": str(subitem_id),
            "item_id": str(item_id),
            "action": "updated",
            "message": "Subitem updated successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error updating subitem: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/templates/{template_id}/items/{item_id}/subitems/{subitem_id}")
async def delete_subitem(
    template_id: UUID,
    item_id: UUID,
    subitem_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Delete a template subitem"""
    try:
        if not is_admin(current_user) and not has_capability(current_user.get("role"), "MANAGE_TEMPLATES"):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        
        # Verify template exists
        template = ChecklistDBService.get_template(template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")

        _ensure_template_access(template, current_user)

        success = ChecklistDBService.delete_template_subitem(subitem_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="Subitem not found")
        
        # Emit ops event
        background_tasks.add_task(
            _emit_ops_event_async,
            {
                "event_type": "TEMPLATE_SUBITEM_DELETED",
                "entity_type": "CHECKLIST_SUBITEM",
                "entity_id": str(subitem_id),
                "payload": {
                    "template_id": str(template_id),
                    "item_id": str(item_id),
                    "user_id": current_user["id"],
                    "username": current_user["username"]
                }
            }
        )
        
        return {
            "id": str(subitem_id),
            "item_id": str(item_id),
            "action": "deleted",
            "message": "Subitem deleted successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error deleting subitem: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Instance Management ---
@router.post("/instances")
async def create_checklist_instance(
    data: ChecklistInstanceCreate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Create a new checklist instance for a shift"""
    log.info(f"🚀 Create checklist instance request: {data}")
    try:
        template = None
        desired_section = None
        if data.template_id:
            template = ChecklistDBService.get_template(data.template_id)
            if not template:
                raise HTTPException(status_code=404, detail="Template not found")
            _ensure_template_access(
                template,
                current_user,
                forbidden_detail="Insufficient permissions for this template's section",
            )
            desired_section = _normalize_section_id(template.get("section_id"))
            if not desired_section:
                raise HTTPException(status_code=409, detail="Template is missing section alignment")

        requested_section = _normalize_section_id(data.section_id)
        if template:
            if requested_section and requested_section != desired_section:
                raise HTTPException(status_code=400, detail="Instance section must match the selected template section")
        elif is_admin(current_user):
            desired_section = requested_section
            if not desired_section:
                raise HTTPException(
                    status_code=400,
                    detail="section_id is required when creating an instance without a template",
                )
        else:
            desired_section = _require_user_section_id(current_user)
            if requested_section and requested_section != desired_section:
                raise HTTPException(status_code=403, detail="Instances must be created in your own section")

        result = ChecklistDBService.create_checklist_instance(
            checklist_date=data.checklist_date,
            shift=data.shift.value if hasattr(data.shift, 'value') else data.shift,
            created_by=current_user["id"],
            created_by_username=current_user["username"],
            template_id=data.template_id,
            section_id=desired_section
        )
        
        # Emit ops event asynchronously for new instances only
        if result.get("message") == "New instance created":
            background_tasks.add_task(
                _emit_ops_event_async,
                {
                    "event_type": "CHECKLIST_CREATED",
                    "entity_type": "CHECKLIST_INSTANCE", 
                    "entity_id": str(result["id"]),
                    "payload": {
                        "user_id": current_user["id"],
                        "username": current_user["username"],
                        "checklist_date": str(data.checklist_date),
                        "shift": data.shift.value if hasattr(data.shift, 'value') else data.shift,
                        "template_id": str(data.template_id)
                    }
                }
            )
            background_tasks.add_task(
                websocket_manager.broadcast_instance_created,
                str(result["id"]),
                current_user["id"]
            )
        
        # Get the full instance data (already included in result)
        instance = result["instance"]
        
        response_data = {
            "id": result["id"],
            "instance": instance,
            "message": result.get("message", ""),
            "effects": {
                "background_task": True,
                "notification_created": True
            }
        }
        log.info(f"📤 Returning response: {response_data}")
        return response_data
    except ValueError as e:
        log.error(f"Error creating instance: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error(f"Error creating instance: {e}")
        # Debug: Log the actual error and response
        import traceback
        log.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/instances", response_model=List[ChecklistInstanceResponse])
async def get_all_checklist_instances(
    start_date: Optional[date] = Query(None, description="Start date for filtering"),
    end_date: Optional[date] = Query(None, description="End date for filtering"),
    shift: Optional[str] = Query(None, regex="^(MORNING|AFTERNOON|NIGHT)$", description="Filter by shift"),
    current_user: dict = Depends(get_current_user)
):
    """Get all checklist instances with optional date range and shift filtering"""
    try:
        query_start_date = start_date or date.today()
        query_end_date = end_date or query_start_date

        if query_start_date > query_end_date:
            raise HTTPException(status_code=400, detail="start_date cannot be after end_date")

        effective_section = None if is_admin(current_user) else _normalize_section_id(current_user.get("section_id"))
        if not is_admin(current_user) and not effective_section:
            return []

        instances, _ = ChecklistDBService.get_paginated_instance_summaries(
            query_start_date,
            query_end_date,
            shift,
            section_id=effective_section,
            page=1,
            limit=10_000,
            sort_by="checklist_date",
            sort_order="desc",
        )

        return instances
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error getting all checklist instances: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/instances/paginated", response_model=PaginatedResponse)
async def get_paginated_checklist_instances(
    start_date: Optional[date] = Query(None, description="Start date for filtering"),
    end_date: Optional[date] = Query(None, description="End date for filtering"),
    shift: Optional[str] = Query(None, regex="^(MORNING|AFTERNOON|NIGHT)$", description="Filter by shift"),
    status: Optional[str] = Query(
        None,
        pattern="^(OPEN|IN_PROGRESS|PENDING_REVIEW|COMPLETED|COMPLETED_WITH_EXCEPTIONS|INCOMPLETE)$",
        description="Filter by checklist status",
    ),
    search: Optional[str] = Query(None, min_length=1, max_length=120, description="Search by template, shift, status, date, or ID"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(18, ge=1, le=100, description="Items per page"),
    sort_by: str = Query("checklist_date", pattern="^(checklist_date|shift|status)$", description="Field to sort by"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$", description="Sort direction"),
    current_user: dict = Depends(get_current_user)
):
    """Get checklist instances with date range filtering, sorting, and pagination."""
    try:
        query_end_date = end_date or date.today()
        query_start_date = start_date or (query_end_date - timedelta(days=30))

        if query_start_date > query_end_date:
            raise HTTPException(status_code=400, detail="start_date cannot be after end_date")

        effective_section = None if is_admin(current_user) else _normalize_section_id(current_user.get("section_id"))
        if not is_admin(current_user) and not effective_section:
            return {
                "items": [],
                "total": 0,
                "page": page,
                "pages": 1,
                "has_next": False,
                "has_prev": page > 1
            }

        page_items, total = ChecklistDBService.get_paginated_instance_summaries(
            query_start_date,
            query_end_date,
            shift,
            status=status,
            search=search,
            section_id=effective_section,
            page=page,
            limit=limit,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        pages = max((total + limit - 1) // limit, 1)

        return {
            "items": page_items,
            "total": total,
            "page": page,
            "pages": pages,
            "has_next": page < pages,
            "has_prev": page > 1
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error getting paginated checklist instances: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/instances/today/coverage")
async def get_today_checklist_coverage(
    current_user: dict = Depends(get_current_user)
):
    """Get lightweight per-shift checklist counts for the current operational day."""
    try:
        async with get_async_connection() as conn:
            operational_context = await _get_operational_day_context(conn)

        effective_section = None if is_admin(current_user) else _normalize_section_id(current_user.get("section_id"))
        if not is_admin(current_user) and not effective_section:
            return {"MORNING": 0, "AFTERNOON": 0, "NIGHT": 0}

        return ChecklistDBService.get_shift_coverage_for_date(
            operational_context["operational_date"],
            section_id=effective_section,
        )
    except Exception as e:
        log.error(f"Error getting operational-day checklist coverage: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/instances/today")
async def get_todays_checklists(
    current_user: dict = Depends(get_current_user)
):
    """Get checklist instances for the current operational day."""
    try:
        async with get_async_connection() as conn:
            operational_context = await _get_operational_day_context(conn)

        instances = ChecklistDBService.get_instances_by_date(operational_context["operational_date"])

        instances = [instance for instance in instances if _instance_visible_to_user(instance, current_user)]

        return instances
    except Exception as e:
        log.error(f"Error getting operational-day checklists: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/instances/{instance_id}")
async def get_checklist_instance(
    instance_id: UUID,
    current_user: dict = Depends(get_current_user)
):
    """Get checklist instance by ID"""
    try:
        instance = ChecklistDBService.get_instance(instance_id)
        if not instance:
            raise ValueError(f"Instance {instance_id} not found")
        _ensure_instance_access(instance, current_user)
        return instance
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error getting instance: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/instances/{instance_id}/join")
async def join_checklist_instance(
    instance_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Join a checklist instance as participant"""
    try:
        # Ensure user has access to this instance's section (unless admin)
        instance = ChecklistDBService.get_instance(instance_id)
        if not instance:
            raise HTTPException(status_code=404, detail="Checklist instance not found")
        _ensure_instance_access(
            instance,
            current_user,
            forbidden_detail="Insufficient permissions to join this checklist",
        )

        # Join checklist using database service
        result = ChecklistDBService.add_participant(
            instance_id,
            current_user["id"],
            current_user["username"]
        )
        
        # Emit ops event asynchronously
        if result:
            background_tasks.add_task(
                _emit_ops_event_async,
                {
                    "event_type": "PARTICIPANT_JOINED",
                    "entity_type": "CHECKLIST_INSTANCE",
                    "entity_id": str(instance_id),
                    "payload": {
                        "user_id": current_user["id"],
                        "username": current_user["username"]
                    }
                }
            )
            
            # Broadcast real-time update to all connected clients
            background_tasks.add_task(
                websocket_manager.broadcast_instance_joined,
                str(instance_id),
                current_user["id"]
            )
        
        # Get updated instance
        instance = ChecklistDBService.get_instance(instance_id)
        
        return {
            "instance": instance,
            "effects": disclose_effects(EffectType.CHECKLIST_JOINED).to_dict()
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error joining checklist: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Item Management ---
@router.patch("/instances/{instance_id}/items/{item_id}")
async def update_checklist_item(
    instance_id: UUID,
    item_id: UUID,
    update: ChecklistItemUpdate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Update checklist item status"""
    try:
        # Ensure user has access to this instance's section (unless admin)
        instance = ChecklistDBService.get_instance(instance_id)
        if not instance:
            raise HTTPException(status_code=404, detail="Checklist instance not found")
        _ensure_instance_access(
            instance,
            current_user,
            forbidden_detail="Insufficient permissions to update items in this checklist",
        )
        
        instance_item = next(
            (item for item in instance.get("items", []) if str(item.get("id")) == str(item_id)),
            None
        )

        # Update item using database service
        result = ChecklistDBService.update_item_status(
            item_id=item_id,
            new_status=update.status.value if hasattr(update.status, 'value') else update.status,
            user_id=current_user["id"],
            username=current_user["username"],
            reason=update.reason,
            comment=update.comment or update.notes
        )
        
        # Emit ops event asynchronously
        background_tasks.add_task(
            _emit_ops_event_async,
            {
                "event_type": f"ITEM_{result['item']['status'].upper()}",
                "entity_type": "CHECKLIST_ITEM",
                "entity_id": str(item_id),
                "payload": {
                    "instance_id": str(instance_id),
                    "user_id": current_user["id"],
                    "username": current_user["username"],
                    "reason": update.reason,
                    "comment": update.comment or update.notes
                }
            }
        )
        
        # Broadcast real-time item update to all connected clients
        background_tasks.add_task(
            websocket_manager.broadcast_item_update,
            str(instance_id),
            str(item_id),
            result['item']['status'],
            current_user["id"],
            result['item'].get('previous_status', 'PENDING')
        )
        
        # Notify all participants of item action
        if background_tasks and result.get("item"):
            item_data = result["item"]
            item_title = (
                item_data.get("title")
                or (instance_item.get("title") if instance_item else None)
                or "Unknown"
            )
            background_tasks.add_task(
                NotificationService.notify_participants_item_action,
                instance_id=str(instance_id),
                item_id=str(item_id),
                item_title=item_title,
                action=item_data.get('status', 'UPDATED'),
                username=current_user.get("username", "Unknown")
            )
        
        # Get updated instance
        instance = ChecklistDBService.get_instance(instance_id)
        
        return {
            "instance": instance,
            "effects": disclose_effects(EffectType.ITEM_UPDATED).to_dict()
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error updating item: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- SUBITEM MANAGEMENT (Hierarchical Checklists) ---

@router.post("/instances/{instance_id}/items/{item_id}/start-work")
async def start_working_on_item(
    instance_id: UUID,
    item_id: UUID,
    background_tasks: BackgroundTasks,
    payload: Optional[ItemStartWorkRequest] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Start working on a checklist item.
    If the item has subitems, returns the subitems to be completed sequentially.
    Updates item status to IN_PROGRESS.
    """
    try:
        # Verify instance exists and user has access
        instance = ChecklistDBService.get_instance(instance_id)
        if not instance:
            raise HTTPException(status_code=404, detail="Checklist instance not found")
        _ensure_instance_access(instance, current_user)
        
        # Find the item in the instance
        item = None
        for i in instance['items']:
            if UUID(i['id']) == item_id:
                item = i
                break
        
        if not item:
            raise HTTPException(status_code=404, detail="Item not found in instance")
        
        # Update item status to IN_PROGRESS
        ChecklistDBService.update_item_status(
            item_id=item_id,
            new_status='IN_PROGRESS',
            user_id=current_user["id"],
            username=current_user["username"],
            comment=payload.comment if payload else None,
        )
        
        # Get subitems for this item
        subitems = ChecklistDBService.get_subitems_for_item(item_id)
        next_subitem = ChecklistDBService.get_next_pending_subitem(item_id)
        subitem_stats = ChecklistDBService.get_subitem_completion_status(item_id)
        
        # Build response
        response = {
            "item_id": str(item_id),
            "item_title": item.get('title', 'Unknown'),
            "item_status": "IN_PROGRESS",
            "has_subitems": subitem_stats['has_subitems'],
            "subitems": subitems,
            "next_subitem": next_subitem,
            "subitem_count": subitem_stats['total'],
            "completed_subitem_count": subitem_stats['completed'],
            "subitem_status": subitem_stats['status']
        }
        
        # Emit ops event
        background_tasks.add_task(
            _emit_ops_event_async,
            {
                "event_type": "ITEM_STARTED",
                "entity_type": "CHECKLIST_ITEM",
                "entity_id": str(item_id),
                "payload": {
                    "instance_id": str(instance_id),
                    "user_id": current_user["id"],
                    "username": current_user["username"],
                    "comment": payload.comment if payload else None,
                    "has_subitems": subitem_stats['has_subitems'],
                    "subitem_count": subitem_stats['total']
                }
            }
        )
        
        # Notify all participants of item action
        if background_tasks:
            background_tasks.add_task(
                NotificationService.notify_participants_item_action,
                instance_id=str(instance_id),
                item_id=str(item_id),
                item_title=item.get('title', 'Unknown'),
                action='IN_PROGRESS',
                username=current_user.get("username", "Unknown")
            )
        
        return response

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error starting work on item: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/instances/{instance_id}/items/{item_id}/subitems")
async def get_item_subitems(
    instance_id: UUID,
    item_id: UUID,
    current_user: dict = Depends(get_current_user)
):
    """Get all subitems for a checklist item"""
    try:
        # Verify instance exists
        instance = ChecklistDBService.get_instance(instance_id)
        if not instance:
            raise HTTPException(status_code=404, detail="Checklist instance not found")
        _ensure_instance_access(instance, current_user)
        
        # Get subitems
        subitems = ChecklistDBService.get_subitems_for_item(item_id)
        next_subitem = ChecklistDBService.get_next_pending_subitem(item_id)
        stats = ChecklistDBService.get_subitem_completion_status(item_id)
        
        return {
            "item_id": str(item_id),
            "subitems": subitems,
            "next_subitem": next_subitem,
            "stats": {
                "total": stats['total'],
                "completed": stats['completed'],
                "skipped": stats['skipped'],
                "failed": stats['failed'],
                "pending": stats['pending'],
                "in_progress": stats['in_progress'],
                "all_actioned": stats['all_actioned'],
                "status": stats['status']
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error getting subitems: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/instances/{instance_id}/items/{item_id}/subitems/{subitem_id}")
async def update_subitem_status(
    instance_id: UUID,
    item_id: UUID,
    subitem_id: UUID,
    update: SubitemCompletionRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """
    Update a subitem status (COMPLETED, SKIPPED, or FAILED).
    This is called during sequential subitem completion.
    """
    try:
        # Verify instance exists
        instance = ChecklistDBService.get_instance(instance_id)
        if not instance:
            raise HTTPException(status_code=404, detail="Checklist instance not found")
        _ensure_instance_access(instance, current_user)
        
        # Get current subitem status for transition validation
        current_subitem = ChecklistDBService.get_subitem_by_id(subitem_id)
        if not current_subitem:
            raise HTTPException(status_code=404, detail="Subitem not found")
        
        # Validate state transition
        new_status = update.status.value if hasattr(update.status, 'value') else update.status
        if not is_item_transition_allowed(current_subitem['status'], new_status, current_user.get('role', 'USER')):
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid state transition from {current_subitem['status']} to {new_status}"
            )
        
        # Update subitem status
        subitem_result = ChecklistDBService.update_subitem_status(
            subitem_id=subitem_id,
            new_status=new_status,
            user_id=current_user["id"],
            username=current_user["username"],
            reason=update.reason,
            comment=update.comment
        )
        
        # Get updated subitems data
        subitems = ChecklistDBService.get_subitems_for_item(item_id)
        next_subitem = ChecklistDBService.get_next_pending_subitem(item_id)
        stats = ChecklistDBService.get_subitem_completion_status(item_id)
        
        # Determine if all subitems are actioned
        all_subitems_done = stats['all_actioned']
        
        # Emit ops event
        background_tasks.add_task(
            _emit_ops_event_async,
            {
                "event_type": f"SUBITEM_{subitem_result['status'].upper()}",
                "entity_type": "CHECKLIST_SUBITEM",
                "entity_id": str(subitem_id),
                "payload": {
                    "instance_id": str(instance_id),
                    "item_id": str(item_id),
                    "user_id": current_user["id"],
                    "username": current_user["username"],
                    "status": subitem_result['status'],
                    "reason": update.reason,
                    "all_subitems_done": all_subitems_done
                }
            }
        )
        
        # Broadcast real-time subitem update to all connected clients
        background_tasks.add_task(
            websocket_manager.broadcast_instance_update,
            str(instance_id),
            'SUBITEM_UPDATED',
            {
                'subitem_id': str(subitem_id),
                'item_id': str(item_id),
                'status': subitem_result['status'],
                'user_id': current_user["id"],
                'all_subitems_done': all_subitems_done
            }
        )
        
        # Notify all participants of subitem action
        if background_tasks and subitem_result:
            await NotificationService.notify_participants_subitem_action(
                instance_id=str(instance_id),
                item_id=str(item_id),
                subitem_id=str(subitem_id),
                subitem_title=current_subitem.get('title', 'Unknown'),
                action=subitem_result['status'],
                username=current_user.get("username", "Unknown")
            )
        
        return {
            "subitem_id": str(subitem_id),
            "status": subitem_result['status'],
            "next_subitem": next_subitem,
            "all_subitems_done": all_subitems_done,
            "subitems": subitems,
            "stats": {
                "total": stats['total'],
                "completed": stats['completed'],
                "skipped": stats['skipped'],
                "failed": stats['failed'],
                "pending": stats['pending'],
                "in_progress": stats['in_progress'],
                "all_actioned": stats['all_actioned'],
                "status": stats['status']
            }
        }
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error updating subitem: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/instances/{instance_id}/items/{item_id}/completion-summary")
async def get_item_completion_summary(
    instance_id: UUID,
    item_id: UUID,
    current_user: dict = Depends(get_current_user)
):
    """
    Get the completion summary for an item after all subitems are done.
    Shows subitem statuses and who actioned them.
    """
    try:
        # Verify instance exists
        instance = ChecklistDBService.get_instance(instance_id)
        if not instance:
            raise HTTPException(status_code=404, detail="Checklist instance not found")
        _ensure_instance_access(instance, current_user)
        
        # Get subitems
        subitems = ChecklistDBService.get_subitems_for_item(item_id)
        stats = ChecklistDBService.get_subitem_completion_status(item_id)
        
        return {
            "item_id": str(item_id),
            "has_subitems": stats['has_subitems'],
            "subitems": subitems,
            "stats": {
                "total": stats['total'],
                "completed": stats['completed'],
                "skipped": stats['skipped'],
                "failed": stats['failed']
            },
            "summary": {
                "all_completed": stats['completed'] == stats['total'],
                "all_actioned": stats['all_actioned'],
                "status": stats['status'],
                "can_complete_item": stats['all_actioned'] and (stats['failed'] == 0)
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error getting completion summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/instances/{instance_id}/stats", response_model=ChecklistStats)
async def get_checklist_stats(
    instance_id: UUID,
    current_user: dict = Depends(get_current_user)
):
    """Get statistics for a checklist instance"""
    try:
        instance = ChecklistDBService.get_instance(instance_id)
        if not instance:
            raise ValueError(f"Instance {instance_id} not found")
        _ensure_instance_access(instance, current_user)

        total_items = len(instance["items"])
        completed_items = sum(1 for item in instance["items"] 
                            if item["status"] == "COMPLETED")
        skipped_items = sum(1 for item in instance["items"] 
                          if item["status"] == "SKIPPED")
        failed_items = sum(1 for item in instance["items"] 
                         if item["status"] == "FAILED")
        pending_items = total_items - completed_items - skipped_items - failed_items
        
        required_items = sum(1 for item in instance["items"] 
                           if item["template_item"]["is_required"])
        completed_required = sum(1 for item in instance["items"] 
                               if item["template_item"]["is_required"] 
                               and item["status"] == "COMPLETED")
        
        return ChecklistStats(
            total_items=total_items,
            completed_items=completed_items,
            skipped_items=skipped_items,
            failed_items=failed_items,
            pending_items=pending_items,
            completion_percentage=round((completed_items / total_items * 100) 
                                      if total_items > 0 else 0, 1),
            required_completion_percentage=round((completed_required / required_items * 100) 
                                               if required_items > 0 else 0, 1),
            estimated_time_remaining_minutes=instance["statistics"]["time_remaining_minutes"]
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error getting stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Handover Notes ---
@router.post("/handover-notes")
async def create_handover_note(
    data: HandoverNoteCreate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Create a handover note"""
    try:
        # Use provided from_instance_id or find user's current checklist
        from_instance_id = data.from_instance_id
        
        if not from_instance_id:
            # Find user's current checklist for the operational day if not provided.
            async with get_async_connection() as conn:
                operational_context = await _get_operational_day_context(conn)

            effective_section = None if is_admin(current_user) else _normalize_section_id(current_user.get("section_id"))
            if not is_admin(current_user) and not effective_section:
                raise HTTPException(status_code=403, detail="Your profile is not assigned to a section")

            instances, _ = ChecklistDBService.get_paginated_instance_summaries(
                operational_context["operational_date"],
                operational_context["operational_date"],
                section_id=effective_section,
                page=1,
                limit=10_000,
                sort_by="shift",
                sort_order="asc",
            )
            current_instance = next(
                (inst for inst in instances 
                 if inst["status"] in ["IN_PROGRESS", "PENDING_REVIEW"]),
                instances[0] if instances else None
            )
            
            if not current_instance:
                raise HTTPException(status_code=400, 
                                  detail="No active checklist found for user. Please start a checklist first or provide a specific checklist instance.")
            
            from_instance_id = current_instance["id"]
        else:
            source_instance = ChecklistDBService.get_instance(from_instance_id)
            if not source_instance:
                raise HTTPException(status_code=404, detail="Checklist instance not found")
            _ensure_instance_access(source_instance, current_user)
        
        # Create handover note (returns minimal data + deferred ops event)
        result = await ChecklistService.create_handover_note(
            from_instance_id=from_instance_id,
            content=data.content,
            priority=data.priority,
            user_id=current_user["id"],
            to_shift=data.to_shift,
            to_date=data.to_date
        )
        
        # Emit ops event asynchronously
        background_tasks.add_task(
            ChecklistService.emit_ops_event_async,
            **result["ops_event"]
        )
        
        return result["instance"]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error creating handover note: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/instances/{instance_id}/handover-notes")
async def get_handover_notes_for_instance(
    instance_id: UUID,
    include_outgoing: bool = Query(True, description="Include notes FROM this instance"),
    include_incoming: bool = Query(True, description="Include notes TO this instance"),
    current_user: dict = Depends(get_current_user)
):
    """Get handover notes for a specific checklist instance"""
    try:
        from app.checklists.handover_service import HandoverService
        
        # Verify user has access to this instance
        instance = ChecklistDBService.get_instance(instance_id)
        if not instance:
            raise HTTPException(status_code=404, detail="Checklist instance not found")
        _ensure_instance_access(instance, current_user)
        
        # Get handover notes
        notes = await HandoverService.get_handover_notes_for_instance(
            instance_id, include_outgoing, include_incoming
        )
        
        return {
            "instance_id": str(instance_id),
            "handover_notes": notes,
            "count": len(notes)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error getting handover notes: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/handover-notes/{note_id}/acknowledge")
async def acknowledge_handover_note(
    note_id: UUID,
    current_user: dict = Depends(get_current_user)
):
    """Acknowledge a handover note"""
    try:
        from app.checklists.handover_service import HandoverService

        note_section_id = await _get_handover_note_section_id(note_id)
        _ensure_section_access(
            note_section_id,
            current_user,
            missing_detail="Handover note is missing a section assignment",
        )

        result = await HandoverService.acknowledge_handover_note(
            note_id, UUID(current_user["id"])
        )
        
        return {
            "id": str(result["id"]),
            "acknowledged_by": str(result["acknowledged_by"]),
            "acknowledged_at": result["acknowledged_at"],
            "message": "Handover note acknowledged successfully"
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error acknowledging handover note: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/handover-notes/{note_id}/resolve")
async def resolve_handover_note(
    note_id: UUID,
    resolution_notes: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Resolve a handover note"""
    try:
        from app.checklists.handover_service import HandoverService

        note_section_id = await _get_handover_note_section_id(note_id)
        _ensure_section_access(
            note_section_id,
            current_user,
            missing_detail="Handover note is missing a section assignment",
        )

        result = await HandoverService.resolve_handover_note(
            note_id, UUID(current_user["id"]), resolution_notes
        )
        
        return {
            "id": str(result["id"]),
            "resolved_by": str(result["resolved_by"]),
            "resolved_at": result["resolved_at"],
            "resolution_notes": result["resolution_notes"],
            "message": "Handover note resolved successfully"
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error resolving handover note: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/authorization-policy")
async def get_authorization_policy():
    """Expose role → capability mapping (read-only)."""
    from app.core.authorization import get_authorization_policy
    return get_authorization_policy()

@router.get("/state-policy")
async def get_state_policy():
    """Expose checklist and item state transition policies (read-only)."""
    return {
        "item": get_item_transition_policy(),
        "checklist": get_checklist_transition_policy(),
    }

# --- Performance Metrics ---
@router.get("/performance/metrics", response_model=List[ShiftPerformance])
async def get_performance_metrics(
    start_date: Optional[date] = Query(None, description="Start date (default: 30 days ago)"),
    end_date: Optional[date] = Query(None, description="End date (default: today)"),
    user_id: Optional[UUID] = Query(None, description="Filter by user"),
    current_user: dict = Depends(get_current_user)
):
    """Get shift performance metrics"""
    try:
        if not start_date:
            start_date = date.today() - timedelta(days=30)
        if not end_date:
            end_date = date.today()
        
        metrics = await ChecklistDBService.get_shift_performance_metrics(
            start_date=start_date,
            end_date=end_date,
            user_id=user_id
        )
        
        return [
            ShiftPerformance(
                shift_date=m["shift_date"],
                shift_type=m["shift_type"],
                total_instances=m["total_instances"],
                completed_on_time=m["completed_on_time"],
                completed_with_exceptions=m["completed_with_exceptions"],
                avg_completion_time_minutes=m["avg_completion_minutes"],
                avg_points_per_shift=m["avg_points"],
                team_engagement_score=m["avg_participants"] * 20  # Scale to 0-100
            )
            for m in metrics
        ]
    except Exception as e:
        log.error(f"Error getting performance metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- Shift Scheduling Endpoints ---
@router.get('/shifts')
async def list_shifts(current_user: dict = Depends(get_current_user)):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, start_time, end_time, timezone, color, metadata FROM shifts ORDER BY id")
                rows = cur.fetchall()
                shifts = [
                    {
                        'id': r[0], 'name': r[1], 'start_time': str(r[2]), 'end_time': str(r[3]),
                        'timezone': r[4], 'color': r[5], 'metadata': r[6]
                    }
                    for r in rows
                ]
                return shifts
    except Exception as e:
        log.error(f"Error listing shifts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/shifts')
async def create_shift(payload: dict, current_user: dict = Depends(get_current_user)):
    # Admin only
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail='Only admins may manage shifts')
    try:
        name = payload.get('name')
        start_time = payload.get('start_time')
        end_time = payload.get('end_time')
        timezone = payload.get('timezone', 'UTC')
        color = payload.get('color')
        metadata = json.dumps(payload.get('metadata') or {})
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO shifts (name, start_time, end_time, timezone, color, metadata) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                    (name, start_time, end_time, timezone, color, metadata)
                )
                new_id = cur.fetchone()[0]
                conn.commit()
                return {'id': new_id}
    except Exception as e:
        log.error(f"Error creating shift: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/scheduled-shifts')
async def list_scheduled_shifts(start_date: Optional[date] = None, end_date: Optional[date] = None, section_id: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    try:
        q = "SELECT ss.id, ss.shift_id, ss.user_id, ss.date, ss.start_ts, ss.end_ts, ss.assigned_by, ss.status FROM scheduled_shifts ss JOIN users u ON ss.user_id = u.id WHERE 1=1"
        params = []
        if start_date:
            q += " AND ss.date >= %s"
            params.append(start_date)
        if end_date:
            q += " AND ss.date <= %s"
            params.append(end_date)
        if section_id:
            q += " AND u.section_id = %s"
            params.append(section_id)
        # Non-admins: restrict to their section
        if not is_admin(current_user) and not section_id:
            q += " AND u.section_id = %s"
            params.append(current_user.get('section_id'))
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(q, params)
                rows = cur.fetchall()
                result = [
                    {
                        'id': str(r[0]), 'shift_id': r[1], 'user_id': str(r[2]), 'date': str(r[3]),
                        'start_ts': r[4].isoformat() if r[4] else None, 'end_ts': r[5].isoformat() if r[5] else None,
                        'assigned_by': str(r[6]) if r[6] else None, 'status': r[7]
                    }
                    for r in rows
                ]
                return result
    except Exception as e:
        log.error(f"Error listing scheduled shifts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/scheduled-shifts')
async def create_scheduled_shift(payload: dict, current_user: dict = Depends(get_current_user)):
    try:
        shift_id = payload.get('shift_id')
        user_id = payload.get('user_id')
        date_val = payload.get('date')
        start_ts = payload.get('start_ts')
        end_ts = payload.get('end_ts')
        status = payload.get('status', 'ASSIGNED')

        # Non-admins must only assign users from their section
        if not is_admin(current_user):
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT section_id FROM users WHERE id = %s", (user_id,))
                    row = cur.fetchone()
                    if not row or str(row[0]) != str(current_user.get('section_id')):
                        raise HTTPException(status_code=403, detail='Insufficient permissions to assign this user')

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO scheduled_shifts (shift_id, user_id, date, start_ts, end_ts, assigned_by, status) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (shift_id, user_id, date_val, start_ts, end_ts, current_user.get('id'), status)
                )
                new_id = cur.fetchone()[0]
                conn.commit()
                return {'id': str(new_id)}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error creating scheduled shift: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete('/scheduled-shifts/{shift_id}')
async def delete_scheduled_shift(shift_id: str, current_user: dict = Depends(get_current_user)):
    try:
        # Admins can delete any; managers only in their section (verify via join)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id FROM scheduled_shifts WHERE id = %s", (shift_id,))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail='Scheduled shift not found')
                user_id = row[0]
                if not is_admin(current_user):
                    cur.execute("SELECT section_id FROM users WHERE id = %s", (user_id,))
                    urow = cur.fetchone()
                    if not urow or str(urow[0]) != str(current_user.get('section_id')):
                        raise HTTPException(status_code=403, detail='Insufficient permissions to delete this scheduled shift')
                cur.execute("DELETE FROM scheduled_shifts WHERE id = %s", (shift_id,))
                conn.commit()
                return {'deleted': True}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error deleting scheduled shift: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Advanced Shift Scheduling (Bulk Assignment, Patterns, Days Off) ---

@router.get('/shift-patterns')
async def list_shift_patterns(section_id: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    """Get available shift patterns for a section"""
    try:
        from app.services.shift_scheduling_service import ShiftSchedulingService

        # Normalize incoming section_id: FastAPI may pass an empty string when query param is present but empty
        incoming = section_id if section_id and str(section_id).strip() else None

        # Determine effective section: non-admins are scoped to their section
        section = incoming
        if not is_admin(current_user):
            section = current_user.get('section_id')

        # Accept both str and UUID for section; convert if present, otherwise allow None for admin
        section_uuid = None
        if section:
            section_uuid = section if isinstance(section, UUID) else UUID(str(section))

        patterns = ShiftSchedulingService.get_available_patterns(section_uuid)
        return patterns
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error listing patterns: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/shift-patterns/{pattern_id}')
async def get_pattern_details(pattern_id: str, current_user: dict = Depends(get_current_user)):
    """Get detailed schedule for a shift pattern (what shift on which day)"""
    try:
        from app.services.shift_scheduling_service import ShiftSchedulingService
        
        details = ShiftSchedulingService.get_pattern_schedule(UUID(pattern_id))
        if not details:
            raise HTTPException(status_code=404, detail='Pattern not found')
        return details
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error fetching pattern details: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post('/shift-patterns')
async def create_shift_pattern(payload: dict, current_user: dict = Depends(get_current_user)):
    """Create a new shift pattern with day-by-day schedule."""
    try:
        from app.services.shift_scheduling_service import ShiftSchedulingService

        role = (current_user.get('role') or '').upper()
        if role not in ('ADMIN', 'MANAGER', 'SUPERVISOR'):
            raise HTTPException(status_code=403, detail='Insufficient permissions to create patterns')

        section_in_payload = payload.get('section_id')
        if is_admin(current_user):
            if not section_in_payload:
                raise HTTPException(status_code=400, detail='section_id is required')
            section_id = UUID(str(section_in_payload))
        else:
            user_section = current_user.get('section_id')
            if not user_section:
                raise HTTPException(status_code=403, detail='No section assigned to your profile')
            section_id = UUID(str(user_section))

        success, pattern, errors = ShiftSchedulingService.create_pattern(
            name=payload.get('name'),
            description=payload.get('description'),
            pattern_type=payload.get('pattern_type', 'CUSTOM'),
            section_id=section_id,
            schedule_days=payload.get('schedule_days') or [],
            metadata=payload.get('metadata') or {},
            created_by=UUID(str(current_user.get('id')))
        )

        if not success:
            raise HTTPException(status_code=400, detail='; '.join(errors) if errors else 'Failed to create pattern')

        return {
            'success': True,
            'pattern': pattern,
            'message': 'Shift pattern created successfully'
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid input: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error creating shift pattern: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.put('/shift-patterns/{pattern_id}')
async def update_shift_pattern(pattern_id: str, payload: dict, current_user: dict = Depends(get_current_user)):
    """Update an existing shift pattern and its schedule."""
    try:
        from app.services.shift_scheduling_service import ShiftSchedulingService

        role = (current_user.get('role') or '').upper()
        if role not in ('ADMIN', 'MANAGER', 'SUPERVISOR'):
            raise HTTPException(status_code=403, detail='Insufficient permissions to update patterns')

        pattern_uuid = UUID(pattern_id)

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT section_id FROM shift_patterns WHERE id = %s", (str(pattern_uuid),))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail='Pattern not found')
                if not is_admin(current_user) and str(row[0]) != str(current_user.get('section_id')):
                    raise HTTPException(status_code=403, detail='Cannot modify patterns outside your section')

        success, pattern, errors = ShiftSchedulingService.update_pattern(
            pattern_id=pattern_uuid,
            name=payload.get('name'),
            description=payload.get('description'),
            pattern_type=payload.get('pattern_type'),
            schedule_days=payload.get('schedule_days'),
            metadata=payload.get('metadata')
        )

        if not success:
            if errors and 'not found' in errors[0].lower():
                raise HTTPException(status_code=404, detail=errors[0])
            raise HTTPException(status_code=400, detail='; '.join(errors) if errors else 'Failed to update pattern')

        return {
            'success': True,
            'pattern': pattern,
            'message': 'Shift pattern updated successfully'
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid input: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error updating shift pattern: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete('/shift-patterns/{pattern_id}')
async def delete_shift_pattern(pattern_id: str, current_user: dict = Depends(get_current_user)):
    """Delete a shift pattern."""
    try:
        from app.services.shift_scheduling_service import ShiftSchedulingService

        role = (current_user.get('role') or '').upper()
        if role not in ('ADMIN', 'MANAGER', 'SUPERVISOR'):
            raise HTTPException(status_code=403, detail='Insufficient permissions to delete patterns')

        pattern_uuid = UUID(pattern_id)

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT section_id FROM shift_patterns WHERE id = %s", (str(pattern_uuid),))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail='Pattern not found')
                if not is_admin(current_user) and str(row[0]) != str(current_user.get('section_id')):
                    raise HTTPException(status_code=403, detail='Cannot delete patterns outside your section')

        success, message = ShiftSchedulingService.delete_pattern(pattern_uuid)
        if not success:
            if 'not found' in (message or '').lower():
                raise HTTPException(status_code=404, detail=message)
            raise HTTPException(status_code=400, detail=message)

        return {'success': True, 'message': message}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid input: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error deleting shift pattern: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post('/bulk-assign-shifts')
async def bulk_assign_shifts(payload: dict, current_user: dict = Depends(get_current_user)):
    """
    Bulk assign a shift pattern to multiple users.
    
    Payload:
    {
        "users": ["user_id_1", "user_id_2"],
        "pattern_id": "pattern_uuid",
        "start_date": "2026-02-10",
        "end_date": "2026-03-10",  // optional, null = ongoing
        "section_id": "section_uuid"
    }
    """
    try:
        from app.services.shift_scheduling_service import ShiftSchedulingService
        
        # Authorization: admin or manager in section
        if not is_admin(current_user):
            section_required = UUID(payload.get('section_id', ''))
            user_section = current_user.get('section_id')
            if str(section_required) != str(user_section):
                raise HTTPException(status_code=403, detail='Cannot assign users outside your section')
        
        users = payload.get('users', [])
        pattern_id = payload.get('pattern_id')
        start_date = date.fromisoformat(payload.get('start_date'))
        end_date = date.fromisoformat(payload.get('end_date')) if payload.get('end_date') else None
        section_id = UUID(payload.get('section_id'))
        
        success, count, errors = ShiftSchedulingService.bulk_assign_pattern(
            users=users,
            pattern_id=UUID(pattern_id),
            start_date=start_date,
            end_date=end_date,
            section_id=section_id,
            assigned_by=UUID(current_user.get('id'))
        )
        
        if not success:
            raise HTTPException(status_code=400, detail='; '.join(errors))
        
        return {
            'success': True,
            'assignments_created': count,
            'errors': errors,
            'message': f"✨ Bulk assigned pattern to {len(users)} users, created {count} shift assignments"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid input: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error in bulk assignment: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post('/days-off')
async def register_days_off(payload: dict, current_user: dict = Depends(get_current_user)):
    """
    Register days off for a user (vacation, sick leave, etc.).
    
    Payload:
    {
        "user_id": "user_uuid",
        "start_date": "2026-02-15",
        "end_date": "2026-02-20",
        "reason": "Vacation",
        "approved": true  // admin/manager only
    }
    """
    try:
        from app.services.shift_scheduling_service import ShiftSchedulingService
        
        user_id = payload.get('user_id')
        
        # Regular users can only register for themselves
        if not is_admin(current_user) and str(current_user.get('id')) != user_id:
            raise HTTPException(status_code=403, detail='You can only register days off for yourself')
        
        start_date = date.fromisoformat(payload.get('start_date'))
        end_date = date.fromisoformat(payload.get('end_date'))
        reason = payload.get('reason', 'Time Off')
        approved = payload.get('approved', False)
        
        # Only admin/manager can approve
        if approved and not is_admin(current_user):
            approved = False
        
        success, message = ShiftSchedulingService.add_days_off(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            reason=reason,
            approved=approved,
            approved_by=str(current_user.get('id')) if approved else None
        )
        
        if not success:
            raise HTTPException(status_code=400, detail=message)
        
        return {
            'success': True,
            'message': message,
            'status': 'APPROVED' if approved else 'PENDING'
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid input: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error registering days off: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post('/shift-exception')
async def set_shift_exception(payload: dict, current_user: dict = Depends(get_current_user)):
    """
    Create a one-off exception for a user on a specific date.
    Override normal pattern or mark as day off.
    
    Payload:
    {
        "user_id": "user_uuid",
        "exception_date": "2026-02-14",
        "shift_id": 1,  // optional, required if not is_day_off
        "is_day_off": false,
        "reason": "Moved to night shift"
    }
    """
    try:
        from app.services.shift_scheduling_service import ShiftSchedulingService
        
        # Authorization
        user_id = payload.get('user_id')
        if not is_admin(current_user):
            # Manager/supervisor can only create exceptions in their section
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT section_id FROM users WHERE id = %s", (user_id,))
                    row = cur.fetchone()
                    if not row or str(row[0]) != str(current_user.get('section_id')):
                        raise HTTPException(status_code=403, detail='Cannot modify users outside your section')
        
        exception_date = date.fromisoformat(payload.get('exception_date'))
        shift_id = payload.get('shift_id')
        is_day_off = payload.get('is_day_off', False)
        reason = payload.get('reason')
        
        success, message = ShiftSchedulingService.set_shift_exception(
            user_id=user_id,
            exception_date=exception_date,
            shift_id=shift_id,
            is_day_off=is_day_off,
            reason=reason,
            created_by=str(current_user.get('id'))
        )
        
        if not success:
            raise HTTPException(status_code=400, detail=message)
        
        return {'success': True, 'message': message}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid input: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error setting shift exception: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/my-schedule')
async def get_user_schedule(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Get current user's shift schedule and days off"""
    try:
        from app.services.shift_scheduling_service import ShiftSchedulingService
        
        today = date.today()
        start = date.fromisoformat(start_date) if start_date else today
        end = date.fromisoformat(end_date) if end_date else (today + timedelta(days=90))
        
        schedule = ShiftSchedulingService.get_user_schedule(
            user_id=str(current_user.get('id')),
            start_date=start,
            end_date=end
        )
        
        return {
            'user_id': current_user.get('id'),
            'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            'schedule': schedule
        }
    except Exception as e:
        log.error(f"Error fetching user schedule: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- System Operations ---
@router.post("/instances/{instance_id}/complete")
async def complete_checklist_instance(
    instance_id: UUID,
    with_exceptions: bool = Query(False, description="Allow completion with exceptions (not 100% done)"),
    background_tasks: BackgroundTasks = None,
    current_user: dict = Depends(get_current_user)
):
    """Mark checklist as completed (supervisor/admin only)
    
    - Regular completion: Requires 100% items completed
    - With exceptions: Allows completion even with skipped/failed items or < 100% done
    """
    try:
        # Check if user has supervisor role
        from app.core.authorization import has_capability
        if not has_capability(current_user["role"], "SUPERVISOR_COMPLETE_CHECKLIST"):
            raise HTTPException(
                status_code=403, 
                detail="Only supervisors can complete checklists"
            )

        instance = ChecklistDBService.get_instance(instance_id)
        if not instance:
            raise HTTPException(status_code=404, detail="Checklist instance not found")
        _ensure_instance_access(instance, current_user)
        
        # Use database service for completion
        result = ChecklistDBService.complete_checklist_instance(
            instance_id=instance_id,
            user_id=current_user["id"],
            with_exceptions=with_exceptions
        )
        
        # Notify all participants of checklist completion
        if background_tasks and result.get("instance"):
            instance_data = result["instance"]
            background_tasks.add_task(
                NotificationService.notify_participants_checklist_completed,
                instance_id=str(instance_id),
                checklist_date=instance_data.get("checklist_date", "Unknown"),
                shift=instance_data.get("shift", "Unknown"),
                completed_by_username=current_user.get("username", "Unknown"),
                completion_rate=instance_data.get("completion_percentage", 100.0)
            )
        
        # Emit ops event asynchronously if present
        if "ops_event" in result and background_tasks:
            background_tasks.add_task(
                _emit_ops_event_async,
                result["ops_event"]
            )
        
        return {
            "message": f"Checklist completed successfully ({result['instance']['status']})",
            "instance": result["instance"],
            "effects": disclose_effects(EffectType.CHECKLIST_COMPLETED).to_dict()
        }
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error completing checklist: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/instances/{instance_id}/change-date")
async def change_checklist_instance_date(
    instance_id: UUID,
    payload: ChecklistDateChangeRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Hidden supervisor tool to re-date a completed checklist and its timeline records.
    """
    try:
        from app.core.authorization import has_capability

        if not has_capability(current_user["role"], "SUPERVISOR_COMPLETE_CHECKLIST"):
            raise HTTPException(status_code=403, detail="Only supervisors can change checklist dates")

        async with get_async_connection() as conn:
            instance_row = await conn.fetchrow(
                """
                SELECT id, status::text AS status, shift::text AS shift, section_id
                FROM checklist_instances
                WHERE id = $1
                """,
                instance_id,
            )

            if not instance_row:
                raise HTTPException(status_code=404, detail="Checklist instance not found")

            _ensure_section_access(
                instance_row["section_id"],
                current_user,
                missing_detail="Checklist instance is missing a section assignment",
            )

            current_status = (instance_row["status"] or "").upper()
            completed_states = {"COMPLETED", "COMPLETED_WITH_EXCEPTIONS", "CLOSED_BY_EXCEPTION"}
            if current_status not in completed_states:
                raise HTTPException(
                    status_code=409,
                    detail="Changing the date now and completing the checklist after will distort the timeline."
                )

            target_date = payload.target_date
            target_timestamp = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
            shift_start, shift_end = await _build_shift_window_for_date(conn, instance_row["shift"], target_date)

            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE checklist_instances
                    SET
                        checklist_date = $2,
                        shift_start = $3,
                        shift_end = $4,
                        created_at = $5::timestamptz,
                        closed_at = CASE
                            WHEN closed_at IS NOT NULL THEN $5::timestamptz + INTERVAL '1 hour'
                            ELSE NULL
                        END
                    WHERE id = $1
                    """,
                    instance_id,
                    target_date,
                    shift_start,
                    shift_end,
                    target_timestamp,
                )

                await conn.execute(
                    """
                    UPDATE checklist_instance_items
                    SET
                        completed_at = CASE
                            WHEN completed_at IS NOT NULL THEN $2::timestamptz + INTERVAL '2 hours'
                            ELSE NULL
                        END
                    WHERE instance_id = $1
                    """,
                    instance_id,
                    target_timestamp,
                )

                await conn.execute(
                    """
                    UPDATE checklist_instance_subitems
                    SET
                        completed_at = CASE
                            WHEN completed_at IS NOT NULL THEN $2::timestamptz + INTERVAL '3 hours'
                            ELSE NULL
                        END,
                        created_at = $2::timestamptz
                    WHERE instance_item_id IN (
                        SELECT id FROM checklist_instance_items WHERE instance_id = $1
                    )
                    """,
                    instance_id,
                    target_timestamp,
                )

                await conn.execute(
                    """
                    UPDATE checklist_item_activity
                    SET created_at = $2::timestamptz + INTERVAL '30 minutes'
                    WHERE instance_item_id IN (
                        SELECT id FROM checklist_instance_items WHERE instance_id = $1
                    )
                    """,
                    instance_id,
                    target_timestamp,
                )

                await conn.execute(
                    """
                    UPDATE handover_notes
                    SET
                        created_at = $2::timestamptz,
                        acknowledged_at = CASE
                            WHEN acknowledged_at IS NOT NULL THEN $2::timestamptz + INTERVAL '4 hours'
                            ELSE NULL
                        END,
                        resolved_at = CASE
                            WHEN resolved_at IS NOT NULL THEN $2::timestamptz + INTERVAL '5 hours'
                            ELSE NULL
                        END
                    WHERE from_instance_id = $1 OR to_instance_id = $1
                    """,
                    instance_id,
                    target_timestamp,
                )

            verification_rows = await conn.fetch(
                """
                SELECT
                    'checklist_instances'::text AS table_name,
                    COUNT(*)::int AS total_records,
                    MIN(checklist_date::text) AS earliest_date,
                    MAX(checklist_date::text) AS latest_date
                FROM checklist_instances
                WHERE id = $1

                UNION ALL

                SELECT
                    'checklist_instance_items'::text AS table_name,
                    COUNT(*)::int AS total_records,
                    MIN(completed_at::text) AS earliest_date,
                    MAX(completed_at::text) AS latest_date
                FROM checklist_instance_items
                WHERE instance_id = $1

                UNION ALL

                SELECT
                    'checklist_instance_subitems'::text AS table_name,
                    COUNT(*)::int AS total_records,
                    MIN(created_at::text) AS earliest_date,
                    MAX(created_at::text) AS latest_date
                FROM checklist_instance_subitems
                WHERE instance_item_id IN (
                    SELECT id FROM checklist_instance_items WHERE instance_id = $1
                )

                UNION ALL

                SELECT
                    'checklist_item_activity'::text AS table_name,
                    COUNT(*)::int AS total_records,
                    MIN(created_at::text) AS earliest_date,
                    MAX(created_at::text) AS latest_date
                FROM checklist_item_activity
                WHERE instance_item_id IN (
                    SELECT id FROM checklist_instance_items WHERE instance_id = $1
                )

                UNION ALL

                SELECT
                    'handover_notes'::text AS table_name,
                    COUNT(*)::int AS total_records,
                    MIN(created_at::text) AS earliest_date,
                    MAX(created_at::text) AS latest_date
                FROM handover_notes
                WHERE from_instance_id = $1 OR to_instance_id = $1
                """,
                instance_id,
            )

            updated_instance = ChecklistDBService.get_instance(instance_id)

            return {
                "message": "Checklist date changed successfully",
                "instance": updated_instance,
                "target_date": str(target_date),
                "verification": [dict(r) for r in verification_rows],
            }

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error changing checklist date: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/dashboard/summary")
async def get_dashboard_summary(
    current_user: dict = Depends(get_current_user)
):
    """Get dashboard summary for current user"""
    try:
        async with get_async_connection() as conn:
            operational_context = await _get_operational_day_context(conn)
            operational_date = operational_context["operational_date"]
            user_section = None if is_admin(current_user) else _normalize_section_id(current_user.get("section_id"))
            restrict_operational_scope = not is_admin(current_user) and not user_section

            notifications_unread = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM notifications
                WHERE is_read = FALSE
                  AND (
                      user_id = $1
                      OR role_id IN (
                          SELECT role_id
                          FROM user_roles
                          WHERE user_id = $1
                      )
                  )
                """,
                current_user["id"],
            )

            thread_rows = []
            if not restrict_operational_scope:
                thread_params = [operational_date, current_user["id"]]
                section_filter_sql = ""
                if user_section:
                    thread_params.append(user_section)
                    section_filter_sql = "AND ci.section_id = $3"

                thread_rows = await conn.fetch(
                    f"""
                    WITH visible_instances AS (
                        SELECT
                            ci.id,
                            ci.template_id,
                            ci.checklist_date,
                            ci.shift::text AS shift,
                            ci.status::text AS status,
                            ci.section_id,
                            COALESCE(ct.name, 'Checklist') AS template_name
                        FROM checklist_instances ci
                        LEFT JOIN checklist_templates ct ON ct.id = ci.template_id
                        WHERE ci.checklist_date = $1
                          {section_filter_sql}
                    ),
                    item_rollup AS (
                        SELECT
                            cii.instance_id,
                            COUNT(*)::int AS total_items,
                            COUNT(*) FILTER (WHERE cii.status = 'COMPLETED')::int AS completed_items,
                            COUNT(*) FILTER (WHERE cii.status IN ('COMPLETED', 'SKIPPED', 'FAILED'))::int AS actioned_items,
                            COUNT(*) FILTER (WHERE COALESCE(cti.severity, 0) >= 4)::int AS critical_items,
                            COUNT(*) FILTER (
                                WHERE COALESCE(cti.severity, 0) >= 4
                                  AND cii.status NOT IN ('COMPLETED', 'SKIPPED')
                            )::int AS open_critical_items,
                            COUNT(*) FILTER (WHERE cii.status IN ('SKIPPED', 'FAILED'))::int AS exception_items
                        FROM checklist_instance_items cii
                        JOIN visible_instances vi ON vi.id = cii.instance_id
                        LEFT JOIN checklist_template_items cti ON cti.id = cii.template_item_id
                        GROUP BY cii.instance_id
                    ),
                    participant_rollup AS (
                        SELECT
                            cp.instance_id,
                            COUNT(*)::int AS participants_count,
                            BOOL_OR(cp.user_id = $2) AS user_joined
                        FROM checklist_participants cp
                        JOIN visible_instances vi ON vi.id = cp.instance_id
                        GROUP BY cp.instance_id
                    ),
                    handover_rollup AS (
                        SELECT
                            hn.from_instance_id AS instance_id,
                            COUNT(*)::int AS handover_count
                        FROM handover_notes hn
                        JOIN visible_instances vi ON vi.id = hn.from_instance_id
                        GROUP BY hn.from_instance_id
                    )
                    SELECT
                        vi.id,
                        vi.template_id,
                        vi.template_name,
                        vi.checklist_date,
                        vi.shift,
                        vi.status,
                        COALESCE(pr.participants_count, 0) AS participants_count,
                        COALESCE(pr.user_joined, FALSE) AS user_joined,
                        COALESCE(ir.total_items, 0) AS total_items,
                        COALESCE(ir.completed_items, 0) AS completed_items,
                        COALESCE(ir.actioned_items, 0) AS actioned_items,
                        COALESCE(ir.critical_items, 0) AS critical_items,
                        COALESCE(ir.open_critical_items, 0) AS open_critical_items,
                        COALESCE(ir.exception_items, 0) AS exception_items,
                        COALESCE(hr.handover_count, 0) AS handover_count
                    FROM visible_instances vi
                    LEFT JOIN item_rollup ir ON ir.instance_id = vi.id
                    LEFT JOIN participant_rollup pr ON pr.instance_id = vi.id
                    LEFT JOIN handover_rollup hr ON hr.instance_id = vi.id
                    ORDER BY
                        CASE UPPER(vi.shift)
                            WHEN 'MORNING' THEN 0
                            WHEN 'AFTERNOON' THEN 1
                            WHEN 'NIGHT' THEN 2
                            ELSE 99
                        END,
                        vi.id
                    """,
                    *thread_params,
                )

            network_rows = await conn.fetch(
                """
                SELECT
                    s.id,
                    s.name,
                    s.address,
                    s.port,
                    st.overall_status::text AS overall_status,
                    st.last_state_change_at
                FROM network_services s
                JOIN network_service_status st ON st.service_id = s.id
                WHERE s.deleted_at IS NULL
                  AND s.enabled = TRUE
                  AND st.overall_status::text IN ('DOWN', 'DEGRADED')
                ORDER BY
                    CASE st.overall_status::text
                        WHEN 'DOWN' THEN 0
                        WHEN 'DEGRADED' THEN 1
                        ELSE 99
                    END,
                    st.last_state_change_at ASC NULLS LAST,
                    s.created_at ASC
                LIMIT 4
                """
            )

            return _build_dashboard_summary_payload(
                operational_context,
                thread_rows,
                network_rows,
                int(notifications_unread or 0),
            )
                
    except Exception as e:
        log.error(f"Error getting dashboard summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- WebSocket Endpoint for Real-Time Updates ---
@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(...)):
    """WebSocket endpoint for real-time checklist updates"""
    user = None
    try:
        # Accept WebSocket connection first
        await websocket.accept()
        log.info(f"WebSocket connection accepted")
        
        # Authenticate the WebSocket connection using the dependency
        user = await get_current_user_websocket(token)
        
        user_id = user.get('id')
        log.info(f"WebSocket authenticated for user: {user.get('username')}")
        
        # Add to connection manager with user_id
        await websocket_manager.connect(websocket, user_id)
        log.info(f"WebSocket connection established for user {user.get('username')}")
        
        # Wait a moment before sending welcome message to ensure connection is stable
        await asyncio.sleep(0.1)
        
        # Send welcome message after connection is established
        await websocket_manager.send_welcome_message(websocket, user_id)
        
        # Store subscriptions for this connection
        subscriptions = set()
        
        try:
            while True:
                try:
                    # Receive message from client
                    data = await websocket.receive_text()
                    try:
                        message = json.loads(data)
                        # Handle different message types if needed
                        message_type = message.get('type')
                        
                        if message_type == 'PING':
                            await websocket.send_text(json.dumps({
                                'type': 'PONG',
                                'timestamp': datetime.now().isoformat()
                            }))
                        elif message_type == 'SUBSCRIBE_INSTANCE':
                            instance_id = message.get('instance_id')
                            if instance_id:
                                # Store subscription for this connection
                                subscriptions.add(instance_id)
                                await websocket.send_text(json.dumps({
                                    'type': 'SUBSCRIBED',
                                    'instance_id': instance_id,
                                    'timestamp': datetime.now().isoformat()
                                }))
                                log.info(f"Client {user.get('username')} subscribed to instance: {instance_id}")
                        
                    except json.JSONDecodeError:
                        await websocket.send_text(json.dumps({
                            'type': 'ERROR',
                            'message': 'Invalid JSON format'
                        }))
                    except Exception as e:
                        log.error(f"Error handling WebSocket message: {e}")
                        await websocket.send_text(json.dumps({
                            'type': 'ERROR',
                            'message': 'Internal server error'
                        }))
                        
                except (WebSocketDisconnect, RuntimeError) as e:
                    if isinstance(e, RuntimeError) and "disconnect message" in str(e):
                        log.info("Client disconnected (detected after connection closed)")
                    else:
                        log.info("WebSocket client disconnected normally")
                    break  # Exit the while loop when disconnect occurs
                except Exception as e:
                    log.error(f"WebSocket connection error: {e}")
                    break  # Exit the while loop on other connection errors
                    
        except Exception as e:
            log.error(f"WebSocket connection error: {e}")
            
    except Exception as e:
        log.error(f"Failed to establish WebSocket connection: {e}")
    finally:
        await websocket_manager.disconnect(websocket)
        if user:
            log.info(f"WebSocket connection closed for user: {user.get('username')}")
        # Best-effort: respond with a clean close frame if still open.
        try:
            await websocket.close(code=1000)
        except Exception:
            pass
