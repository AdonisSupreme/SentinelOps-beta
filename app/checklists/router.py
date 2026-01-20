# app/checklists/router.py
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from typing import List, Optional
from uuid import UUID
from datetime import date, timedelta, datetime

from app.auth.service import get_current_user
from app.checklists.schemas import (
    ChecklistInstanceCreate, ChecklistItemUpdate, HandoverNoteCreate,
    ChecklistInstanceResponse, ChecklistStats, ShiftPerformance,
    PaginatedResponse, ChecklistTemplateResponse
)
from app.checklists.service import ChecklistService
from app.db.database import get_async_connection
from app.core.logging import get_logger

log = get_logger("checklists-router")

router = APIRouter(prefix="/checklists", tags=["Checklists"])

# --- Template Management ---
@router.get("/templates", response_model=List[ChecklistTemplateResponse])
async def get_templates(
    shift: Optional[str] = Query(None, regex="^(MORNING|AFTERNOON|NIGHT)$"),
    active_only: bool = True,
    current_user: dict = Depends(get_current_user)
):
    """Get checklist templates"""
    try:
        templates = await ChecklistService.get_active_templates(
            shift if shift else None
        )
        return templates
    except Exception as e:
        log.error(f"Error getting templates: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Instance Management ---
@router.post("/instances", response_model=ChecklistInstanceResponse)
async def create_checklist_instance(
    data: ChecklistInstanceCreate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Create a new checklist instance for a shift"""
    try:
        instance = await ChecklistService.create_checklist_instance(
            checklist_date=data.checklist_date,
            shift=data.shift,
            template_id=data.template_id,
            user_id=current_user["id"]
        )
        
        # Schedule notifications in background
        background_tasks.add_task(
            ChecklistService.schedule_instance_notifications,
            instance["id"]
        )
        
        return instance
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error(f"Error creating instance: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/instances/today", response_model=List[ChecklistInstanceResponse])
async def get_todays_checklists(
    current_user: dict = Depends(get_current_user)
):
    """Get all checklist instances for today"""
    try:
        instances = await ChecklistService.get_todays_checklists(current_user["id"])
        return instances
    except Exception as e:
        log.error(f"Error getting today's checklists: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/instances/{instance_id}", response_model=ChecklistInstanceResponse)
async def get_checklist_instance(
    instance_id: UUID,
    current_user: dict = Depends(get_current_user)
):
    """Get checklist instance by ID"""
    try:
        instance = await ChecklistService.get_instance_by_id(instance_id)
        return instance
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        log.error(f"Error getting instance: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/instances/{instance_id}/join", response_model=ChecklistInstanceResponse)
async def join_checklist_instance(
    instance_id: UUID,
    current_user: dict = Depends(get_current_user)
):
    """Join a checklist instance as participant"""
    try:
        instance = await ChecklistService.join_checklist(
            instance_id, current_user["id"]
        )
        return instance
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
    current_user: dict = Depends(get_current_user)
):
    """Update checklist item status"""
    try:
        instance = await ChecklistService.update_item_status(
            instance_id=instance_id,
            item_id=item_id,
            status=update.status,
            user_id=current_user["id"],
            comment=update.comment,
            reason=update.reason
        )
        return instance
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
        instance = await ChecklistService.get_instance_by_id(instance_id)
        
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
        
        note = await ChecklistService.create_handover_note(
            from_instance_id=current_instance["id"],
            content=data.content,
            priority=data.priority,
            user_id=current_user["id"],
            to_shift=data.to_shift,
            to_date=data.to_date
        )
        
        return note
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error(f"Error creating handover note: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
    current_user: dict = Depends(get_current_user)
):
    """Mark checklist as completed (supervisor only)"""
    try:
        # Check if user has supervisor role
        if current_user["role"] not in ["SUPERVISOR", "MANAGER", "ADMIN"]:
            raise HTTPException(status_code=403, 
                              detail="Only supervisors can complete checklists")
        
        # Update instance status
        async with get_async_connection() as conn:
            result = await conn.execute("""
                UPDATE checklist_instances 
                SET status = $1, closed_by = $2, closed_at = $3
                WHERE id = $4
            """, "COMPLETED", current_user["id"], datetime.now(), instance_id)
            
            if not result or result == 'UPDATE 0':
                raise HTTPException(status_code=404, 
                                  detail="Checklist instance not found")
            
            # Award bonus points for supervisor completion
            await conn.execute("""
                INSERT INTO gamification_scores 
                (user_id, shift_instance_id, points, reason)
                SELECT 
                    cp.user_id,
                    $1,
                    25,
                    'SUPERVISOR_APPROVAL'
                FROM checklist_participants cp
                WHERE cp.instance_id = $1
            """, instance_id)
            
            return {"message": "Checklist completed successfully"}
                
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