# ROUTER UPDATE CHECKLIST
## Quick Reference for app/checklists/router.py Transformation

---

## 🎯 OBJECTIVE
Replace all `UnifiedChecklistService` calls with `ChecklistDBService` in the router.

**Effort:** 2-3 hours  
**File:** `app/checklists/router.py`  
**Success Indicator:** All imports updated + all endpoints refactored

---

## STEP 0: IMPORTS (5 minutes)

### Current (File-Based)
```python
from app.checklists.unified_service import UnifiedChecklistService
from app.checklists.schemas import ChecklistRequestSchema
```

### New (Database-Driven) - REPLACE WITH:
```python
from app.checklists.db_service import ChecklistDBService
from app.notifications.service import NotificationService
from app.checklists.schemas import ChecklistRequestSchema
```

---

## STEP 1: GET TEMPLATES ENDPOINT

### Current (File-Based)
```python
@router.get("/templates")
async def get_templates(shift: Optional[str] = None):
    templates = UnifiedChecklistService.list_templates()
    if shift:
        templates = [t for t in templates if t.get('shift') == shift]
    return {"templates": templates}
```

### New (Database-Driven) - REPLACE WITH:
```python
@router.get("/templates")
async def get_templates(shift: Optional[str] = None):
    """
    Get all checklist templates, optionally filtered by shift.
    Active templates only.
    """
    try:
        if shift:
            templates = ChecklistDBService.list_templates(shift=shift, active_only=True)
        else:
            templates = ChecklistDBService.list_templates(active_only=True)
        
        return {
            "success": True,
            "templates": templates,
            "count": len(templates)
        }
    except Exception as e:
        logger.error(f"Failed to fetch templates: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch templates")
```

---

## STEP 2: GET ACTIVE TEMPLATE ENDPOINT

### Current (File-Based)
```python
@router.get("/templates/active/{shift}")
async def get_active_template(shift: str):
    template = UnifiedChecklistService.get_active_template(shift)
    return {"template": template}
```

### New (Database-Driven) - REPLACE WITH:
```python
@router.get("/templates/active/{shift}")
async def get_active_template(shift: str):
    """
    Get the active template for a specific shift.
    Shift must be MORNING, AFTERNOON, or NIGHT.
    """
    try:
        if shift not in ['MORNING', 'AFTERNOON', 'NIGHT']:
            raise HTTPException(status_code=400, detail="Invalid shift. Use MORNING, AFTERNOON, or NIGHT")
        
        template = ChecklistDBService.get_active_template_for_shift(shift)
        if not template:
            raise HTTPException(status_code=404, detail=f"No active template found for shift {shift}")
        
        return {
            "success": True,
            "template": template
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch active template for {shift}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch active template")
```

---

## STEP 3: CREATE CHECKLIST INSTANCE ENDPOINT

### Current (File-Based)
```python
@router.post("/instances")
async def create_checklist_instance(request: ChecklistRequestSchema, current_user: dict = Depends(get_current_user)):
    instance = UnifiedChecklistService.create_checklist_instance(
        date=request.date,
        shift=request.shift,
        created_by=str(current_user['id']),
        template_id=request.template_id
    )
    return {"instance": instance}
```

### New (Database-Driven) - REPLACE WITH:
```python
@router.post("/instances")
async def create_checklist_instance(
    request: ChecklistRequestSchema, 
    current_user: dict = Depends(get_current_user)
):
    """
    Create a new checklist instance from a template.
    
    Returns:
    - id: Instance UUID
    - status: OPEN (fresh instance)
    - items: Auto-populated from template
    - shift_start/shift_end: Calculated time windows
    """
    try:
        # Validate shift
        if request.shift not in ['MORNING', 'AFTERNOON', 'NIGHT']:
            raise HTTPException(status_code=400, detail="Invalid shift")
        
        # Get template (will fail if template_id invalid)
        template = ChecklistDBService.get_template(request.template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")
        
        # Create instance (auto-populates items, logs event, returns full instance)
        instance = ChecklistDBService.create_checklist_instance(
            date=request.date,
            shift=request.shift,
            created_by=str(current_user['id']),
            username=current_user['username'],
            template_id=request.template_id
        )
        
        return {
            "success": True,
            "instance": instance,
            "message": f"Checklist created for {request.shift} shift on {request.date}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create checklist instance: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create checklist instance")
```

---

## STEP 4: GET CHECKLIST INSTANCE ENDPOINT

### Current (File-Based)
```python
@router.get("/instances/{instance_id}")
async def get_checklist_instance(instance_id: str):
    instance = UnifiedChecklistService.get_instance(instance_id)
    return {"instance": instance}
```

### New (Database-Driven) - REPLACE WITH:
```python
@router.get("/instances/{instance_id}")
async def get_checklist_instance(instance_id: str):
    """
    Retrieve a specific checklist instance with all items, participants, and activity.
    """
    try:
        instance = ChecklistDBService.get_instance(instance_id)
        if not instance:
            raise HTTPException(status_code=404, detail="Checklist instance not found")
        
        return {
            "success": True,
            "instance": instance
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch instance {instance_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch checklist instance")
```

---

## STEP 5: UPDATE ITEM STATUS ENDPOINT ⭐ CRITICAL

### Current (File-Based)
```python
@router.put("/instances/{instance_id}/items/{item_id}")
async def update_item(
    instance_id: str,
    item_id: str,
    status: str,
    reason: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    updated = UnifiedChecklistService.update_item_status(
        instance_id=instance_id,
        item_id=item_id,
        status=status,
        reason=reason,
        user_id=str(current_user['id'])
    )
    return {"updated": updated}
```

### New (Database-Driven) - REPLACE WITH:
```python
@router.put("/instances/{instance_id}/items/{item_id}")
async def update_item_status(
    instance_id: str,
    item_id: str,
    status: str,
    reason: Optional[str] = None,
    comment: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Update checklist item status.
    
    Status values: COMPLETED, SKIPPED, FAILED, IN_PROGRESS
    
    Auto-triggers:
    - Activity logging to checklist_item_activity table
    - Notifications to admin/manager on SKIPPED or FAILED
    - Ops event logging (ITEM_SKIPPED, ITEM_FAILED, ITEM_COMPLETED)
    """
    try:
        if status not in ['COMPLETED', 'SKIPPED', 'FAILED', 'IN_PROGRESS']:
            raise HTTPException(status_code=400, detail="Invalid item status")
        
        # Update item (auto-logs activity + notifications + ops event)
        success = ChecklistDBService.update_item_status(
            item_id=item_id,
            new_status=status,
            user_id=str(current_user['id']),
            username=current_user['username'],
            reason=reason,
            comment=comment
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="Item or instance not found")
        
        # Fetch updated instance to return full state
        instance = ChecklistDBService.get_instance(instance_id)
        
        return {
            "success": True,
            "message": f"Item status updated to {status}",
            "instance": instance
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update item {item_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to update item status")
```

**Why this endpoint matters:** ⭐ This is where skip/fail notifications are auto-triggered! The method calls `NotificationDBService.create_item_skipped_notification()` or `create_item_failed_notification()` internally.

---

## STEP 6: UPDATE INSTANCE STATUS ENDPOINT

### Current (File-Based)
```python
@router.patch("/instances/{instance_id}/status")
async def update_instance_status(instance_id: str, status: str, current_user: dict = Depends(get_current_user)):
    updated = UnifiedChecklistService.update_instance_status(instance_id, status)
    return {"updated": updated}
```

### New (Database-Driven) - REPLACE WITH:
```python
@router.patch("/instances/{instance_id}/status")
async def update_instance_status(
    instance_id: str, 
    status: str,
    comment: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Update overall checklist instance status.
    
    Status values: OPEN, IN_PROGRESS, PENDING_REVIEW, COMPLETED, COMPLETED_WITH_EXCEPTIONS
    
    Auto-triggers:
    - Closes instance when marked COMPLETED
    - Logs CHECKLIST_COMPLETED event
    - Calculates completion rate and logs to ops_events
    """
    try:
        valid_statuses = ['OPEN', 'IN_PROGRESS', 'PENDING_REVIEW', 'COMPLETED', 'COMPLETED_WITH_EXCEPTIONS']
        if status not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}")
        
        success = ChecklistDBService.update_instance_status(
            instance_id=instance_id,
            new_status=status,
            user_id=str(current_user['id']),
            username=current_user['username'],
            comment=comment
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="Instance not found")
        
        # Fetch updated instance
        instance = ChecklistDBService.get_instance(instance_id)
        
        return {
            "success": True,
            "message": f"Checklist status updated to {status}",
            "instance": instance
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update instance {instance_id} status: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to update instance status")
```

---

## STEP 7: ADD PARTICIPANT ENDPOINT

### Current (File-Based)
```python
@router.post("/instances/{instance_id}/join")
async def add_participant(instance_id: str, current_user: dict = Depends(get_current_user)):
    added = UnifiedChecklistService.add_participant(instance_id, str(current_user['id']))
    return {"added": added}
```

### New (Database-Driven) - REPLACE WITH:
```python
@router.post("/instances/{instance_id}/join")
async def add_participant(
    instance_id: str, 
    current_user: dict = Depends(get_current_user)
):
    """
    Add the current user as a participant to the checklist instance.
    
    Auto-triggers:
    - Logs PARTICIPANT_JOINED event
    """
    try:
        success = ChecklistDBService.add_participant(
            instance_id=instance_id,
            user_id=str(current_user['id']),
            username=current_user['username']
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="Instance not found or user already a participant")
        
        # Fetch updated instance with participant list
        instance = ChecklistDBService.get_instance(instance_id)
        
        return {
            "success": True,
            "message": f"Successfully joined checklist",
            "instance": instance
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to add participant to {instance_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to add participant")
```

---

## STEP 8: GET CHECKLISTS BY DATE ENDPOINT

### Current (File-Based)
```python
@router.get("/instances/by-date/{date}")
async def get_by_date(date: str, shift: Optional[str] = None):
    instances = UnifiedChecklistService.get_checklists_by_date(date, shift)
    return {"instances": instances}
```

### New (Database-Driven) - REPLACE WITH:
```python
@router.get("/instances/by-date/{date}")
async def get_by_date(date: str, shift: Optional[str] = None):
    """
    Retrieve all checklist instances for a specific date.
    Optionally filter by shift.
    """
    try:
        instances = ChecklistDBService.get_instances_by_date(date, shift)
        
        return {
            "success": True,
            "instances": instances,
            "count": len(instances),
            "date": date,
            "shift": shift or "ALL"
        }
    except Exception as e:
        logger.error(f"Failed to fetch instances for {date}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch checklist instances")
```

---

## ✅ VERIFICATION CHECKLIST

After updating all endpoints:

- [ ] All imports at top of router.py updated (UnifiedChecklistService → ChecklistDBService)
- [ ] All 8 endpoints refactored (GET templates, GET active template, POST instances, GET instance, PUT item status, PATCH instance status, POST join, GET by-date)
- [ ] File compiles without syntax errors
- [ ] All error handling includes try/catch blocks
- [ ] All user_id parameters use `str(current_user['id'])`
- [ ] All endpoints return `{"success": True/False, ...}` format
- [ ] All endpoints log errors using logger
- [ ] No references to `UnifiedChecklistService` remain in file

---

## 🧪 QUICK TEST AFTER UPDATES

Run these curl commands to verify endpoints work:

```bash
# Get templates
curl -X GET http://localhost:8000/checklists/templates \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# Get active template for MORNING shift
curl -X GET http://localhost:8000/checklists/templates/active/MORNING \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# Create checklist instance (requires valid template_id from above)
curl -X POST http://localhost:8000/checklists/instances \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "date": "2024-01-15",
    "shift": "MORNING",
    "template_id": "YOUR_TEMPLATE_UUID"
  }'

# Get checklist instance
curl -X GET http://localhost:8000/checklists/instances/YOUR_INSTANCE_UUID \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# Update item status (SKIP → triggers notification)
curl -X PUT http://localhost:8000/checklists/instances/YOUR_INSTANCE_UUID/items/YOUR_ITEM_UUID \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "SKIPPED",
    "reason": "Equipment unavailable"
  }'
```

---

## 🚨 COMMON PITFALLS TO AVOID

1. **Forgetting to update imports** - Old imports will cause "module not found" errors
2. **Missing error handling** - Add try/catch to all endpoints
3. **Forgetting username parameter** - ChecklistDBService needs both user_id AND username
4. **Hardcoding UUIDs in tests** - Get real UUIDs from database first
5. **Not returning updated instance** - Always fetch and return updated instance after changes
6. **Forgetting shift validation** - Only MORNING, AFTERNOON, NIGHT are valid

---

## 📞 DEBUGGING HELP

If an endpoint fails after update:

1. **Check error logs** - Look for "Failed to..." messages
2. **Verify DB connection** - Run `SELECT 1;` in psql
3. **Check for typos** - Compare with examples above carefully
4. **Verify UUID format** - UUIDs should be valid v4 format
5. **Check auth token** - Make sure JWT token is still valid
6. **Run database queries** - Use DATABASE_QUERIES.md to verify data exists

---

**You've got this! 💪 Expected time: 2-3 hours**
