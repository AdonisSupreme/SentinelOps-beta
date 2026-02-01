# app/checklists/file_router.py
"""
File-based checklist router - only contains endpoints that don't exist in the original router
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
