# app/checklists/router.py
import json
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from typing import List, Optional
from uuid import UUID, uuid4
from datetime import date, timedelta, datetime

from app.checklists.file_service import FileChecklistService
from app.checklists.unified_service import UnifiedChecklistService
from app.checklists.schemas import (
    ChecklistTemplateCreate, ChecklistTemplateUpdate,
    ChecklistInstanceCreate, ChecklistItemUpdate,
    HandoverNoteCreate, ShiftType, ChecklistStatus,
    ItemStatus, ActivityAction, ChecklistMutationResponse,
    ChecklistTemplateResponse, ChecklistInstanceResponse,
    ChecklistStats, ShiftPerformance
)
from app.auth.service import get_current_user
from app.checklists.state_machine import (
    get_item_transition_policy, get_checklist_transition_policy,
    is_item_transition_allowed
)
from app.core.authorization import has_capability
from app.core.effects import EffectType, disclose_effects
from app.db.database import get_async_connection
from app.core.logging import get_logger

log = get_logger("checklists-router")

router = APIRouter(prefix="/checklists", tags=["Checklists"])

# Helper function for async ops event emission (file-based version)
async def _emit_ops_event_async(ops_event: dict):
    """Emit ops event asynchronously (file-based version)"""
    try:
        from app.checklists.instance_storage import INSTANCES_DIR
        from pathlib import Path
        import json
        
        # Store ops events in a separate file for now
        ops_dir = INSTANCES_DIR.parent / "ops_events"
        ops_dir.mkdir(exist_ok=True)
        
        event_file = ops_dir / f"{ops_event['entity_id']}_{datetime.now().timestamp()}.json"
        
        with open(event_file, 'w', encoding='utf-8') as f:
            json.dump({
                **ops_event,
                'created_at': datetime.now().isoformat()
            }, f, indent=2, default=str)
        
    except Exception as e:
        # Log error but don't fail the main operation
        log.error(f"Failed to emit ops event: {e}")

# --- Template Management ---
@router.get("/templates", response_model=List[ChecklistTemplateResponse])
async def get_templates(
    shift: Optional[str] = Query(None, regex="^(MORNING|AFTERNOON|NIGHT)$"),
    active_only: bool = True,
    current_user: dict = Depends(get_current_user)
):
    """Get checklist templates"""
    try:
        templates = []
        template_dir = Path(__file__).parent / "templates"
        
        # Get available shifts
        available_shifts = [d.name for d in template_dir.iterdir() if d.is_dir()]
        
        # Filter by shift if specified
        if shift:
            available_shifts = [shift] if shift in available_shifts else []
        
        for shift_name in available_shifts:
            shift_dir = template_dir / shift_name
            for template_file in shift_dir.glob("*.json"):
                try:
                    with open(template_file, 'r', encoding='utf-8') as f:
                        template_data = json.load(f)
                    
                    # Convert to response format
                    template_response = {
                        "id": uuid4(),  # Generate UUID for template
                        "name": template_data["name"],
                        "description": f"{template_data.get('name', '')} - {shift_name} shift",
                        "shift": template_data["shift"],
                        "is_active": True,
                        "version": template_data.get("version", 1),
                        "created_by": current_user["id"],
                        "created_at": datetime.now(),
                        "items": [
                            {
                                "id": item["id"],  # Keep as string
                                "template_id": str(uuid4()),  # Generate UUID for template_id
                                "created_at": datetime.now(),
                                "title": item["title"],
                                "description": item.get("description"),
                                "item_type": item["item_type"],
                                "is_required": item["is_required"],
                                "scheduled_time": item.get("scheduled_time"),
                                "notify_before_minutes": item.get("notify_before_minutes"),
                                "severity": item.get("severity", 1),
                                "sort_order": item.get("sort_order", 0),
                                "item": {  # Add the 'item' property for frontend compatibility
                                    "id": item["id"],
                                    "title": item["title"],
                                    "description": item.get("description", ""),
                                    "item_type": item["item_type"],
                                    "is_required": item["is_required"],
                                    "scheduled_time": item.get("scheduled_time"),
                                    "notify_before_minutes": item.get("notify_before_minutes"),
                                    "severity": item.get("severity", 1),
                                    "sort_order": item.get("sort_order", 0)
                                }
                            }
                            for item in template_data.get("items", [])
                        ]
                    }
                    templates.append(template_response)
                    
                except Exception as e:
                    log.error(f"Error loading template {template_file}: {e}")
                    continue
        
        return templates
        
    except Exception as e:
        log.error(f"Error getting templates: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Instance Management ---
@router.post("/instances")
async def create_checklist_instance(
    data: ChecklistInstanceCreate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Create a new checklist instance for a shift"""
    try:
        # Create instance using unified file service
        result = await UnifiedChecklistService.create_checklist_instance(
            checklist_date=data.checklist_date,
            shift=data.shift.value if hasattr(data.shift, 'value') else data.shift,
            template_id=data.template_id,
            user_id=current_user["id"]
        )
        
        # Emit ops event asynchronously
        if "ops_event" in result:
            background_tasks.add_task(
                _emit_ops_event_async,
                result["ops_event"]
            )
        
        # Get the full instance data
        instance = await UnifiedChecklistService.get_instance_by_id(result["instance"]["id"])
        
        # Debug: Log what we're returning
        log.info(f"Returning instance with ID: {instance.get('id')}, type: {type(instance.get('id'))}")
        log.info(f"Instance keys: {list(instance.keys()) if isinstance(instance, dict) else 'Not a dict'}")
        
        return {
            "instance": instance,
            "effects": {
                "background_task": True,
                "notification_created": True
            }
        }
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
        from app.checklists.instance_storage import list_instances
        
        # Get all instances (no date filter if not specified)
        instances = list_instances(shift=shift)
        
        # Apply date range filtering
        if start_date or end_date:
            filtered_instances = []
            for instance in instances:
                instance_date = date.fromisoformat(instance.get('checklist_date', ''))
                if start_date and instance_date < start_date:
                    continue
                if end_date and instance_date > end_date:
                    continue
                filtered_instances.append(instance)
            instances = filtered_instances
        
        # Sort by date descending, then by shift order
        shift_order = {'MORNING': 0, 'AFTERNOON': 1, 'NIGHT': 2}
        instances.sort(key=lambda x: (
            x.get('checklist_date', ''),
            shift_order.get(x.get('shift', ''), 99)
        ), reverse=True)
        
        # Format instances to match expected response format
        formatted_instances = []
        for instance in instances:
            formatted_instance = UnifiedChecklistService._format_instance_response(instance)
            formatted_instances.append(formatted_instance)
        
        return formatted_instances
        
    except Exception as e:
        log.error(f"Error getting all checklist instances: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/instances/today")
async def get_todays_checklists(
    current_user: dict = Depends(get_current_user)
):
    """Get all checklist instances for today"""
    try:
        instances = await UnifiedChecklistService.get_todays_checklists()
        return instances
    except Exception as e:
        log.error(f"Error getting today's checklists: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/instances/{instance_id}")
async def get_checklist_instance(
    instance_id: UUID,
    current_user: dict = Depends(get_current_user)
):
    """Get checklist instance by ID"""
    try:
        instance = await UnifiedChecklistService.get_instance_by_id(instance_id)
        return instance
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
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
        # Join checklist using unified service (returns minimal data + deferred ops event)
        # Pass through current user's identity details so participant records in the instance
        # contain rich user info without requiring a separate lookup.
        result = await UnifiedChecklistService.join_checklist(
            instance_id,
            current_user["id"],
            {
                "username": current_user.get("username"),
                "role": current_user.get("role"),
                "email": current_user.get("email", ""),
                "first_name": current_user.get("first_name", ""),
                "last_name": current_user.get("last_name", "")
            }
        )
        
        # Emit ops event asynchronously
        if "ops_event" in result:
            background_tasks.add_task(
                _emit_ops_event_async,
                result["ops_event"]
            )
        
        # Get updated instance
        instance = await UnifiedChecklistService.get_instance_by_id(instance_id)
        
        return {
            "instance": instance,
            "effects": disclose_effects(EffectType.CHECKLIST_JOINED).to_dict()
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
        # Update item using unified service (returns minimal data + deferred ops event)
        result = await UnifiedChecklistService.update_item_status(
            instance_id=instance_id,
            item_id=item_id,
            status=update.status.value if hasattr(update.status, 'value') else update.status,
            user_id=current_user["id"],
            comment=update.comment,
            reason=update.reason,
            action_type=update.action_type.value if update.action_type else None,
            metadata=update.metadata,
            notes=update.notes
        )
        
        # Emit ops event asynchronously
        if "ops_event" in result:
            background_tasks.add_task(
                _emit_ops_event_async,
                result["ops_event"]
            )
        
        # Get updated instance
        instance = await UnifiedChecklistService.get_instance_by_id(instance_id)
        
        return {
            "instance": instance,
            "effects": disclose_effects(EffectType.ITEM_UPDATED).to_dict()
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error(f"Error updating item: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/instances/{instance_id}/stats", response_model=ChecklistStats)
async def get_checklist_stats(
    instance_id: UUID,
    current_user: dict = Depends(get_current_user)
):
    """Get statistics for a checklist instance"""
    try:
        instance = await UnifiedChecklistService.get_instance_by_id(instance_id)
        
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
        # For now, use today's checklist as from_instance
        today = date.today()
        
        # Get user's current checklist
        instances = await ChecklistService.get_todays_checklists(current_user["id"])
        current_instance = next(
            (inst for inst in instances 
             if inst["status"] in ["IN_PROGRESS", "PENDING_REVIEW"]),
            instances[0] if instances else None
        )
        
        if not current_instance:
            raise HTTPException(status_code=400, 
                              detail="No active checklist found for user")
        
        # Create handover note (returns minimal data + deferred ops event)
        result = await ChecklistService.create_handover_note(
            from_instance_id=current_instance["id"],
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
    except Exception as e:
        log.error(f"Error creating handover note: {e}")
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
        
        metrics = await ChecklistService.get_shift_performance_metrics(
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
        
        # Use unified service for file-based completion
        result = await UnifiedChecklistService.complete_instance(
            instance_id=instance_id,
            user_id=current_user["id"],
            with_exceptions=with_exceptions
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

@router.get("/dashboard/summary")
async def get_dashboard_summary(
    current_user: dict = Depends(get_current_user)
):
    """Get dashboard summary for current user"""
    try:
        today = date.today()
        
        async with get_async_connection() as conn:
            # Today's checklists
            today_stats = await conn.fetchrow("""
                SELECT 
                    COUNT(*) as total_today,
                    COUNT(CASE WHEN ci.status = 'COMPLETED' THEN 1 END) as completed_today,
                    COUNT(CASE WHEN ci.status = 'IN_PROGRESS' THEN 1 END) as in_progress_today
                FROM checklist_instances ci
                LEFT JOIN checklist_participants cp ON ci.id = cp.instance_id
                WHERE ci.checklist_date = $1 
                  AND cp.user_id = $2
            """, today, current_user["id"])
            
            # Pending handover notes
            pending_handovers_row = await conn.fetchval("""
                SELECT COUNT(*) 
                FROM handover_notes hn
                LEFT JOIN checklist_instances ci ON hn.to_instance_id = ci.id
                LEFT JOIN checklist_participants cp ON ci.id = cp.instance_id
                WHERE hn.acknowledged_at IS NULL 
                  AND cp.user_id = $1
            """, current_user["id"])
            
            pending_handovers = pending_handovers_row or 0
            
            # Recent activity
            recent_activity = await conn.fetch("""
                SELECT 
                    cia.action,
                    cia.created_at,
                    cti.title,
                    ci.shift,
                    ci.checklist_date
                FROM checklist_item_activity cia
                JOIN checklist_instance_items cii ON cia.instance_item_id = cii.id
                JOIN checklist_instances ci ON cii.instance_id = ci.id
                JOIN checklist_template_items cti ON cii.template_item_id = cti.id
                WHERE cia.user_id = $1
                ORDER BY cia.created_at DESC
                LIMIT 10
            """, current_user["id"])
            
            # Gamification summary
            gamification = await conn.fetchrow("""
                SELECT 
                    COALESCE(SUM(gs.points), 0) as total_points,
                    COALESCE(MAX(uos.current_streak_days), 0) as current_streak,
                    COALESCE(MAX(uos.perfect_shifts_count), 0) as perfect_shifts
                FROM users u
                LEFT JOIN gamification_scores gs ON u.id = gs.user_id
                LEFT JOIN user_operational_streaks uos ON u.id = uos.user_id
                WHERE u.id = $1
                GROUP BY u.id
            """, current_user["id"])
            
            return {
                "today": {
                    "total_checklists": today_stats['total_today'] if today_stats else 0,
                    "completed": today_stats['completed_today'] if today_stats else 0,
                    "in_progress": today_stats['in_progress_today'] if today_stats else 0
                },
                "pending_handovers": pending_handovers,
                "recent_activity": [
                    {
                        "action": act['action'],
                        "timestamp": act['created_at'],
                        "item_title": act['title'],
                        "shift": act['shift'],
                        "date": act['checklist_date']
                    }
                    for act in recent_activity
                ],
                "gamification": {
                    "total_points": gamification['total_points'] if gamification else 0,
                    "current_streak": gamification['current_streak'] if gamification else 0,
                    "perfect_shifts": gamification['perfect_shifts'] if gamification else 0
                }
            }
                
    except Exception as e:
        log.error(f"Error getting dashboard summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))