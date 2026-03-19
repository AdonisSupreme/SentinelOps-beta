# app/tasks/router.py
"""
Task Management API Routes

Following existing SentinelOps patterns:
- RESTful endpoint design
- Consistent error handling
- Authentication middleware integration
- Pagination and filtering support
- Response envelope format
"""

import json
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks, status, UploadFile, File
from fastapi.responses import JSONResponse
from typing import List, Optional
from uuid import UUID

from app.tasks.service import TaskService, TaskAccessError, TaskValidationError, TaskNotFoundError
from app.tasks.schemas import (
    TaskCreate, TaskUpdate, TaskAssignment, TaskFilters, TaskListRequest,
    TaskResponse, TaskSummary, TaskMutationResponse, BulkTaskOperation,
    BulkTaskResponse, TaskType, Priority, TaskStatus
)
from app.auth.dependencies import get_current_user
from app.core.authorization import is_admin, is_manager_or_admin
from app.core.error_models import ErrorResponse
from app.core.logging import get_logger

log = get_logger("tasks-router")

router = APIRouter(prefix="/tasks", tags=["Tasks"])

# =====================================================
# TASK CRUD OPERATIONS
# =====================================================

# =====================================================
# SPECIALIZED TASK ENDPOINTS
# =====================================================

@router.get("/my-tasks")
async def get_my_tasks(
    status: Optional[List[TaskStatus]] = Query(None),
    priority: Optional[List[Priority]] = Query(None),
    sort: str = Query("due_date", description="Sort field"),
    order: str = Query("asc", description="Sort order (asc/desc)"),
    limit: int = Query(20, ge=1, le=50, description="Pagination limit"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    current_user: dict = Depends(get_current_user)
):
    """
    Get tasks assigned to current user
    - Shortcut endpoint for user's own tasks
    - Optimized for personal task management
    """
    try:
        filters = TaskFilters(
            assigned_to=UUID(current_user["id"]),
            status=status,
            priority=priority
        )
        
        result = await TaskService.list_tasks(
            user=current_user,
            filters=filters,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset
        )
        
        return result
        
    except Exception as e:
        log.error(f"Error getting my tasks: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve tasks")

@router.get("/team-tasks")
async def get_team_tasks(
    status: Optional[List[TaskStatus]] = Query(None),
    section_id: Optional[UUID] = Query(None, description="Specific section filter"),
    sort: str = Query("created_at", description="Sort field"),
    order: str = Query("desc", description="Sort order (asc/desc)"),
    limit: int = Query(50, ge=1, le=100, description="Pagination limit"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    current_user: dict = Depends(get_current_user)
):
    """
    Get team tasks (managers and admins only)
    - Shows tasks across user's teams
    - Requires manager or admin role
    """
    try:
        # Check permissions
        if not is_manager_or_admin(current_user):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        
        filters = TaskFilters(
            task_type=[TaskType.TEAM],
            section_id=section_id,
            status=status
        )
        
        result = await TaskService.list_tasks(
            user=current_user,
            filters=filters,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset
        )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error getting team tasks: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve tasks")

@router.get("/department-tasks")
async def get_department_tasks(
    status: Optional[List[TaskStatus]] = Query(None),
    # department_id is an integer in the DB schema
    department_id: Optional[int] = Query(None, description="Specific department filter"),
    sort: str = Query("created_at", description="Sort field"),
    order: str = Query("desc", description="Sort order (asc/desc)"),
    limit: int = Query(50, ge=1, le=100, description="Pagination limit"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    current_user: dict = Depends(get_current_user)
):
    """
    Get department tasks (managers and admins only)
    - Shows tasks across user's department
    - Requires manager or admin role
    """
    try:
        # Check permissions
        if not is_manager_or_admin(current_user):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        
        filters = TaskFilters(
            task_type=[TaskType.DEPARTMENT],
            department_id=department_id,
            status=status
        )
        
        result = await TaskService.list_tasks(
            user=current_user,
            filters=filters,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset
        )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error getting department tasks: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve tasks")

# =====================================================
# TASK CRUD OPERATIONS
# =====================================================

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_task(
    task_data: TaskCreate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """
    Create a new task
    - Users can create personal tasks
    - Managers can create team/department tasks  
    - Admins can create any task type
    """
    try:
        result = await TaskService.create_task(task_data, current_user)
        
        # Emit ops event for audit
        background_tasks.add_task(
            _emit_ops_event_async,
            {
                "event_type": "TASK_CREATED",
                "entity_type": "TASK",
                "entity_id": str(result['id']),
                "payload": {
                    "user_id": current_user["id"],
                    "username": current_user["username"],
                    "task_title": task_data.title,
                    "task_type": task_data.task_type.value,
                    "priority": task_data.priority.value
                }
            }
        )
        
        return {
            "id": result['id'],
            "created_at": result['created_at'],
            "message": result['message']
        }
        
    except TaskAccessError as e:
        raise HTTPException(status_code=403, detail=e.message)
    except TaskValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        log.error(f"Error creating task: {e}")
        raise HTTPException(status_code=500, detail="Failed to create task")

@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: UUID,
    current_user: dict = Depends(get_current_user)
):
    """
    Get task details with full related data
    - Enforces visibility rules at service level
    - Returns permissions matrix for UI
    """
    try:
        task = await TaskService.get_task(task_id, current_user)
        return task
        
    except TaskAccessError as e:
        raise HTTPException(status_code=403, detail=e.message)
    except TaskNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except Exception as e:
        log.error(f"Error getting task {task_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve task")

@router.patch("/{task_id}")
async def update_task(
    task_id: UUID,
    updates: TaskUpdate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """
    Update task with access control
    - Users can update own personal tasks
    - Managers can update department/team tasks
    - Status transitions validated
    """
    try:
        result = await TaskService.update_task(task_id, updates, current_user)
        
        # Emit ops event for significant updates
        if updates.status or updates.assigned_to_id:
            background_tasks.add_task(
                _emit_ops_event_async,
                {
                    "event_type": "TASK_UPDATED",
                    "entity_type": "TASK", 
                    "entity_id": str(task_id),
                    "payload": {
                        "user_id": current_user["id"],
                        "username": current_user["username"],
                        "updates": updates.dict(exclude_unset=True)
                    }
                }
            )
        
        return {
            "id": result['id'],
            "updated_at": result['updated_at'],
            "message": result['message']
        }
        
    except TaskAccessError as e:
        raise HTTPException(status_code=403, detail=e.message)
    except TaskValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        log.error(f"Error updating task {task_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update task")

@router.delete("/{task_id}")
async def delete_task(
    task_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """
    Soft delete task with access control
    - Users can delete own personal tasks
    - Managers can delete team/department tasks
    - Admins can delete any task
    """
    try:
        result = await TaskService.delete_task(task_id, current_user)
        
        # Emit ops event
        background_tasks.add_task(
            _emit_ops_event_async,
            {
                "event_type": "TASK_DELETED",
                "entity_type": "TASK",
                "entity_id": str(task_id),
                "payload": {
                    "user_id": current_user["id"],
                    "username": current_user["username"]
                }
            }
        )
        
        return {
            "id": result['id'],
            "deleted_at": result['deleted_at'],
            "message": result['message']
        }
        
    except TaskAccessError as e:
        raise HTTPException(status_code=403, detail=e.message)
    except TaskNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except Exception as e:
        log.error(f"Error deleting task {task_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete task")

# =====================================================
# TASK LISTING WITH FILTERING
# =====================================================

@router.get("/")
async def list_tasks(
    status: Optional[List[TaskStatus]] = Query(None, description="Filter by task status"),
    assigned_to: Optional[UUID] = Query(None, description="Filter by assigned user"),
    assigned_by: Optional[UUID] = Query(None, description="Filter by creator"),
    # department id is integer in DB migration
    department_id: Optional[int] = Query(None, description="Filter by department"),
    section_id: Optional[UUID] = Query(None, description="Filter by section"),
    priority: Optional[List[Priority]] = Query(None, description="Filter by priority"),
    task_type: Optional[List[TaskType]] = Query(None, description="Filter by task type"),
    due_before: Optional[str] = Query(None, description="Filter tasks due before date (ISO format)"),
    due_after: Optional[str] = Query(None, description="Filter tasks due after date (ISO format)"),
    created_after: Optional[str] = Query(None, description="Filter tasks created after date (ISO format)"),
    created_before: Optional[str] = Query(None, description="Filter tasks created before date (ISO format)"),
    tags: Optional[List[str]] = Query(None, description="Filter by tags"),
    search: Optional[str] = Query(None, description="Search in title and description"),
    parent_task_id: Optional[UUID] = Query(None, description="Filter by parent task"),
    is_overdue: Optional[bool] = Query(None, description="Filter overdue tasks"),
    sort: str = Query("created_at", description="Sort field"),
    order: str = Query("desc", description="Sort order (asc/desc)"),
    limit: int = Query(50, ge=1, le=100, description="Pagination limit"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    current_user: dict = Depends(get_current_user)
):
    """
    List tasks with comprehensive filtering and pagination
    - Visibility enforced at query level
    - Supports all task fields for filtering
    - Role-based visibility automatically applied
    """
    try:
        # Build filters object
        filters = TaskFilters(
            status=status,
            assigned_to=assigned_to,
            assigned_by=assigned_by,
            department_id=department_id,
            section_id=section_id,
            priority=priority,
            task_type=task_type,
            due_before=due_before,
            due_after=due_after,
            created_after=created_after,
            created_before=created_before,
            tags=tags,
            search=search,
            parent_task_id=parent_task_id,
            is_overdue=is_overdue
        )
        
        result = await TaskService.list_tasks(
            user=current_user,
            filters=filters,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset
        )
        
        return result
        
    except Exception as e:
        log.error(f"Error listing tasks: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve tasks")

# =====================================================
# TASK ASSIGNMENT OPERATIONS
# =====================================================

@router.post("/{task_id}/assign")
async def assign_task(
    task_id: UUID,
    assignment: TaskAssignment,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """
    Assign task to user with access control
    - Users can assign personal tasks they created
    - Managers can assign team/department tasks
    - Admins can assign any task
    """
    try:
        result = await TaskService.assign_task(task_id, assignment, current_user)
        
        # Emit ops event
        background_tasks.add_task(
            _emit_ops_event_async,
            {
                "event_type": "TASK_ASSIGNED",
                "entity_type": "TASK",
                "entity_id": str(task_id),
                "payload": {
                    "user_id": current_user["id"],
                    "username": current_user["username"],
                    "assigned_to": str(assignment.assigned_to_id),
                    "assigned_by": str(assignment.assigned_by_id)
                }
            }
        )
        
        return {
            "id": result['id'],
            "assigned_to_id": result['assigned_to_id'],
            "updated_at": result['updated_at'],
            "message": result['message']
        }
        
    except TaskAccessError as e:
        raise HTTPException(status_code=403, detail=e.message)
    except TaskNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except Exception as e:
        log.error(f"Error assigning task {task_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to assign task")


@router.post("/{task_id}/comments", status_code=status.HTTP_201_CREATED)
async def add_comment(
    task_id: UUID,
    content: dict,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """
    Add a comment to a task
    """
    try:
        # Expect JSON body like {"content": "..."}
        if 'content' not in content or not content['content']:
            raise HTTPException(status_code=400, detail="Missing comment content")

        result = await TaskService.add_comment(task_id, content['content'], current_user)

        # Emit ops event
        background_tasks.add_task(
            _emit_ops_event_async,
            {
                "event_type": "TASK_COMMENT_ADDED",
                "entity_type": "TASK",
                "entity_id": str(task_id),
                "payload": {
                    "user_id": current_user["id"],
                    "username": current_user["username"],
                    "comment_id": str(result['id'])
                }
            }
        )

        return {"id": result['id'], "created_at": result['created_at'], "message": "Comment added"}

    except TaskAccessError as e:
        raise HTTPException(status_code=403, detail=e.message)
    except TaskNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except TaskValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        log.error(f"Error adding comment to task {task_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to add comment")


@router.post("/{task_id}/attachments", status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    task_id: UUID,
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Upload an attachment for a task
    """
    try:
        # Save file to local uploads directory
        from pathlib import Path
        uploads_dir = Path(__file__).resolve().parents[2] / 'static' / 'uploads' / 'tasks'
        uploads_dir.mkdir(parents=True, exist_ok=True)

        file_path = uploads_dir / f"{task_id}_{file.filename}"
        with open(file_path, 'wb') as fh:
            content = await file.read()
            fh.write(content)

        result = await TaskService.add_attachment(
            task_id,
            filename=file.filename,
            file_path=str(file_path),
            file_size=len(content),
            mime_type=file.content_type or 'application/octet-stream',
            user=current_user
        )

        # Emit ops event
        if background_tasks:
            background_tasks.add_task(
                _emit_ops_event_async,
                {
                    "event_type": "TASK_ATTACHMENT_UPLOADED",
                    "entity_type": "TASK",
                    "entity_id": str(task_id),
                    "payload": {
                        "user_id": current_user["id"],
                        "username": current_user["username"],
                        "attachment_id": str(result['id'])
                    }
                }
            )

        return {"id": result['id'], "uploaded_at": result['uploaded_at'], "message": "Attachment uploaded"}

    except TaskAccessError as e:
        raise HTTPException(status_code=403, detail=e.message)
    except TaskNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except Exception as e:
        log.error(f"Error uploading attachment for task {task_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload attachment")


@router.get("/{task_id}/attachments/{attachment_id}/download")
async def download_attachment(
    task_id: UUID,
    attachment_id: UUID,
    current_user: dict = Depends(get_current_user)
):
    """
    Stream an attachment file to the client
    """
    try:
        log.info(f"Download request for task={task_id} attachment={attachment_id} by user={current_user.get('id')}")
        # Fetch attachment metadata and validate access
        attachment = await TaskService.get_attachment(task_id, attachment_id, current_user)

        from pathlib import Path
        file_path = attachment.get('file_path')
        log.info(f"Attachment metadata: filename={attachment.get('filename')} file_path={file_path}")
        if not file_path:
            raise HTTPException(status_code=404, detail='Attachment file not available')

        p = Path(file_path)
        # Prevent path traversal by resolving and ensuring within static/uploads
        uploads_root = Path(__file__).resolve().parents[2] / 'static' / 'uploads'
        try:
            resolved = p.resolve()
        except Exception:
            raise HTTPException(status_code=400, detail='Invalid file path')

        if uploads_root not in resolved.parents and uploads_root != resolved.parent:
            # File is outside uploads directory - do not serve directly
            log.warning(f"Refusing to serve file outside uploads: {resolved}")
            raise HTTPException(status_code=403, detail='Forbidden')

        if not resolved.exists():
            raise HTTPException(status_code=404, detail='File not found on server')

        # Determine file size and log for diagnostics
        try:
            file_size = resolved.stat().st_size
        except Exception:
            file_size = None
        log.info(f"Serving file {resolved} size={file_size}")

        # Use FileResponse to stream the file with explicit headers
        from fastapi.responses import FileResponse
        headers = {}
        if file_size is not None:
            headers['Content-Length'] = str(file_size)

        return FileResponse(
            path=str(resolved),
            filename=attachment.get('filename'),
            media_type=attachment.get('mime_type') or 'application/octet-stream',
            headers=headers
        )

    except TaskAccessError as e:
        raise HTTPException(status_code=403, detail=e.message)
    except TaskNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except Exception as e:
        log.error(f"Error downloading attachment {attachment_id} for task {task_id}: {e}")
        raise HTTPException(status_code=500, detail='Failed to download attachment')


@router.get("/{task_id}/attachments/{attachment_id}/inspect")
async def inspect_attachment(
    task_id: UUID,
    attachment_id: UUID,
    current_user: dict = Depends(get_current_user)
):
    """
    Debug endpoint: return DB metadata and on-disk file stats (size + sha256)
    """
    try:
        attachment = await TaskService.get_attachment(task_id, attachment_id, current_user)
        from pathlib import Path
        import hashlib

        file_path = attachment.get('file_path')
        if not file_path:
            raise HTTPException(status_code=404, detail='Attachment file not available')

        p = Path(file_path)
        try:
            resolved = p.resolve()
        except Exception:
            raise HTTPException(status_code=400, detail='Invalid file path')

        if not resolved.exists():
            raise HTTPException(status_code=404, detail='File not found on server')

        # compute file size and sha256 (streaming)
        sha256 = hashlib.sha256()
        size = 0
        with open(resolved, 'rb') as fh:
            for chunk in iter(lambda: fh.read(8192), b''):
                sha256.update(chunk)
                size += len(chunk)

        return {
            'db_filename': attachment.get('filename'),
            'db_file_size': attachment.get('file_size'),
            'on_disk_path': str(resolved),
            'on_disk_size': size,
            'on_disk_sha256': sha256.hexdigest(),
            'mime_type': attachment.get('mime_type')
        }
    except TaskAccessError as e:
        raise HTTPException(status_code=403, detail=e.message)
    except TaskNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except Exception as e:
        log.error(f"Error inspecting attachment {attachment_id} for task {task_id}: {e}")
        raise HTTPException(status_code=500, detail='Failed to inspect attachment')

@router.post("/{task_id}/complete")
async def complete_task(
    task_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """
    Complete task with access control
    - Users can complete assigned tasks
    - Creators can complete their own tasks
    - Managers can complete department tasks
    """
    try:
        result = await TaskService.complete_task(task_id, current_user)
        
        # Emit ops event
        background_tasks.add_task(
            _emit_ops_event_async,
            {
                "event_type": "TASK_COMPLETED",
                "entity_type": "TASK",
                "entity_id": str(task_id),
                "payload": {
                    "user_id": current_user["id"],
                    "username": current_user["username"],
                    "completed_at": result['completed_at'].isoformat()
                }
            }
        )
        
        return {
            "id": result['id'],
            "completed_at": result['completed_at'],
            "updated_at": result['updated_at'],
            "message": result['message']
        }
        
    except TaskAccessError as e:
        raise HTTPException(status_code=403, detail=e.message)
    except TaskNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except Exception as e:
        log.error(f"Error completing task {task_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to complete task")

# =====================================================
# BULK OPERATIONS
# =====================================================

@router.post("/bulk-operations")
async def bulk_task_operations(
    operation: BulkTaskOperation,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """
    Perform bulk operations on multiple tasks
    - Requires appropriate permissions for all tasks
    - Supports assign, complete, cancel, delete operations
    - Returns detailed success/failure results
    """
    try:
        # Validate operation
        if operation.operation not in ["assign", "complete", "cancel", "delete"]:
            raise HTTPException(status_code=400, detail="Invalid operation")
        
        successful = []
        failed = []
        
        # Process each task
        for task_id in operation.task_ids:
            try:
                if operation.operation == "assign":
                    if not operation.parameters or "assigned_to_id" not in operation.parameters:
                        failed.append({
                            "task_id": str(task_id),
                            "error": "Missing assigned_to_id parameter"
                        })
                        continue
                    
                    assignment = TaskAssignment(
                        assigned_to_id=UUID(operation.parameters["assigned_to_id"]),
                        assigned_by_id=UUID(current_user["id"])
                    )
                    await TaskService.assign_task(task_id, assignment, current_user)
                    
                elif operation.operation == "complete":
                    await TaskService.complete_task(task_id, current_user)
                    
                elif operation.operation == "cancel":
                    updates = TaskUpdate(status=TaskStatus.CANCELLED)
                    await TaskService.update_task(task_id, updates, current_user)
                    
                elif operation.operation == "delete":
                    await TaskService.delete_task(task_id, current_user)
                
                successful.append(task_id)
                
            except Exception as e:
                failed.append({
                    "task_id": str(task_id),
                    "error": str(e)
                })
        
        # Emit bulk ops event
        if successful:
            background_tasks.add_task(
                _emit_ops_event_async,
                {
                    "event_type": "TASK_BULK_OPERATION",
                    "entity_type": "TASK",
                    "entity_id": str(successful[0]),  # First task as representative
                    "payload": {
                        "user_id": current_user["id"],
                        "username": current_user["username"],
                        "operation": operation.operation,
                        "successful_count": len(successful),
                        "failed_count": len(failed)
                    }
                }
            )
        
        return BulkTaskResponse(
            successful=successful,
            failed=failed,
            total_processed=len(operation.task_ids),
            success_count=len(successful),
            failure_count=len(failed)
        )
        
    except Exception as e:
        log.error(f"Error in bulk task operations: {e}")
        raise HTTPException(status_code=500, detail="Bulk operation failed")

# =====================================================
# TASK ANALYTICS (MANAGERS AND ADMINS)
# =====================================================

@router.get("/analytics/summary")
async def get_task_analytics(
    current_user: dict = Depends(get_current_user)
):
    """
    Get task analytics summary (managers and admins only)
    - Overall task statistics
    - Performance metrics
    - Distribution by status, priority, type
    """
    try:
        # Check permissions
        if not is_manager_or_admin(current_user):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        
        # This would integrate with a TaskAnalyticsService
        # For now, return basic analytics from service
        analytics = await TaskService.get_task_analytics(current_user)
        return analytics
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error getting task analytics: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve analytics")

# =====================================================
# HELPER FUNCTIONS
# =====================================================

async def _emit_ops_event_async(ops_event: dict):
    """Emit ops event asynchronously"""
    try:
        # Import here to avoid circular imports
        from app.ops.events import OpsEventLogger
        
        OpsEventLogger.log_event(
            event_type=ops_event['event_type'],
            entity_type=ops_event['entity_type'],
            entity_id=UUID(ops_event['entity_id']),
            payload=ops_event.get('payload', {})
        )
        log.info(f"Ops event logged: {ops_event['event_type']}/{ops_event['entity_type']}/{ops_event['entity_id']}")
    except Exception as e:
        log.error(f"Failed to emit ops event: {e}")
