# app/tasks/service.py
"""
Task Management Service

Principles:
1. Strict role-based access control
2. Ownership validation at data level
3. Status transition enforcement
4. Visibility rules enforced in queries
5. Comprehensive audit logging

Writes confirm. Reads explain. Never mix them.

🏁 One-Line Principle: Writes confirm. Reads explain. Never mix them.
- Mutation endpoints return minimal confirmations
- Full task fetch happens only via GET /tasks/{id}
- No redundant database calls after mutations
- Consistent access control across all operations
"""

import asyncio
import json
from typing import List, Optional, Dict, Any, Tuple
from uuid import UUID, uuid4
from datetime import datetime, timedelta
from enum import Enum

from app.db.database import get_async_connection
from app.core.logging import get_logger
from app.core.authorization import is_admin, is_manager_or_admin, has_capability
from app.core.error_models import (
    AuthorizationError, NotFoundError, ValidationError, 
    StateTransitionError, ErrorResponse
)
from app.tasks.schemas import (
    TaskCreate, TaskUpdate, TaskAssignment, TaskFilters,
    TaskType, Priority, TaskStatus, TaskAction
)

# Email sending helper
from app.core.emailer import send_email_fire_and_forget
from app.core.email_templates import (
    assignment_template,
    comment_template,
    attachment_template,
    created_template,
    status_change_template,
    task_updated_template,
)
from app.gamification.performance_service import PerformanceCommandService
from app.notifications.db_service import NotificationDBService

log = get_logger("tasks-service")

class TaskAccessError(Exception):
    """Custom exception for task access violations"""
    def __init__(self, message: str, code: str = "TASK_ACCESS_DENIED"):
        self.message = message
        self.code = code
        super().__init__(message)

class TaskValidationError(Exception):
    """Custom exception for task validation errors"""
    def __init__(self, message: str, code: str = "TASK_VALIDATION_ERROR"):
        self.message = message
        self.code = code
        super().__init__(message)

class TaskNotFoundError(Exception):
    """Custom exception for task not found errors"""
    def __init__(self, message: str, code: str = "TASK_NOT_FOUND"):
        self.message = message
        self.code = code
        super().__init__(message)

class TaskStateTransitionError(Exception):
    """Custom exception for task state transition errors"""
    def __init__(self, message: str, code: str = "INVALID_STATE_TRANSITION"):
        self.message = message
        self.code = code
        super().__init__(message)

class TaskService:
    """Core task business logic service with strict access control"""
    
    # =====================================================
    # ACCESS CONTROL VALIDATION
    # =====================================================
    
    @staticmethod
    async def _validate_task_access(
        user: dict, 
        task_id: UUID, 
        required_action: str = "read"
    ) -> Dict[str, Any]:
        """
        Validate user access to a specific task
        Returns task data if access is granted, raises exception otherwise
        """
        user_id = user.get('id')
        user_role = (user.get('role') or '').upper()
        
        async with get_async_connection() as conn:
            # Get task with ownership info
            task_query = """
                SELECT 
                    t.*,
                    u.username as assigned_to_username,
                    ub.username as assigned_by_username,
                    d.department_name as department_name,
                    s.section_name as section_name,
                    parent.title as parent_task_title
                FROM tasks t
                LEFT JOIN users u ON t.assigned_to_id = u.id
                LEFT JOIN users ub ON t.assigned_by_id = ub.id
                LEFT JOIN department d ON t.department_id = d.id
                LEFT JOIN sections s ON t.section_id = s.id
                LEFT JOIN tasks parent ON t.parent_task_id = parent.id
                WHERE t.id = $1 AND t.deleted_at IS NULL
            """
            
            task = await conn.fetchrow(task_query, task_id)
            if not task:
                raise TaskNotFoundError(
                    f"Task {task_id} not found"
                )
            
            # Convert to dict for easier manipulation and normalize UUIDs to strings
            task_dict = dict(task)
            # Normalize common UUID fields to string for consistent comparisons
            for key in ('id', 'assigned_to_id', 'assigned_by_id', 'parent_task_id', 'section_id'):
                if key in task_dict and task_dict[key] is not None:
                    try:
                        task_dict[key] = str(task_dict[key])
                    except Exception:
                        # leave as-is if cannot stringify
                        pass
            
            # Apply access control rules
            access_granted = await TaskService._check_task_permissions(
                user, task_dict, required_action
            )
            
            if not access_granted:
                raise TaskAccessError(
                    f"Access denied for {required_action} on task {task_id}",
                    code="TASK_ACCESS_DENIED"
                )
            
            return task_dict
    
    @staticmethod
    async def _check_task_permissions(
        user: dict, 
        task: Dict[str, Any], 
        action: str
    ) -> bool:
        """
        Check if user has permission for specific action on task
        """
        user_id = user.get('id')
        user_role = (user.get('role') or '').upper()
        user_department = user.get('department_id')
        user_teams = user.get('team_ids', [])
        
        task_type = task.get('task_type')
        assigned_to_id = task.get('assigned_to_id')
        assigned_by_id = task.get('assigned_by_id')
        task_department = task.get('department_id')
        task_section = task.get('section_id')
        
        # Admin override - full access
        if user_role == 'ADMIN':
            return True
        
        # Ownership checks
        is_creator = assigned_by_id == user_id
        is_assigned = assigned_to_id == user_id
        
        # Read access rules
        if action in ['read', 'list']:
            # Can read own tasks, assigned tasks, and organizational tasks
            if is_creator or is_assigned:
                return True
            
            # Team tasks - can see if member of section
            if task_type == 'TEAM' and task_section in user_teams:
                return True
            
            # Department tasks - can see if in same department
            if task_type == 'DEPARTMENT' and task_department == user_department:
                return True
            
            # Personal tasks - only creator and assigned
            if task_type == 'PERSONAL':
                return is_creator or is_assigned
            
            return False
        
        # Write access rules
        if action in ['update', 'assign']:
            # Can update own created tasks
            if is_creator:
                # Managers can update department/team tasks
                if user_role == 'MANAGER' and task_type in ['TEAM', 'DEPARTMENT', 'SYSTEM']:
                    return True
                # Users can only update personal tasks
                elif task_type == 'PERSONAL':
                    return True
                else:
                    return False
            
            # Can update status of assigned tasks
            if is_assigned and action == 'update':
                return True
            
            return False
        
        # Delete access rules
        if action == 'delete':
            # Can delete own created tasks
            if is_creator:
                # Managers can delete team/department tasks
                if user_role == 'MANAGER' and task_type in ['TEAM', 'DEPARTMENT', 'SYSTEM']:
                    return True
                # Users can only delete personal tasks
                elif task_type == 'PERSONAL':
                    return True
                else:
                    return False
            
            return False
        
        # Complete access rules
        if action == 'complete':
            # Can complete assigned tasks or own created tasks
            return is_assigned or is_creator
        
        return False
    
    @staticmethod
    def _validate_status_transition(
        current_status: TaskStatus, 
        new_status: TaskStatus, 
        user_role: str
    ) -> bool:
        """
        Validate if status transition is allowed based on business rules
        """
        # Define allowed transitions
        allowed_transitions = {
            TaskStatus.DRAFT: [TaskStatus.ACTIVE, TaskStatus.CANCELLED],
            TaskStatus.ACTIVE: [TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED, TaskStatus.ON_HOLD],
            TaskStatus.IN_PROGRESS: [TaskStatus.ACTIVE, TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.ON_HOLD],
            TaskStatus.ON_HOLD: [TaskStatus.ACTIVE, TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED],
            TaskStatus.COMPLETED: [TaskStatus.ACTIVE],  # Can reopen completed tasks
            TaskStatus.CANCELLED: [TaskStatus.ACTIVE]   # Can reopen cancelled tasks
        }
        
        return new_status in allowed_transitions.get(current_status, [])

    @staticmethod
    async def _get_user_contact_row(conn, user_id: Optional[Any]) -> Optional[Dict[str, Any]]:
        """Fetch a user's notification contact row when available."""
        if not user_id:
            return None
        row = await conn.fetchrow(
            "SELECT email, first_name, last_name FROM users WHERE id = $1 LIMIT 1",
            user_id
        )
        if not row:
            return None
        return dict(row)

    @staticmethod
    async def _get_user_email_row(conn, user_id: Optional[Any]) -> Optional[Dict[str, Any]]:
        """Fetch a user's email profile row when available."""
        row = await TaskService._get_user_contact_row(conn, user_id)
        if not row or not row.get('email'):
            return None
        return row

    @staticmethod
    async def _build_task_notification_targets(
        conn,
        task: Dict[str, Any],
        actor_user_id: Optional[Any] = None,
        include_actor: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Build per-user task notification targets.
        Personal tasks notify the assignee only; shared tasks notify assignee and creator.
        """
        targets: Dict[str, Dict[str, Any]] = {}

        if task.get('task_type') == 'PERSONAL':
            candidate_ids = [task.get('assigned_to_id')]
        else:
            candidate_ids = [task.get('assigned_to_id'), task.get('assigned_by_id')]

        if include_actor and actor_user_id:
            candidate_ids.append(actor_user_id)

        for uid in candidate_ids:
            if not uid:
                continue

            uid_str = str(uid)
            if not include_actor and actor_user_id and uid_str == str(actor_user_id):
                continue
            if uid_str in targets:
                continue

            row = await TaskService._get_user_contact_row(conn, uid)
            if not row:
                continue

            full_name = " ".join(
                part for part in [row.get('first_name'), row.get('last_name')] if part
            ).strip()

            targets[uid_str] = {
                'user_id': uid_str,
                'email': row.get('email'),
                'recipient_name': full_name or row.get('first_name') or None,
            }

        return list(targets.values())

    @staticmethod
    def _create_task_in_app_notifications(
        recipients: List[Dict[str, Any]],
        title: str,
        message: str,
        task_id: Any
    ) -> None:
        """Persist task notifications so websocket delivery fans out automatically."""
        if not recipients:
            return

        task_uuid = task_id if isinstance(task_id, UUID) else UUID(str(task_id))

        for recipient in recipients:
            try:
                NotificationDBService.create_notification(
                    title=title,
                    message=message,
                    user_id=UUID(str(recipient['user_id'])),
                    related_entity="task",
                    related_id=task_uuid
                )
            except Exception as exc:
                log.warning(
                    "Failed to create in-app task notification for user %s task %s: %s",
                    recipient.get('user_id'),
                    task_id,
                    exc
                )

    @staticmethod
    def _summarize_task_update_fields(new_values: Dict[str, Any]) -> Optional[str]:
        field_labels = {
            'title': 'title',
            'description': 'description',
            'priority': 'priority',
            'due_date': 'due date',
            'estimated_hours': 'effort estimate',
            'department_id': 'department scope',
            'section_id': 'section scope',
            'tags': 'tags',
            'completion_percentage': 'progress',
            'task_type': 'task type',
            'is_recurring': 'recurrence',
            'recurrence_pattern': 'recurrence pattern',
            'parent_task_id': 'parent task',
        }
        changed = [
            label
            for key, label in field_labels.items()
            if key in new_values and new_values.get(key) is not None
        ]
        if not changed:
            return None
        if len(changed) == 1:
            return f"Updated {changed[0]}."
        if len(changed) == 2:
            return f"Updated {changed[0]} and {changed[1]}."
        return f"Updated {', '.join(changed[:-1])}, and {changed[-1]}."

    @staticmethod
    async def _build_status_notification_recipients(
        conn,
        task: Dict[str, Any],
        actor_user_id: Optional[Any]
    ) -> List[str]:
        """
        Build recipient list for status updates.
        Primary recipients are assignee and assigner (excluding actor).
        If that yields no recipients (e.g. self-assigned personal tasks), fall back to actor.
        """
        recipients = set()

        for uid in (task.get('assigned_to_id'), task.get('assigned_by_id')):
            if not uid:
                continue
            if str(uid) == str(actor_user_id):
                continue
            row = await TaskService._get_user_email_row(conn, uid)
            if row:
                recipients.add(row['email'])

        # Fallback to actor to ensure status actions still notify a user.
        if not recipients and actor_user_id:
            actor_row = await TaskService._get_user_email_row(conn, actor_user_id)
            if actor_row:
                recipients.add(actor_row['email'])

        return list(recipients)
    
    @staticmethod
    def _coerce_uuid(value: Optional[Any]) -> Optional[UUID]:
        if value in (None, ""):
            return None
        if isinstance(value, UUID):
            return value
        try:
            return UUID(str(value))
        except (TypeError, ValueError, AttributeError):
            return None

    @staticmethod
    def _coerce_int(value: Optional[Any]) -> Optional[int]:
        if value in (None, "") or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_user_team_ids(user: dict) -> List[UUID]:
        raw_team_ids = user.get('team_ids') or []
        if not raw_team_ids and user.get('section_id'):
            raw_team_ids = [user.get('section_id')]

        team_ids: List[UUID] = []
        for raw_team_id in raw_team_ids:
            team_id = TaskService._coerce_uuid(raw_team_id)
            if team_id and team_id not in team_ids:
                team_ids.append(team_id)

        return team_ids

    @staticmethod
    def _build_visibility_filter(user: dict, filters: Optional[TaskFilters] = None) -> Tuple[str, List]:
        """
        Build WHERE clause for task visibility based on user role and ownership.
        """
        user_id = TaskService._coerce_uuid(user.get('id'))
        user_role = (user.get('role') or '').upper()
        user_department = TaskService._coerce_int(user.get('department_id'))
        user_teams = TaskService._normalize_user_team_ids(user)

        conditions = ["t.deleted_at IS NULL"]
        params: List[Any] = []

        if user_role == 'ADMIN':
            pass
        elif user_role == 'MANAGER':
            manager_conditions = []

            if user_id:
                base_index = len(params)
                manager_conditions.extend([
                    "t.assigned_by_id = $%d" % (base_index + 1),
                    "t.assigned_to_id = $%d" % (base_index + 2),
                    "t.task_type = 'PERSONAL' AND t.assigned_by_id = $%d" % (base_index + 3),
                ])
                params.extend([user_id, user_id, user_id])

            if user_department is not None:
                manager_conditions.append(
                    "t.task_type = 'DEPARTMENT' AND t.department_id = $%d" % (len(params) + 1)
                )
                params.append(user_department)

            if user_teams:
                team_placeholders = ','.join(['$%d' % (len(params) + i + 1) for i in range(len(user_teams))])
                manager_conditions.append(
                    f"t.task_type = 'TEAM' AND t.section_id IN ({team_placeholders})"
                )
                params.extend(user_teams)

            conditions.append(f"({' OR '.join(manager_conditions)})" if manager_conditions else "FALSE")
        else:
            user_conditions = []

            if user_id:
                base_index = len(params)
                user_conditions.extend([
                    "t.assigned_by_id = $%d" % (base_index + 1),
                    "t.assigned_to_id = $%d" % (base_index + 2),
                ])
                params.extend([user_id, user_id])

            if user_teams:
                team_placeholders = ','.join(['$%d' % (len(params) + i + 1) for i in range(len(user_teams))])
                user_conditions.append(
                    f"t.task_type = 'TEAM' AND t.section_id IN ({team_placeholders})"
                )
                params.extend(user_teams)

            if user_department is not None:
                user_conditions.append(
                    "t.task_type = 'DEPARTMENT' AND t.department_id = $%d" % (len(params) + 1)
                )
                params.append(user_department)

            conditions.append(f"({' OR '.join(user_conditions)})" if user_conditions else "FALSE")

        if filters:
            if filters.status:
                status_placeholders = ','.join(['$%d' % (len(params) + i + 1) for i in range(len(filters.status))])
                conditions.append(f"t.status IN ({status_placeholders})")
                params.extend([
                    status.value if isinstance(status, Enum) else status
                    for status in filters.status
                ])

            assigned_to = TaskService._coerce_uuid(filters.assigned_to)
            if assigned_to:
                conditions.append(f"t.assigned_to_id = ${len(params) + 1}")
                params.append(assigned_to)

            assigned_by = TaskService._coerce_uuid(filters.assigned_by)
            if assigned_by:
                conditions.append(f"t.assigned_by_id = ${len(params) + 1}")
                params.append(assigned_by)

            if filters.priority:
                priority_placeholders = ','.join(['$%d' % (len(params) + i + 1) for i in range(len(filters.priority))])
                conditions.append(f"t.priority IN ({priority_placeholders})")
                params.extend([
                    priority.value if isinstance(priority, Enum) else priority
                    for priority in filters.priority
                ])

            if filters.task_type:
                type_placeholders = ','.join(['$%d' % (len(params) + i + 1) for i in range(len(filters.task_type))])
                conditions.append(f"t.task_type IN ({type_placeholders})")
                params.extend([
                    task_type.value if isinstance(task_type, Enum) else task_type
                    for task_type in filters.task_type
                ])

            if filters.due_before:
                conditions.append(f"t.due_date <= ${len(params) + 1}")
                params.append(filters.due_before)

            if filters.due_after:
                conditions.append(f"t.due_date >= ${len(params) + 1}")
                params.append(filters.due_after)

            if filters.created_after:
                conditions.append(f"t.created_at >= ${len(params) + 1}")
                params.append(filters.created_after)

            if filters.created_before:
                conditions.append(f"t.created_at <= ${len(params) + 1}")
                params.append(filters.created_before)

            if filters.search:
                pattern = f"%{filters.search}%"
                conditions.append(
                    f"(t.title ILIKE ${len(params) + 1} OR COALESCE(t.description, '') ILIKE ${len(params) + 2})"
                )
                params.extend([pattern, pattern])

            department_id = TaskService._coerce_int(filters.department_id)
            if department_id is not None:
                conditions.append(f"t.department_id = ${len(params) + 1}")
                params.append(department_id)

            section_id = TaskService._coerce_uuid(filters.section_id)
            if section_id:
                conditions.append(f"t.section_id = ${len(params) + 1}")
                params.append(section_id)

            if filters.tags:
                conditions.append(f"COALESCE(t.tags, ARRAY[]::text[]) && ${len(params) + 1}::text[]")
                params.append(filters.tags)

            parent_task_id = TaskService._coerce_uuid(filters.parent_task_id)
            if parent_task_id:
                conditions.append(f"t.parent_task_id = ${len(params) + 1}")
                params.append(parent_task_id)

            if filters.is_overdue is not None:
                overdue_clause = "t.due_date IS NOT NULL AND t.due_date < NOW() AND t.status NOT IN ('COMPLETED', 'CANCELLED')"
                conditions.append(overdue_clause if filters.is_overdue else f"NOT ({overdue_clause})")

        return ' AND '.join(conditions), params
    
    # =====================================================
    # CORE TASK OPERATIONS
    # =====================================================
    
    @staticmethod
    async def create_task(task_data: TaskCreate, user: dict) -> Dict[str, Any]:
        """
        Create a new task with strict access control
        """
        user_id = user.get('id')
        user_role = (user.get('role') or '').upper()
        
        # Validate creation permissions based on task type
        if task_data.task_type == TaskType.PERSONAL:
            # Anyone can create personal tasks, but they are assigned to themselves
            if task_data.assigned_to_id and task_data.assigned_to_id != user_id:
                raise TaskValidationError(
                    "Personal tasks can only be assigned to yourself",
                    code="PERSONAL_TASK_ASSIGNMENT_INVALID"
                )
            # Ensure assigned_to_id is set to current user for personal tasks
            task_data.assigned_to_id = user_id
        elif task_data.task_type == TaskType.TEAM:
            # Only managers and admins can create team tasks
            if user_role not in ['MANAGER', 'ADMIN']:
                raise TaskAccessError(
                    "Only managers and admins can create team tasks",
                    code="INSUFFICIENT_ROLE"
                )
        elif task_data.task_type == TaskType.DEPARTMENT:
            # Only managers and admins can create department tasks
            if user_role not in ['MANAGER', 'ADMIN']:
                raise TaskAccessError(
                    "Only managers and admins can create department tasks",
                    code="INSUFFICIENT_ROLE"
                )
        elif task_data.task_type == TaskType.SYSTEM:
            # Managers and admins can create system tasks
            if user_role not in ['MANAGER', 'ADMIN']:
                raise TaskAccessError(
                    "Only managers and admins can create system tasks",
                    code="INSUFFICIENT_ROLE"
                )
        
        # Ensure assigned_by_id is set to current user for all task types
        task_data.assigned_by_id = user_id
        
        async with get_async_connection() as conn:
            try:
                # Insert task
                insert_query = """
                    INSERT INTO tasks (
                        title, description, task_type, priority, status,
                        assigned_to_id, assigned_by_id, department_id, section_id,
                        due_date, estimated_hours, tags, parent_task_id,
                        is_recurring, recurrence_pattern
                    ) VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7, $8, $9,
                        $10, $11, $12, $13,
                        $14, $15
                    ) RETURNING id, created_at
                """
                
                result = await conn.fetchrow(
                    insert_query,
                    task_data.title,
                    task_data.description,
                    task_data.task_type,
                    task_data.priority,
                    task_data.status,
                    task_data.assigned_to_id,
                    task_data.assigned_by_id,
                    task_data.department_id,
                    task_data.section_id,
                    task_data.due_date,
                    task_data.estimated_hours,
                    task_data.tags,
                    task_data.parent_task_id,
                    task_data.is_recurring,
                    task_data.recurrence_pattern
                )
                
                # Log creation in history
                await conn.execute("""
                    INSERT INTO task_history (task_id, user_id, action, new_values)
                    VALUES ($1, $2, $3, $4::jsonb)
                """, result['id'], user_id, TaskAction.CREATED, json.dumps({
                    'title': task_data.title,
                    'task_type': task_data.task_type.value,
                    'priority': task_data.priority.value,
                    'status': task_data.status.value
                }))
                
                log.info(f"Task {result['id']} created by user {user_id}")
                # Notify assignee (if present) — always send to the assigned user
                try:
                    if task_data.assigned_to_id:
                        row = await TaskService._get_user_contact_row(conn, task_data.assigned_to_id)
                        if row:
                            if str(task_data.assigned_to_id) == str(user_id):
                                notification_title = "Task created"
                                notification_message = (
                                    f'Your task "{task_data.title}" has been created and is ready for execution.'
                                )
                                subject, text, html = created_template(
                                    task_data.title,
                                    str(result['id']),
                                    actor_name=user.get('username') or None
                                )
                            else:
                                notification_title = "New task assigned"
                                notification_message = (
                                    f'{user.get("username") or "Someone"} assigned "{task_data.title}" to you.'
                                )
                                subject, text, html = assignment_template(
                                    row.get('first_name') or None,
                                    task_data.title,
                                    str(result['id']),
                                    actor_name=user.get('username') or None
                                )

                            TaskService._create_task_in_app_notifications(
                                [{
                                    'user_id': str(task_data.assigned_to_id),
                                    'email': row.get('email'),
                                    'recipient_name': row.get('first_name') or None,
                                }],
                                notification_title,
                                notification_message,
                                result['id']
                            )

                            if row.get('email'):
                                log.info("Queuing creation/assignment email to %s subject=%s", row['email'], subject)
                                send_email_fire_and_forget([row['email']], subject, text, html)
                except Exception:
                    log.exception("Failed to queue creation notification email for task %s", result['id'])

                return {
                    'id': result['id'],
                    'created_at': result['created_at'],
                    'message': 'Task created successfully'
                }
                
            except Exception as e:
                log.error(f"Failed to create task: {e}")
                raise TaskAccessError(
                    f"Failed to create task: {str(e)}",
                    code="TASK_CREATION_FAILED"
                )
    
    @staticmethod
    async def get_task(task_id: UUID, user: dict) -> Dict[str, Any]:
        """
        Get task details with access control validation
        """
        task = await TaskService._validate_task_access(user, task_id, 'read')
        
        # Get additional related data
        async with get_async_connection() as conn:
            # Get comments
            comments_query = """
                SELECT 
                    tc.*,
                    u.username, u.first_name, u.last_name
                FROM task_comments tc
                JOIN users u ON tc.user_id = u.id
                WHERE tc.task_id = $1
                ORDER BY tc.created_at DESC
                LIMIT 50
            """
            comments = await conn.fetch(comments_query, task_id)
            
            # Get attachments
            attachments_query = """
                SELECT 
                    ta.*,
                    u.username, u.first_name, u.last_name
                FROM task_attachments ta
                JOIN users u ON ta.uploaded_by_id = u.id
                WHERE ta.task_id = $1
                ORDER BY ta.uploaded_at DESC
            """
            attachments = await conn.fetch(attachments_query, task_id)
            
            # Get subtasks
            subtasks_query = """
                SELECT 
                    id, title, status, priority, due_date,
                    completion_percentage, created_at, updated_at
                FROM tasks
                WHERE parent_task_id = $1 AND deleted_at IS NULL
                ORDER BY created_at ASC
            """
            subtasks = await conn.fetch(subtasks_query, task_id)
            
            # Get history
            history_query = """
                SELECT 
                    th.*,
                    u.username, u.first_name, u.last_name
                FROM task_history th
                JOIN users u ON th.user_id = u.id
                WHERE th.task_id = $1
                ORDER BY th.timestamp DESC
                LIMIT 20
            """
            history = await conn.fetch(history_query, task_id)
            
            # Build complete task response
            task_response = {
                **task,
                'comments': [dict(comment) for comment in comments],
                'attachments': [dict(attachment) for attachment in attachments],
                'subtasks': [dict(subtask) for subtask in subtasks],
                'history': [dict(h) for h in history],
                'permissions': await TaskService._get_task_permissions(user, task)
            }
            
            return task_response
    
    @staticmethod
    async def update_task(
        task_id: UUID, 
        updates: TaskUpdate, 
        user: dict
    ) -> Dict[str, Any]:
        """
        Update task with strict access control and validation
        """
        # Validate access
        task = await TaskService._validate_task_access(user, task_id, 'update')
        
        user_id = user.get('id')
        user_role = (user.get('role') or '').upper()
        
        # Validate status transition
        if updates.status and updates.status != task['status']:
            if not TaskService._validate_status_transition(
                task['status'], updates.status, user_role
            ):
                raise TaskStateTransitionError(
                    f"Invalid status transition from {task['status']} to {updates.status}"
                )
        
        # Build update query dynamically
        update_fields = []
        params = []
        param_count = 0
        
        for field, value in updates.dict(exclude_unset=True).items():
            if value is not None and field != 'id':
                param_count += 1
                update_fields.append(f"{field} = ${param_count}")
                params.append(value.value if hasattr(value, 'value') else value)
        
        if not update_fields:
            raise TaskValidationError(
                "No valid fields to update",
                code="NO_UPDATE_FIELDS"
            )
        
        # Add task_id to params for the WHERE clause (user_id is used only for history logging)
        params.append(task_id)
        
        async with get_async_connection() as conn:
            try:
                # Update task
                update_query = f"""
                    UPDATE tasks 
                    SET {', '.join(update_fields)}, updated_at = NOW()
                    WHERE id = ${param_count + 1} AND deleted_at IS NULL
                    RETURNING updated_at
                """
                
                result = await conn.fetchrow(update_query, *params)
                
                # Log update in history
                old_values = {k: v for k, v in task.items() if k in updates.dict(exclude_unset=True)}
                new_values = updates.dict(exclude_unset=True)
                
                await conn.execute("""
                    INSERT INTO task_history (task_id, user_id, action, old_values, new_values)
                    VALUES ($1, $2, $3, $4::jsonb, $5::jsonb)
                """, task_id, user_id, TaskAction.UPDATED, json.dumps(old_values) if old_values else None, json.dumps(new_values) if new_values else None)
                
                log.info(f"Task {task_id} updated by user {user_id}")

                # If status changed, notify assigned and creator (exclude actor)
                try:
                    if 'status' in new_values and new_values.get('status') != task.get('status'):
                        if str(new_values.get('status')) == 'COMPLETED':
                            PerformanceCommandService.schedule_badge_unlock_sync(
                                task.get('assigned_to_id') or user_id
                            )

                        notification_targets = await TaskService._build_task_notification_targets(
                            conn,
                            task,
                            actor_user_id=user_id
                        )
                        if not notification_targets and user_id:
                            actor_row = await TaskService._get_user_contact_row(conn, user_id)
                            if actor_row:
                                notification_targets = [{
                                    'user_id': str(user_id),
                                    'email': actor_row.get('email'),
                                    'recipient_name': actor_row.get('first_name') or None,
                                }]

                        if notification_targets:
                            TaskService._create_task_in_app_notifications(
                                notification_targets,
                                "Task status updated",
                                f'{user.get("username") or "Someone"} changed "{task.get("title") or task_id}" '
                                f'from {task.get("status")} to {new_values.get("status")}.',
                                task_id
                            )

                        recipients = [target['email'] for target in notification_targets if target.get('email')]
                        if recipients:
                            subject, text, html = status_change_template(
                                task.get('title') or '',
                                str(task_id),
                                str(task.get('status') or ''),
                                str(new_values.get('status')),
                                actor_name=user.get('username') or None
                            )
                        send_email_fire_and_forget(list(recipients), subject, text, html)
                except Exception:
                    log.exception("Failed to queue status-change emails for task %s", task_id)

                try:
                    generic_update_summary = TaskService._summarize_task_update_fields(new_values)
                    if generic_update_summary:
                        notification_targets = await TaskService._build_task_notification_targets(
                            conn,
                            task,
                            actor_user_id=user_id
                        )

                        if notification_targets:
                            TaskService._create_task_in_app_notifications(
                                notification_targets,
                                "Task updated",
                                f'{user.get("username") or "Someone"} updated "{task.get("title") or task_id}".',
                                task_id
                            )

                        recipients = [target['email'] for target in notification_targets if target.get('email')]
                        if recipients:
                            subject, text, html = task_updated_template(
                                task.get('title') or str(task_id),
                                str(task_id),
                                generic_update_summary,
                                actor_name=user.get('username') or None
                            )
                            send_email_fire_and_forget(list(recipients), subject, text, html)
                except Exception:
                    log.exception("Failed to queue generic update notifications for task %s", task_id)

                # If assigned_to changed, notify the new assignee
                try:
                    if 'assigned_to_id' in new_values and str(new_values.get('assigned_to_id')) != str(task.get('assigned_to_id')):
                        new_assignee = new_values.get('assigned_to_id')
                        if new_assignee:
                            row = await TaskService._get_user_contact_row(conn, new_assignee)
                            if row:
                                TaskService._create_task_in_app_notifications(
                                    [{
                                        'user_id': str(new_assignee),
                                        'email': row.get('email'),
                                        'recipient_name': row.get('first_name') or None,
                                    }],
                                    "Task assigned",
                                    f'{user.get("username") or "Someone"} assigned "{task.get("title") or task_id}" to you.',
                                    task_id
                                )

                                subject, text, html = assignment_template(
                                    row.get('first_name') or None,
                                    task.get('title') or str(task_id),
                                    str(task_id),
                                    actor_name=user.get('username') or None
                                )
                                if row.get('email'):
                                    log.info("Queuing assignment email to %s subject=%s", row['email'], subject)
                                    send_email_fire_and_forget([row['email']], subject, text, html)
                except Exception:
                    log.exception("Failed to queue assignment email for task %s", task_id)

                return {
                    'id': task_id,
                    'updated_at': result['updated_at'],
                    'message': 'Task updated successfully'
                }
                
            except Exception as e:
                log.error(f"Failed to update task {task_id}: {e}")
                raise TaskAccessError(
                    f"Failed to update task: {str(e)}",
                    code="TASK_UPDATE_FAILED"
                )
    
    @staticmethod
    async def delete_task(task_id: UUID, user: dict) -> Dict[str, Any]:
        """
        Soft delete task with access control
        """
        # Validate access
        task = await TaskService._validate_task_access(user, task_id, 'delete')
        
        user_id = user.get('id')
        
        async with get_async_connection() as conn:
            try:
                # Soft delete
                result = await conn.fetchrow("""
                    UPDATE tasks 
                    SET deleted_at = NOW(), updated_at = NOW()
                    WHERE id = $1 AND deleted_at IS NULL
                    RETURNING updated_at
                """, task_id)
                
                if not result:
                    raise TaskNotFoundError(
                        f"Task {task_id} not found or already deleted"
                    )
                
                # Log deletion in history
                await conn.execute("""
                    INSERT INTO task_history (task_id, user_id, action, old_values)
                    VALUES ($1, $2, $3, $4::jsonb)
                """, task_id, user_id, TaskAction.CANCELLED, json.dumps(task) if task else None)
                
                log.info(f"Task {task_id} deleted by user {user_id}")
                
                return {
                    'id': task_id,
                    'deleted_at': result['updated_at'],
                    'message': 'Task deleted successfully'
                }
                
            except Exception as e:
                log.error(f"Failed to delete task {task_id}: {e}")
                raise TaskAccessError(
                    f"Failed to delete task: {str(e)}",
                    code="TASK_DELETION_FAILED"
                )
    
    @staticmethod
    async def list_tasks(
        user: dict, 
        filters: Optional[TaskFilters] = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """
        List tasks with visibility filtering and pagination
        """
        # Build visibility filter
        where_clause, params = TaskService._build_visibility_filter(user, filters)

        # Validate sort field
        valid_sort_fields = ['created_at', 'updated_at', 'due_date', 'title', 'priority', 'status']
        if sort not in valid_sort_fields:
            sort = 'created_at'

        order = 'ASC' if order.lower() == 'asc' else 'DESC'
        order_clause = f"t.{sort} {order}"
        if sort == 'due_date':
            order_clause = f"t.{sort} {order} NULLS LAST"

        # Build query
        base_query = f"""
            SELECT 
                t.id,
                t.title,
                t.status,
                t.priority,
                t.task_type,
                t.assigned_to_id,
                t.due_date,
                t.completion_percentage,
                t.created_at,
                t.updated_at,
                t.completed_at,
                u.username as assigned_to_username,
                u.first_name as assigned_to_first_name,
                u.last_name as assigned_to_last_name,
                CASE 
                    WHEN t.due_date < NOW() AND t.status NOT IN ('COMPLETED', 'CANCELLED') THEN TRUE
                    ELSE FALSE
                END as is_overdue,
                COUNT(*) OVER() AS total_count
            FROM tasks t
            LEFT JOIN users u ON t.assigned_to_id = u.id
            WHERE {where_clause}
            ORDER BY {order_clause}
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
        """

        async with get_async_connection() as conn:
            tasks = await conn.fetch(base_query, *params, limit, offset)
            total = tasks[0]['total_count'] if tasks else 0
            task_rows = []
            for task in tasks:
                row = dict(task)
                row.pop('total_count', None)
                task_rows.append(row)

            return {
                'tasks': task_rows,
                'pagination': {
                    'total': total,
                    'limit': limit,
                    'offset': offset,
                    'has_more': offset + limit < total
                },
                'filters_applied': filters.dict() if filters else None
            }
    
    @staticmethod
    async def assign_task(
        task_id: UUID, 
        assignment: TaskAssignment, 
        user: dict
    ) -> Dict[str, Any]:
        """
        Assign task to user with access control
        """
        # Validate access to task
        task = await TaskService._validate_task_access(user, task_id, 'assign')
        
        user_id = user.get('id')
        user_role = (user.get('role') or '').upper()
        
        # Check if user can assign this type of task
        task_type = task.get('task_type')
        if task_type == 'PERSONAL' and task['assigned_by_id'] != user_id:
            raise TaskAccessError(
                "Cannot reassign personal tasks created by others",
                code="PERSONAL_TASK_ASSIGNMENT_RESTRICTED"
            )
        
        if task_type in ['TEAM', 'DEPARTMENT', 'SYSTEM'] and user_role not in ['MANAGER', 'ADMIN']:
            raise TaskAccessError(
                "Only managers and admins can assign team/department/system tasks",
                code="INSUFFICIENT_ROLE"
            )
        
        async with get_async_connection() as conn:
            try:
                # Update assignment
                result = await conn.fetchrow("""
                    UPDATE tasks 
                    SET assigned_to_id = $1, updated_at = NOW()
                    WHERE id = $2 AND deleted_at IS NULL
                    RETURNING updated_at
                """, assignment.assigned_to_id, task_id)
                
                # Log assignment in history
                await conn.execute("""
                    INSERT INTO task_history (task_id, user_id, action, old_values, new_values)
                    VALUES ($1, $2, $3, $4::jsonb, $5::jsonb)
                """, task_id, user_id, TaskAction.ASSIGNED, 
                json.dumps({'assigned_to_id': task.get('assigned_to_id')}),
                json.dumps({'assigned_to_id': assignment.assigned_to_id}))
                
                log.info(f"Task {task_id} assigned to {assignment.assigned_to_id} by {user_id}")
                # Notify the assigned user by email (fire-and-forget)
                try:
                    # Fetch recipient email and name if available
                    recipient = None
                    if assignment.assigned_to_id:
                        recipient = await TaskService._get_user_contact_row(conn, assignment.assigned_to_id)

                    if recipient:
                        TaskService._create_task_in_app_notifications(
                            [{
                                'user_id': str(assignment.assigned_to_id),
                                'email': recipient.get('email'),
                                'recipient_name': recipient.get('first_name') or None,
                            }],
                            "Task assigned",
                            f'{user.get("username") or "Someone"} assigned "{task.get("title") or task_id}" to you.',
                            task_id
                        )

                        subject, text, html = assignment_template(
                            recipient.get('first_name') or None,
                            task.get('title') or str(task_id),
                            str(task_id),
                            actor_name=user.get('username') or None
                        )
                        if recipient.get('email'):
                            log.info("Queuing assignment email to %s subject=%s", recipient['email'], subject)
                            send_email_fire_and_forget([recipient['email']], subject, text, html)
                except Exception:
                    log.exception("Failed to queue assignment notification email for task %s", task_id)

                return {
                    'id': task_id,
                    'assigned_to_id': assignment.assigned_to_id,
                    'updated_at': result['updated_at'],
                    'message': 'Task assigned successfully'
                }
                
            except Exception as e:
                log.error(f"Failed to assign task {task_id}: {e}")
                raise TaskAccessError(
                    f"Failed to assign task: {str(e)}",
                    code="TASK_ASSIGNMENT_FAILED"
                )
    
    @staticmethod
    async def complete_task(task_id: UUID, user: dict) -> Dict[str, Any]:
        """
        Complete task with access control
        """
        # Validate access
        task = await TaskService._validate_task_access(user, task_id, 'complete')
        
        user_id = user.get('id')
        
        # Check if task can be completed
        if task['status'] in ['COMPLETED', 'CANCELLED']:
            raise TaskStateTransitionError(
                f"Cannot complete task in {task['status']} status"
            )
        
        async with get_async_connection() as conn:
            try:
                # Complete task
                result = await conn.fetchrow("""
                    UPDATE tasks 
                    SET status = 'COMPLETED', 
                        completion_percentage = 100,
                        completed_at = NOW(),
                        updated_at = NOW()
                    WHERE id = $1 AND deleted_at IS NULL
                    RETURNING completed_at, updated_at
                """, task_id)
                
                # Log completion in history
                await conn.execute("""
                    INSERT INTO task_history (task_id, user_id, action, new_values)
                    VALUES ($1, $2, $3, $4::jsonb)
                """, task_id, user_id, TaskAction.COMPLETED, json.dumps({
                    'status': 'COMPLETED',
                    'completion_percentage': 100,
                    'completed_at': result['completed_at'].isoformat()
                }))
                
                log.info(f"Task {task_id} completed by user {user_id}")
                PerformanceCommandService.schedule_badge_unlock_sync(
                    task.get('assigned_to_id') or user_id
                )

                # Align complete endpoint with status-change notification behavior.
                try:
                    notification_targets = await TaskService._build_task_notification_targets(
                        conn,
                        task,
                        actor_user_id=user_id
                    )
                    if not notification_targets and user_id:
                        actor_row = await TaskService._get_user_contact_row(conn, user_id)
                        if actor_row:
                            notification_targets = [{
                                'user_id': str(user_id),
                                'email': actor_row.get('email'),
                                'recipient_name': actor_row.get('first_name') or None,
                            }]

                    if notification_targets:
                        TaskService._create_task_in_app_notifications(
                            notification_targets,
                            "Task completed",
                            f'{user.get("username") or "Someone"} completed "{task.get("title") or task_id}".',
                            task_id
                        )

                    recipients = [target['email'] for target in notification_targets if target.get('email')]
                    if recipients:
                        subject, text, html = status_change_template(
                            task.get('title') or '',
                            str(task_id),
                            str(task.get('status') or ''),
                            'COMPLETED',
                            actor_name=user.get('username') or None
                        )
                        send_email_fire_and_forget(recipients, subject, text, html)
                except Exception:
                    log.exception("Failed to queue completion status-change email for task %s", task_id)
                
                return {
                    'id': task_id,
                    'completed_at': result['completed_at'],
                    'updated_at': result['updated_at'],
                    'message': 'Task completed successfully'
                }
                
            except Exception as e:
                log.error(f"Failed to complete task {task_id}: {e}")
                raise TaskAccessError(
                    f"Failed to complete task: {str(e)}",
                    code="TASK_COMPLETION_FAILED"
                )

    @staticmethod
    async def add_comment(task_id: UUID, content: str, user: dict) -> Dict[str, Any]:
        """
        Add a comment to a task (with permission checks)
        """
        # Validate access to task (read visibility)
        task = await TaskService._validate_task_access(user, task_id, 'read')

        # Ensure user may comment
        perms = await TaskService._get_task_permissions(user, task)
        if not perms.get('can_comment'):
            raise TaskAccessError("Insufficient permissions to comment on this task")

        user_id = user.get('id')

        async with get_async_connection() as conn:
            try:
                result = await conn.fetchrow(
                    """
                    INSERT INTO task_comments (task_id, user_id, content, comment_type, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, NOW(), NOW())
                    RETURNING id, created_at
                    """,
                    task_id, user_id, content, 'COMMENT'
                )

                # Log history entry
                await conn.execute(
                    """
                    INSERT INTO task_history (task_id, user_id, action, new_values)
                    VALUES ($1, $2, $3, $4::jsonb)
                    """,
                    task_id, user_id, TaskAction.UPDATED, json.dumps({'comment': content})
                )

                log.info(f"Comment {result['id']} added to task {task_id} by {user_id}")

                # Notify relevant users (assigned_to and assigned_by) except the commenter
                try:
                    notification_targets = await TaskService._build_task_notification_targets(
                        conn,
                        task,
                        actor_user_id=user_id
                    )
                    if notification_targets:
                        TaskService._create_task_in_app_notifications(
                            notification_targets,
                            "New task comment",
                            f'{user.get("username") or "Someone"} commented on "{task.get("title") or task_id}".',
                            task_id
                        )

                    recipients = [target['email'] for target in notification_targets if target.get('email')]
                    if recipients:
                        subject, text, html = comment_template(
                            task.get('title') or str(task_id),
                            str(task_id),
                            content,
                            actor_name=user.get('username') or None
                        )
                        log.debug("Queuing comment email to %s subject=%s", recipients, subject)
                        send_email_fire_and_forget(recipients, subject, text, html)
                except Exception:
                    log.exception("Failed to queue comment notification email for task %s", task_id)

                return {'id': result['id'], 'created_at': result['created_at']}

            except Exception as e:
                log.error(f"Failed to add comment to task {task_id}: {e}")
                raise TaskAccessError(f"Failed to add comment: {str(e)}")

    @staticmethod
    async def add_attachment(
        task_id: UUID,
        filename: str,
        file_path: str,
        file_size: int,
        mime_type: str,
        user: dict
    ) -> Dict[str, Any]:
        """
        Add an attachment record to a task (file saving handled by caller)
        """
        # Validate access to task (read visibility)
        task = await TaskService._validate_task_access(user, task_id, 'read')

        # Ensure user may add attachments
        perms = await TaskService._get_task_permissions(user, task)
        if not perms.get('can_add_attachments'):
            raise TaskAccessError("Insufficient permissions to add attachments to this task")

        user_id = user.get('id')

        async with get_async_connection() as conn:
            try:
                result = await conn.fetchrow(
                    """
                    INSERT INTO task_attachments (task_id, filename, file_path, file_size, mime_type, uploaded_by_id, uploaded_at)
                    VALUES ($1, $2, $3, $4, $5, $6, NOW())
                    RETURNING id, uploaded_at
                    """,
                    task_id, filename, file_path, file_size, mime_type, user_id
                )

                # Log history entry for that log
                await conn.execute(
                    """
                    INSERT INTO task_history (task_id, user_id, action, new_values)
                    VALUES ($1, $2, $3, $4::jsonb)
                    """,
                    task_id, user_id, TaskAction.UPDATED, json.dumps({'attachment': filename})
                )

                log.info(f"Attachment {result['id']} uploaded for task {task_id} by {user_id}")

                # Notify relevant users (assigned_to and assigned_by) except the uploader
                try:
                    notification_targets = await TaskService._build_task_notification_targets(
                        conn,
                        task,
                        actor_user_id=user_id
                    )
                    if notification_targets:
                        TaskService._create_task_in_app_notifications(
                            notification_targets,
                            "New task attachment",
                            f'{user.get("username") or "Someone"} uploaded "{filename}" to "{task.get("title") or task_id}".',
                            task_id
                        )

                    recipients = [target['email'] for target in notification_targets if target.get('email')]
                    if recipients:
                        subject, text, html = attachment_template(
                            task.get('title') or str(task_id),
                            str(task_id),
                            filename,
                            actor_name=user.get('username') or None
                        )
                        log.debug("Queuing attachment email to %s subject=%s", recipients, subject)
                        send_email_fire_and_forget(recipients, subject, text, html)
                except Exception:
                    log.exception("Failed to queue attachment notification email for task %s", task_id)

                return {'id': result['id'], 'uploaded_at': result['uploaded_at']}

            except Exception as e:
                log.error(f"Failed to add attachment to task {task_id}: {e}")
                raise TaskAccessError(f"Failed to add attachment: {str(e)}")

    @staticmethod
    async def get_attachment(task_id: UUID, attachment_id: UUID, user: dict) -> Dict[str, Any]:
        """
        Retrieve a single attachment record and validate access to the parent task
        """
        # Validate task access (read)
        task = await TaskService._validate_task_access(user, task_id, 'read')

        async with get_async_connection() as conn:
            try:
                attachment_query = """
                    SELECT ta.*, u.username, u.first_name, u.last_name
                    FROM task_attachments ta
                    JOIN users u ON ta.uploaded_by_id = u.id
                    WHERE ta.id = $1 AND ta.task_id = $2
                    LIMIT 1
                """
                attachment = await conn.fetchrow(attachment_query, attachment_id, task_id)
                if not attachment:
                    raise TaskNotFoundError("Attachment not found")

                return dict(attachment)
            except TaskNotFoundError:
                raise
            except Exception as e:
                log.error(f"Failed to fetch attachment {attachment_id} for task {task_id}: {e}")
                raise TaskAccessError(f"Failed to fetch attachment: {str(e)}")
    
    @staticmethod
    async def get_task_analytics(user: dict) -> Dict[str, Any]:
        """
        Get task analytics for managers and admins
        """
        user_id = user.get('id')
        user_role = (user.get('role') or '').upper()
        
        async with get_async_connection() as conn:
            # Get basic statistics
            stats_query = """
                SELECT 
                    COUNT(*) as total_tasks,
                    COUNT(CASE WHEN status = 'COMPLETED' THEN 1 END) as completed_tasks,
                    COUNT(CASE WHEN status = 'IN_PROGRESS' THEN 1 END) as in_progress_tasks,
                    COUNT(CASE WHEN due_date < NOW() AND status NOT IN ('COMPLETED', 'CANCELLED') THEN 1 END) as overdue_tasks,
                    COUNT(CASE WHEN status = 'ACTIVE' THEN 1 END) as active_tasks,
                    ROUND(AVG(CASE WHEN status = 'COMPLETED' AND completed_at IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (completed_at - created_at)) / 3600 END), 2) as avg_completion_hours
                FROM tasks
                WHERE deleted_at IS NULL
            """
            
            if user_role != 'ADMIN':
                # Filter by department/team for managers
                user_department = user.get('department_id')
                user_teams = user.get('team_ids', [])
                
                if user_department or user_teams:
                    stats_query += " AND ("
                    conditions = []
                    
                    if user_department:
                        # Compare department id as text to avoid integer/UUID mismatches
                        conditions.append(f"department_id::text = '{user_department}'")
                    
                    if user_teams:
                        team_placeholders = ','.join([f"'{team}'" for team in user_teams])
                        conditions.append(f"team_id IN ({team_placeholders})")
                    
                    stats_query += " OR ".join(conditions) + ")"
            
            stats = await conn.fetchrow(stats_query)
            
            # Get distribution data
            priority_dist_query = """
                SELECT priority, COUNT(*) as count
                FROM tasks
                WHERE deleted_at IS NULL
            """
            status_dist_query = """
                SELECT status, COUNT(*) as count
                FROM tasks
                WHERE deleted_at IS NULL
            """
            type_dist_query = """
                SELECT task_type, COUNT(*) as count
                FROM tasks
                WHERE deleted_at IS NULL
            """
            
            # Apply same filters for distributions
            if user_role != 'ADMIN':
                user_department = user.get('department_id')
                user_teams = user.get('team_ids', [])
                
                if user_department or user_teams:
                    filter_clause = " AND ("
                    conditions = []
                    
                    if user_department:
                        # Compare department id as text to avoid integer/UUID mismatches
                        conditions.append(f"department_id::text = '{user_department}'")
                    
                    if user_teams:
                        team_placeholders = ','.join([f"'{team}'" for team in user_teams])
                        conditions.append(f"team_id IN ({team_placeholders})")
                    
                    filter_clause += " OR ".join(conditions) + ")"
                    
                    priority_dist_query += filter_clause
                    status_dist_query += filter_clause
                    type_dist_query += filter_clause
            
            priority_dist_query += " GROUP BY priority"
            status_dist_query += " GROUP BY status"
            type_dist_query += " GROUP BY task_type"
            
            priority_dist = await conn.fetch(priority_dist_query)
            status_dist = await conn.fetch(status_dist_query)
            type_dist = await conn.fetch(type_dist_query)
            
            # Calculate completion rate
            total_tasks = stats['total_tasks'] or 0
            completed_tasks = stats['completed_tasks'] or 0
            completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
            
            return {
                'total_tasks': total_tasks,
                'completed_tasks': completed_tasks,
                'in_progress_tasks': stats['in_progress_tasks'] or 0,
                'overdue_tasks': stats['overdue_tasks'] or 0,
                'active_tasks': stats['active_tasks'] or 0,
                'completion_rate': round(completion_rate, 2),
                'average_completion_time_hours': stats['avg_completion_hours'],
                'tasks_by_priority': {row['priority']: row['count'] for row in priority_dist},
                'tasks_by_status': {row['status']: row['count'] for row in status_dist},
                'tasks_by_type': {row['task_type']: row['count'] for row in type_dist}
            }
    
    @staticmethod
    async def _get_task_permissions(user: dict, task: Dict[str, Any]) -> Dict[str, bool]:
        """
        Get user's permissions for a specific task
        """
        user_id = user.get('id')
        user_role = (user.get('role') or '').upper()
        
        is_creator = task.get('assigned_by_id') == user_id
        is_assigned = task.get('assigned_to_id') == user_id
        task_type = task.get('task_type')
        
        return {
            'can_view': await TaskService._check_task_permissions(user, task, 'read'),
            'can_edit': await TaskService._check_task_permissions(user, task, 'update'),
            'can_assign': await TaskService._check_task_permissions(user, task, 'assign'),
            'can_complete': await TaskService._check_task_permissions(user, task, 'complete'),
            'can_delete': await TaskService._check_task_permissions(user, task, 'delete'),
            'can_comment': is_creator or is_assigned,  # Can comment if involved
            'can_add_attachments': is_creator or is_assigned,  # Can add files if involved
        }
