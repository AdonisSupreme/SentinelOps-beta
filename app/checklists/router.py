import json
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks, status
from typing import List, Optional
from uuid import UUID, uuid4
from datetime import date, timedelta, datetime

from app.checklists.db_service import ChecklistDBService
from app.checklists.schemas import (
    ChecklistTemplateCreate, ChecklistTemplateUpdate,
    ChecklistTemplateItemCreate, ChecklistTemplateItemUpdate,
    ChecklistTemplateSubitemBase,
    ChecklistInstanceCreate, ChecklistItemUpdate,
    HandoverNoteCreate, ShiftType, ChecklistStatus,
    ItemStatus, ActivityAction, ChecklistMutationResponse,
    ChecklistTemplateResponse, ChecklistInstanceResponse,
    ChecklistStats, ShiftPerformance, SubitemCompletionRequest,
    TemplateMutationResponse, TemplateItemMutationResponse, TemplateSubitemMutationResponse
)
from app.auth.service import get_current_user
from app.ops.events import OpsEventLogger
from app.checklists.state_machine import (
    get_item_transition_policy, get_checklist_transition_policy,
    is_item_transition_allowed
)
from app.core.authorization import has_capability, is_admin
from app.core.effects import EffectType, disclose_effects
from app.db.database import get_async_connection, get_connection
from app.core.logging import get_logger

log = get_logger("checklists-router")

router = APIRouter(prefix="/checklists", tags=["Checklists"])

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
        # Admins may view all templates; non-admins scoped by their section if not provided
        effective_section = None if is_admin(current_user) else (section_id or current_user.get('section_id'))
        templates = ChecklistDBService.list_templates(shift, active_only, effective_section)
        return templates
        
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
        
        # Check section permissions for non-admins
        if not is_admin(current_user):
            user_section = current_user.get('section_id')
            template_section = template.get('section_id')
            if template_section and user_section and str(template_section) != str(user_section):
                raise HTTPException(status_code=403, detail="Insufficient permissions")
        
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
        
        # Determine section
        effective_section = data.section_id or (None if is_admin(current_user) else current_user.get('section_id'))
        
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
        
        if not is_admin(current_user):
            user_section = current_user.get('section_id')
            template_section = template.get('section_id')
            if template_section and user_section and str(template_section) != str(user_section):
                raise HTTPException(status_code=403, detail="Insufficient permissions")
        
        # Update template fields
        success = ChecklistDBService.update_template(
            template_id=template_id,
            name=data.name,
            description=data.description,
            is_active=data.is_active,
            section_id=data.section_id
        )
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update template")
        
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
                    "template_name": data.name or template['name']
                }
            }
        )
        
        return {
            "id": str(template_id),
            "action": "updated",
            "template": updated_template,
            "message": f"Template updated successfully"
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
        
        if not is_admin(current_user):
            user_section = current_user.get('section_id')
            template_section = template.get('section_id')
            if template_section and user_section and str(template_section) != str(user_section):
                raise HTTPException(status_code=403, detail="Insufficient permissions")
        
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
            subitems_data=subitems_data
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
            sort_order=data.sort_order
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
    """Delete a template item (cascades to subitems)"""
    try:
        if not is_admin(current_user) and not has_capability(current_user.get("role"), "MANAGE_TEMPLATES"):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        
        # Verify template exists
        template = ChecklistDBService.get_template(template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")
        
        # Delete item
        success = ChecklistDBService.delete_template_item(item_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="Item not found")
        
        # Emit ops event
        background_tasks.add_task(
            _emit_ops_event_async,
            {
                "event_type": "TEMPLATE_ITEM_DELETED",
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
        
        result = ChecklistDBService.add_template_subitem(
            item_id=item_id,
            title=data.title,
            description=data.description,
            item_type=data.item_type.value if hasattr(data.item_type, 'value') else data.item_type,
            is_required=data.is_required,
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
        
        success = ChecklistDBService.update_template_subitem(
            subitem_id=subitem_id,
            title=data.title,
            description=data.description,
            item_type=data.item_type.value if hasattr(data.item_type, 'value') else data.item_type,
            is_required=data.is_required,
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
        # Determine template and enforce section scoping for managers
        template = None
        if data.template_id:
            template = ChecklistDBService.get_template(data.template_id)
            if not template:
                raise HTTPException(status_code=404, detail="Template not found")

        # Non-admins (managers) can only create instances for their section
        if not is_admin(current_user):
            user_section = current_user.get('section_id')
            tpl_section = template.get('section_id') if template else None
            # If template exists and its section doesn't match user's section, forbid
            if tpl_section and user_section and tpl_section != user_section:
                raise HTTPException(status_code=403, detail="Insufficient permissions for this template's section")

        # Create instance using database service; prefer template.section_id or payload
        desired_section = data.section_id or (template.get('section_id') if template else current_user.get('section_id'))
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
        # Use database service to get instances
        if start_date:
            instances = ChecklistDBService.get_instances_by_date(start_date, shift)
        else:
            # If no start date, get today's instances
            instances = ChecklistDBService.get_instances_by_date(date.today(), shift)

        # Restrict managers to their section
        if not is_admin(current_user) and current_user.get("section_id"):
            user_section = str(current_user.get("section_id"))
            instances = [i for i in instances if str(i.get("section_id") or "") == user_section]
        
        # Apply end date filtering if specified
        if end_date:
            filtered_instances = []
            for instance in instances:
                instance_date = date.fromisoformat(instance.get('checklist_date', ''))
                if instance_date <= end_date:
                    filtered_instances.append(instance)
            instances = filtered_instances
        
        # Sort by date descending, then by shift order
        shift_order = {'MORNING': 0, 'AFTERNOON': 1, 'NIGHT': 2}
        instances.sort(key=lambda x: (
            x.get('checklist_date', ''),
            shift_order.get(x.get('shift', ''), 99)
        ), reverse=True)
        
        # Format instances to match expected response format
        # Database service already returns properly formatted instances
        return instances
        
    except Exception as e:
        log.error(f"Error getting all checklist instances: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/instances/today")
async def get_todays_checklists(
    current_user: dict = Depends(get_current_user)
):
    """Get all checklist instances for today"""
    try:
        instances = ChecklistDBService.get_instances_by_date(date.today())
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
        instance = ChecklistDBService.get_instance(instance_id)
        if not instance:
            raise ValueError(f"Instance {instance_id} not found")
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
        # Ensure user has access to this instance's section (unless admin)
        instance = ChecklistDBService.get_instance(instance_id)
        if not instance:
            raise HTTPException(status_code=404, detail="Checklist instance not found")
        if not is_admin(current_user):
            user_section = current_user.get('section_id')
            inst_section = instance.get('section_id')
            if inst_section and user_section and str(inst_section) != str(user_section):
                raise HTTPException(status_code=403, detail="Insufficient permissions to join this checklist")

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
        
        # Get updated instance
        instance = ChecklistDBService.get_instance(instance_id)
        
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
        # Ensure user has access to this instance's section (unless admin)
        instance = ChecklistDBService.get_instance(instance_id)
        if not instance:
            raise HTTPException(status_code=404, detail="Checklist instance not found")
        if not is_admin(current_user):
            user_section = current_user.get('section_id')
            inst_section = instance.get('section_id')
            if inst_section and user_section and str(inst_section) != str(user_section):
                raise HTTPException(status_code=403, detail="Insufficient permissions to update items in this checklist")

        # Update item using database service
        result = ChecklistDBService.update_item_status(
            item_id=item_id,
            new_status=update.status.value if hasattr(update.status, 'value') else update.status,
            user_id=current_user["id"],
            username=current_user["username"],
            reason=update.reason,
            comment=update.comment
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
                    "comment": update.comment
                }
            }
        )
        
        # Get updated instance
        instance = ChecklistDBService.get_instance(instance_id)
        
        return {
            "instance": instance,
            "effects": disclose_effects(EffectType.ITEM_UPDATED).to_dict()
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error(f"Error updating item: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- SUBITEM MANAGEMENT (Hierarchical Checklists) ---

@router.post("/instances/{instance_id}/items/{item_id}/start-work")
async def start_working_on_item(
    instance_id: UUID,
    item_id: UUID,
    background_tasks: BackgroundTasks,
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
        
        if not is_admin(current_user):
            user_section = current_user.get('section_id')
            inst_section = instance.get('section_id')
            if inst_section and user_section and str(inst_section) != str(user_section):
                raise HTTPException(status_code=403, detail="Insufficient permissions")
        
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
            username=current_user["username"]
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
                    "has_subitems": subitem_stats['has_subitems'],
                    "subitem_count": subitem_stats['total']
                }
            }
        )
        
        return response
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
        
        if not is_admin(current_user):
            user_section = current_user.get('section_id')
            inst_section = instance.get('section_id')
            if inst_section and user_section and str(inst_section) != str(user_section):
                raise HTTPException(status_code=403, detail="Insufficient permissions")
        
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
        
        if not is_admin(current_user):
            user_section = current_user.get('section_id')
            inst_section = instance.get('section_id')
            if inst_section and user_section and str(inst_section) != str(user_section):
                raise HTTPException(status_code=403, detail="Insufficient permissions")
        
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
        
        if not is_admin(current_user):
            user_section = current_user.get('section_id')
            inst_section = instance.get('section_id')
            if inst_section and user_section and str(inst_section) != str(user_section):
                raise HTTPException(status_code=403, detail="Insufficient permissions")
        
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
    except Exception as e:
        log.error(f"Error fetching pattern details: {e}")
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
        
        # Use database service for completion
        result = ChecklistDBService.complete_checklist_instance(
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
                    ci.checklist_date,
                    u.username,
                    u.email
                FROM checklist_item_activity cia
                JOIN checklist_instance_items cii ON cia.instance_item_id = cii.id
                JOIN checklist_instances ci ON cii.instance_id = ci.id
                JOIN checklist_template_items cti ON cii.template_item_id = cti.id
                JOIN users u ON cia.user_id = u.id
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
                        "date": act['checklist_date'],
                        "actor": {
                            "id": current_user["id"],
                            "username": act['username'],
                            "email": act.get('email')
                        }
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