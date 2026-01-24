# app/checklists/file_router.py
"""
File-based checklist router - completely eliminates database dependency
Uses local file storage for both templates and instances
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from typing import Dict, Any, Optional, List
from uuid import UUID
from datetime import date, datetime

from app.checklists.file_service import FileChecklistService
from app.checklists.schemas import (
    ChecklistInstanceCreate, ChecklistItemUpdate, ShiftType
)

router = APIRouter(prefix="/api/v1/checklists", tags=["Checklists"])

@router.post("/instances")
async def create_checklist_instance(
    data: ChecklistInstanceCreate,
    background_tasks: BackgroundTasks,
    current_user: Optional[Dict] = None
) -> Dict[str, Any]:
    """Create a new checklist instance using file-based storage"""
    try:
        user_id = current_user.get("id") if current_user else None
        
        result = FileChecklistService.create_checklist_instance(
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
        instance = FileChecklistService.get_instance_by_id(result["instance"]["id"])
        
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
    """Get checklist instance by ID from file storage"""
    try:
        instance = FileChecklistService.get_instance_by_id(instance_id)
        return instance
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/instances/today")
async def get_todays_checklists(shift: Optional[str] = None) -> Dict[str, Any]:
    """Get today's checklists from file storage"""
    try:
        instances = FileChecklistService.get_todays_checklists(shift)
        return {
            "instances": instances,
            "total": len(instances),
            "date": date.today().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/instances/{instance_id}/join")
async def join_checklist_instance(
    instance_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: Optional[Dict] = None
) -> Dict[str, Any]:
    """Join a checklist instance"""
    try:
        user_id = current_user.get("id") if current_user else None
        
        result = FileChecklistService.join_checklist(instance_id, user_id)
        
        # Schedule ops event emission in background
        if "ops_event" in result:
            background_tasks.add_task(
                _emit_ops_event_async,
                result["ops_event"]
            )
        
        # Get updated instance
        instance = FileChecklistService.get_instance_by_id(instance_id)
        
        return {
            "instance": instance,
            "effects": {
                "checklist_joined": True
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/instances/{instance_id}/items/{item_id}")
async def update_checklist_item(
    instance_id: UUID,
    item_id: UUID,
    update: ChecklistItemUpdate,
    background_tasks: BackgroundTasks,
    current_user: Optional[Dict] = None
) -> Dict[str, Any]:
    """Update checklist item status"""
    try:
        user_id = current_user.get("id") if current_user else None
        
        result = FileChecklistService.update_item_status(
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
        
        # Get updated instance
        instance = FileChecklistService.get_instance_by_id(instance_id)
        
        return {
            "instance": instance,
            "effects": {
                "item_updated": True
            }
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/instances")
async def list_checklists(
    shift: Optional[str] = None,
    date_filter: Optional[date] = None
) -> Dict[str, Any]:
    """List checklist instances with optional filters"""
    try:
        from app.checklists.instance_storage import list_instances
        
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
