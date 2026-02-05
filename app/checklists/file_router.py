# app/checklists/file_router.py
"""
File-based checklist router - completely eliminates database dependency
Uses local file storage for both templates and instances
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from typing import Dict, Any, Optional, List
from uuid import UUID
from datetime import date, datetime

from app.checklists.unified_service import UnifiedChecklistService
from app.checklists.schemas import (
    ChecklistInstanceCreate, ChecklistItemUpdate, ShiftType
)
from app.auth.service import get_current_user
from app.core.error_models import (
    ErrorResponse, ValidationError, StateTransitionError, NotFoundError,
    ErrorCodes, get_status_code_for_error, create_state_transition_error,
    create_not_found_error, create_validation_error
)

# Simple fallback logger to avoid dependency issues
class SimpleLogger:
    def __init__(self, name):
        self.name = name
    def debug(self, msg): print(f"DEBUG: {msg}")
    def info(self, msg): print(f"INFO: {msg}")
    def warning(self, msg): print(f"WARNING: {msg}")
    def error(self, msg): print(f"ERROR: {msg}")

log = SimpleLogger("file-router")

router = APIRouter(prefix="/api/v1/checklists", tags=["Checklists"])

@router.get("/test")
async def test_endpoint():
    """Test endpoint to verify routing is working"""
    return {"message": "Checklist file router is working", "timestamp": datetime.now().isoformat()}

@router.post("/instances")
async def create_checklist_instance(
    data: ChecklistInstanceCreate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """Create a new checklist instance using file-based storage"""
    try:
        user_id_str = current_user.get("id") if current_user else None
        user_id = UUID(user_id_str) if user_id_str else None
        
        result = UnifiedChecklistService.create_checklist_instance(
            checklist_date=data.checklist_date,
            shift=data.shift.value if hasattr(data.shift, 'value') else data.shift,
            template_id=data.template_id,
            user_id=user_id
        )
        
        # Schedule ops event emission in background
        if "ops_event" in result:
            background_tasks.add_task(
                _emit_ops_event_async,
                result["ops_event"]
            )
        
        # Get the full instance data
        instance = UnifiedChecklistService.get_instance_by_id(result["instance"]["id"])
        
        return {
            "instance": instance,
            "effects": {
                "background_task": True,
                "notification_created": True
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/instances/{instance_id}")
async def get_checklist_instance(instance_id: UUID) -> Dict[str, Any]:
    """Get checklist instance by ID from file storage with validation"""
    try:
        # Input validation
        if not instance_id:
            raise HTTPException(
                status_code=400,
                detail=create_validation_error(
                    field="instance_id",
                    value=instance_id,
                    error_message="Instance ID is required"
                ).dict()
            )
        
        instance = UnifiedChecklistService.get_instance_by_id(instance_id)
        return instance
        
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(
                status_code=404,
                detail=create_not_found_error(
                    resource_type="ChecklistInstance",
                    resource_id=str(instance_id),
                    error_message=str(e)
                ).dict()
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    error=str(e),
                    code=ErrorCodes.VALIDATION_ERROR
                ).dict()
            )
    except Exception as e:
        log.error(f"Error getting instance: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error="Failed to get instance",
                code=ErrorCodes.SERVICE_ERROR,
                details={"original_error": str(e)}
            ).dict()
        )

@router.get("/instances")
async def list_checklists(
    shift: Optional[str] = None,
    date_filter: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
) -> List[Dict[str, Any]]:
    """List all checklist instances with optional filters and user filtering"""
    try:
        from app.checklists.instance_storage import list_instances
        from datetime import date
        
        # Parse date filter if provided
        filter_date = None
        if date_filter:
            try:
                filter_date = date.fromisoformat(date_filter)
            except ValueError:
                log.warning(f"Invalid date format: {date_filter}")
        
        instances = list_instances(shift=shift, checklist_date=filter_date)
        
        # Filter instances where user is a participant
        user_id_str = current_user.get("id") if current_user else None
        if user_id_str:
            filtered_instances = []
            for instance in instances:
                participants = instance.get('participants', [])
                if any(p.get('user_id') == user_id_str for p in participants):
                    filtered_instances.append(instance)
            instances = filtered_instances
        
        # Convert string IDs back to UUID objects for frontend compatibility
        for instance in instances:
            instance['id'] = UUID(instance['id'])
            if instance.get('created_by'):
                instance['created_by'] = UUID(instance['created_by'])
        
        return instances
    except Exception as e:
        log.error(f"Error listing checklists: {e}")
        return []

@router.get("/instances/today")
async def get_todays_checklists(
    shift: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
) -> List[Dict[str, Any]]:
    """Get today's checklists from file storage"""
    try:
        log.info(f"Getting today's checklists for user: {current_user.get('id')}, shift: {shift}")
        
        user_id_str = current_user.get("id") if current_user else None
        user_id = UUID(user_id_str) if user_id_str else None
        
        log.info(f"Converted user_id: {user_id}")
        
        instances = UnifiedChecklistService.get_todays_checklists(user_id=user_id, shift=shift)
        
        log.info(f"Retrieved {len(instances)} instances")
        
        # Return empty array if no instances found - this is normal
        return instances
        
    except Exception as e:
        log.error(f"Error getting today's checklists: {e}")
        log.error(f"Exception type: {type(e)}")
        import traceback
        log.error(f"Traceback: {traceback.format_exc()}")
        # Return empty array instead of raising exception for better UX
        return []

@router.post("/instances/{instance_id}/join")
async def join_checklist_instance(
    instance_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """Join a checklist instance with validation"""
    try:
        # Input validation
        if not instance_id:
            raise HTTPException(
                status_code=400,
                detail=create_validation_error(
                    field="instance_id",
                    value=instance_id,
                    error_message="Instance ID is required"
                ).dict()
            )
        
        if not current_user or not current_user.get("id"):
            raise HTTPException(
                status_code=401,
                detail=ErrorResponse(
                    error="Authentication required",
                    code=ErrorCodes.AUTHENTICATION_ERROR
                ).dict()
            )
        
        user_id_str = current_user.get("id") if current_user else None
        user_id = UUID(user_id_str) if user_id_str else None
        
        # Build user info from current_user for saving with participant
        user_info = {
            "username": current_user.get("username", "unknown"),
            "role": current_user.get("role", "user"),
            "email": current_user.get("email", ""),
            "first_name": current_user.get("first_name", ""),
            "last_name": current_user.get("last_name", "")
        } if current_user else None
        
        result = UnifiedChecklistService.join_checklist(instance_id, user_id, user_info)
        
        # Schedule ops event emission in background
        if "ops_event" in result:
            background_tasks.add_task(
                _emit_ops_event_async,
                result["ops_event"]
            )
        
        # Get updated instance and return just the instance to match frontend expectation
        instance = UnifiedChecklistService.get_instance_by_id(instance_id)
        return instance  # Return just the instance, not wrapped in {instance: {}, effects: {}}
        
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(
                status_code=404,
                detail=create_not_found_error(
                    resource_type="ChecklistInstance",
                    resource_id=str(instance_id),
                    error_message=str(e)
                ).dict()
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    error=str(e),
                    code=ErrorCodes.VALIDATION_ERROR
                ).dict()
            )
    except Exception as e:
        log.error(f"Error joining checklist: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error="Failed to join checklist",
                code=ErrorCodes.SERVICE_ERROR,
                details={"original_error": str(e)}
            ).dict()
        )

@router.patch("/instances/{instance_id}/items/{item_id}")
async def update_checklist_item(
    instance_id: UUID,
    item_id: UUID,
    update: ChecklistItemUpdate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """Update checklist item status with enhanced validation"""
    try:
        # Input validation
        if not instance_id:
            raise HTTPException(
                status_code=400,
                detail=create_validation_error(
                    field="instance_id",
                    value=instance_id,
                    error_message="Instance ID is required"
                ).dict()
            )
        
        if not item_id:
            raise HTTPException(
                status_code=400,
                detail=create_validation_error(
                    field="item_id",
                    value=item_id,
                    error_message="Item ID is required"
                ).dict()
            )
        
        if not update.status:
            raise HTTPException(
                status_code=400,
                detail=create_validation_error(
                    field="status",
                    value=update.status,
                    error_message="Status is required"
                ).dict()
            )
        
        # Validate status values
        valid_statuses = ['PENDING', 'IN_PROGRESS', 'COMPLETED', 'SKIPPED', 'FAILED', 'NOT_APPLICABLE']
        if update.status.value not in valid_statuses:
            raise HTTPException(
                status_code=400,
                detail=create_validation_error(
                    field="status",
                    value=update.status.value,
                    error_message=f"Status must be one of: {', '.join(valid_statuses)}"
                ).dict()
            )
        
        user_id_str = current_user.get("id") if current_user else None
        user_id = UUID(user_id_str) if user_id_str else None
        
        result = UnifiedChecklistService.update_item_status(
            instance_id=instance_id,
            item_id=item_id,
            status=update.status.value if hasattr(update.status, 'value') else update.status,
            user_id=user_id,
            comment=update.comment,
            reason=update.reason
        )
        
        # Schedule ops event emission in background
        if "ops_event" in result:
            background_tasks.add_task(
                _emit_ops_event_async,
                result["ops_event"]
            )
        
        # Get updated instance and return the updated item
        instance = UnifiedChecklistService.get_instance_by_id(instance_id)
        
        # Find and return the updated item
        for item in instance.get('items', []):
            if str(UUID(item['id'])) == str(item_id):
                return item  # Return just the updated item, not wrapped in {instance: {}, effects: {}}
        
        # If item not found, return the instance as fallback
        return instance
        
    except ValueError as e:
        # Handle state transition errors
        if "Invalid state transition" in str(e):
            # Extract current and requested status from error message if possible
            error_msg = str(e)
            raise HTTPException(
                status_code=400,
                detail=create_state_transition_error(
                    current_status="unknown",
                    requested_status="unknown",
                    allowed_transitions=['PENDING->IN_PROGRESS', 'PENDING->SKIPPED', 'PENDING->FAILED', 'IN_PROGRESS->COMPLETED', 'IN_PROGRESS->SKIPPED', 'IN_PROGRESS->FAILED'],
                    error_message=error_msg
                ).dict()
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    error=str(e),
                    code=ErrorCodes.VALIDATION_ERROR
                ).dict()
            )
    except Exception as e:
        log.error(f"Error updating item: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error="Failed to update item",
                code=ErrorCodes.SERVICE_ERROR,
                details={"original_error": str(e)}
            ).dict()
        )

        
        instances = list_instances(shift=shift, checklist_date=date_filter)
        
        # Convert string IDs back to UUID objects
        for instance in instances:
            instance['id'] = UUID(instance['id'])
            if instance.get('created_by'):
                instance['created_by'] = UUID(instance['created_by'])
        
        return {
            "instances": instances,
            "total": len(instances),
            "filters": {
                "shift": shift,
                "date": date_filter.isoformat() if date_filter else None
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/state-machine/policy")
async def get_state_policy():
    """Get state machine transition policy for frontend"""
    try:
        from app.checklists.state_machine import get_item_transition_policy, get_checklist_transition_policy
        
        return {
            "item_policy": get_item_transition_policy(),
            "checklist_policy": get_checklist_transition_policy()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/instances/{instance_id}")
async def delete_checklist_instance(instance_id: UUID) -> Dict[str, Any]:
    """Delete a checklist instance"""
    try:
        from app.checklists.instance_storage import delete_instance
        
        if delete_instance(instance_id):
            return {
                "message": f"Checklist instance {instance_id} deleted successfully",
                "effects": {
                    "instance_deleted": True
                }
            }
        else:
            raise HTTPException(status_code=404, detail="Checklist instance not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/dashboard/summary")
async def get_dashboard_summary(
    current_user: dict = Depends(get_current_user)
):
    """Get dashboard summary for current user using file-based storage"""
    try:
        from datetime import date
        from app.checklists.instance_storage import list_instances
        
        today = date.today()
        user_id_str = current_user.get("id") if current_user else None
        
        if not user_id_str:
            raise HTTPException(
                status_code=401,
                detail=ErrorResponse(
                    error="Authentication required",
                    code=ErrorCodes.AUTHENTICATION_ERROR
                ).dict()
            )
        
        # Get today's checklists for the user
        today_instances = list_instances(checklist_date=today)
        
        # Filter instances where user is a participant
        user_instances = []
        for instance in today_instances:
            participants = instance.get('participants', [])
            if any(p.get('user_id') == user_id_str for p in participants):
                user_instances.append(instance)
        
        # Calculate today's stats
        total_today = len(user_instances)
        completed_today = 0
        in_progress_today = 0
        
        for instance in user_instances:
            instance_status = instance.get('status', 'OPEN')
            if instance_status == 'COMPLETED':
                completed_today += 1
            elif instance_status in ['IN_PROGRESS', 'ACTIVE']:
                in_progress_today += 1
        
        # For file-based storage, we'll simulate pending handovers and recent activity
        # In a real implementation, you'd have separate files for these
        pending_handovers = 0
        recent_activity = []
        
        # Get recent activity from instance files
        all_instances = list_instances()
        user_recent_activity = []
        
        for instance in all_instances[:50]:  # Limit to recent 50 instances for performance
            items = instance.get('items', [])
            for item in items:
                activities = item.get('activities', [])
                for activity in activities:
                    if activity.get('actor', {}).get('id') == user_id_str:
                        user_recent_activity.append({
                            'action': activity.get('action'),
                            'timestamp': activity.get('timestamp'),
                            'item_title': item.get('template_item', {}).get('title', 'Unknown Item'),
                            'shift': instance.get('shift'),
                            'date': instance.get('checklist_date')
                        })
        
        # Sort by timestamp and take latest 10
        recent_activity = sorted(
            user_recent_activity, 
            key=lambda x: x.get('timestamp', ''), 
            reverse=True
        )[:10]
        
        # Simulate gamification data (in real implementation, this would come from a separate file)
        gamification = {
            "total_points": 0,
            "current_streak": 0,
            "perfect_shifts": 0
        }
        
        return {
            "today": {
                "total_checklists": total_today,
                "completed": completed_today,
                "in_progress": in_progress_today
            },
            "pending_handovers": pending_handovers,
            "recent_activity": recent_activity,
            "gamification": gamification
        }
                
    except Exception as e:
        log.error(f"Error getting dashboard summary: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error="Failed to get dashboard summary",
                code=ErrorCodes.SERVICE_ERROR,
                details={"original_error": str(e)}
            ).dict()
        )

# Helper function for async ops event emission
async def _emit_ops_event_async(ops_event: Dict[str, Any]):
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
        print(f"Failed to emit ops event: {e}")
