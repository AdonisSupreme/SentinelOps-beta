# app/tasks/schemas.py
from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from enum import Enum
from uuid import UUID

# Enums matching database
class TaskType(str, Enum):
    PERSONAL = "PERSONAL"
    TEAM = "TEAM"
    DEPARTMENT = "DEPARTMENT"
    SYSTEM = "SYSTEM"

class Priority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

class TaskStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    ON_HOLD = "ON_HOLD"

class CommentType(str, Enum):
    COMMENT = "COMMENT"
    STATUS_UPDATE = "STATUS_UPDATE"
    ASSIGNMENT_CHANGE = "ASSIGNMENT_CHANGE"
    SYSTEM_UPDATE = "SYSTEM_UPDATE"

class TaskAction(str, Enum):
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    ASSIGNED = "ASSIGNED"
    UNASSIGNED = "UNASSIGNED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    REOPENED = "REOPENED"

# User Info Model - consistent with existing patterns
class UserInfo(BaseModel):
    id: str  # Changed from UUID to str for proper JSON serialization
    username: str
    email: str
    first_name: str
    last_name: str
    role: str

# Base Models
class TaskBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    task_type: TaskType
    priority: Priority = Priority.MEDIUM
    status: TaskStatus = TaskStatus.ACTIVE
    assigned_to_id: Optional[UUID] = None
    # Note: `department` is stored as an integer id in the DB migration
    department_id: Optional[int] = None
    section_id: Optional[UUID] = None
    due_date: Optional[datetime] = None
    estimated_hours: Optional[float] = Field(None, ge=0.1)
    tags: List[str] = Field(default_factory=list)
    parent_task_id: Optional[UUID] = None
    is_recurring: bool = False
    recurrence_pattern: Optional[str] = Field(None, max_length=500)

class TaskCreate(TaskBase):
    assigned_by_id: UUID  # Creator ID required for creation

class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    priority: Optional[Priority] = None
    status: Optional[TaskStatus] = None
    assigned_to_id: Optional[UUID] = None
    # department id is integer in DB migration
    department_id: Optional[int] = None
    section_id: Optional[UUID] = None
    due_date: Optional[datetime] = None
    estimated_hours: Optional[float] = Field(None, ge=0.1)
    actual_hours: Optional[float] = Field(None, ge=0)
    completion_percentage: Optional[int] = Field(None, ge=0, le=100)
    tags: Optional[List[str]] = None
    is_recurring: Optional[bool] = None
    recurrence_pattern: Optional[str] = Field(None, max_length=500)

class TaskAssignment(BaseModel):
    assigned_to_id: UUID
    assigned_by_id: UUID
    notes: Optional[str] = Field(None, max_length=1000)

# Response Models
class TaskResponse(TaskBase):
    id: UUID
    assigned_by_id: UUID
    actual_hours: Optional[float] = None
    completion_percentage: int = 0
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    
    # Nested relationships
    assigned_to: Optional[UserInfo] = None
    assigned_by: Optional[UserInfo] = None
    department: Optional[Dict[str, Any]] = None
    section: Optional[Dict[str, Any]] = None
    parent_task: Optional[Dict[str, Any]] = None
    subtasks_count: int = 0
    comments_count: int = 0
    attachments_count: int = 0
    # Include detailed related lists for full task view
    comments: Optional[List[Dict[str, Any]]] = None
    attachments: Optional[List[Dict[str, Any]]] = None
    subtasks: Optional[List[Dict[str, Any]]] = None
    history: Optional[List[Dict[str, Any]]] = None
    permissions: Optional[Dict[str, bool]] = None
    
    class Config:
        from_attributes = True

class TaskSummary(BaseModel):
    """Lightweight task summary for list views"""
    id: UUID
    title: str
    status: TaskStatus
    priority: Priority
    task_type: TaskType
    assigned_to: Optional[UserInfo] = None
    due_date: Optional[datetime] = None
    completion_percentage: int = 0
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

# Comment Models
class TaskCommentBase(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    comment_type: CommentType = CommentType.COMMENT

class TaskCommentCreate(TaskCommentBase):
    task_id: UUID
    user_id: UUID

class TaskCommentResponse(TaskCommentBase):
    id: UUID
    task_id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime
    
    # Nested user info
    user: Optional[UserInfo] = None
    
    class Config:
        from_attributes = True

# Attachment Models
class TaskAttachmentBase(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    file_path: str = Field(..., min_length=1, max_length=500)
    file_size: int = Field(..., gt=0)
    mime_type: str = Field(..., min_length=1, max_length=100)

class TaskAttachmentCreate(TaskAttachmentBase):
    task_id: UUID
    uploaded_by_id: UUID

class TaskAttachmentResponse(TaskAttachmentBase):
    id: UUID
    task_id: UUID
    uploaded_by_id: UUID
    uploaded_at: datetime
    
    # Nested user info
    uploaded_by: Optional[UserInfo] = None
    
    class Config:
        from_attributes = True

# History Models
class TaskHistoryBase(BaseModel):
    action: TaskAction
    old_values: Optional[Dict[str, Any]] = None
    new_values: Optional[Dict[str, Any]] = None

class TaskHistoryResponse(TaskHistoryBase):
    id: UUID
    task_id: UUID
    user_id: UUID
    timestamp: datetime
    
    # Nested user info
    user: Optional[UserInfo] = None
    
    class Config:
        from_attributes = True

# Analytics Models
class TaskAnalytics(BaseModel):
    total_tasks: int
    completed_tasks: int
    overdue_tasks: int
    in_progress_tasks: int
    completion_rate: float
    average_completion_time_hours: Optional[float] = None
    tasks_by_priority: Dict[str, int]
    tasks_by_status: Dict[str, int]
    tasks_by_type: Dict[str, int]

class UserTaskAnalytics(BaseModel):
    user_id: UUID
    user: UserInfo
    assigned_tasks: int
    completed_tasks: int
    completion_rate: float
    average_completion_time_hours: Optional[float] = None
    overdue_tasks: int
    current_workload: int

# Filter and Query Models
class TaskFilters(BaseModel):
    status: Optional[List[TaskStatus]] = None
    assigned_to: Optional[UUID] = None
    assigned_by: Optional[UUID] = None
    # department id uses integer in DB migration
    department_id: Optional[int] = None
    section_id: Optional[UUID] = None
    priority: Optional[List[Priority]] = None
    task_type: Optional[List[TaskType]] = None
    due_before: Optional[datetime] = None
    due_after: Optional[datetime] = None
    created_after: Optional[datetime] = None
    created_before: Optional[datetime] = None
    tags: Optional[List[str]] = None
    search: Optional[str] = Field(None, min_length=1, max_length=100)
    parent_task_id: Optional[UUID] = None
    is_overdue: Optional[bool] = None

class TaskListRequest(BaseModel):
    filters: Optional[TaskFilters] = None
    sort: str = Field("created_at", pattern="^(created_at|updated_at|due_date|title|priority|status)$")
    order: str = Field("desc", pattern="^(asc|desc)$")
    limit: int = Field(50, ge=1, le=100)
    offset: int = Field(0, ge=0)

# Mutation Response Models - consistent with existing patterns
class TaskMutationResponse(BaseModel):
    """Lightweight response for mutation endpoints - confirms operation success"""
    task: Dict[str, Any]
    effects: Dict[str, Any]

class BulkTaskOperation(BaseModel):
    task_ids: List[UUID] = Field(..., min_items=1, max_items=50)
    operation: str = Field(..., pattern="^(assign|complete|cancel|delete)$")
    parameters: Optional[Dict[str, Any]] = None

class BulkTaskResponse(BaseModel):
    successful: List[UUID]
    failed: List[Dict[str, Any]]
    total_processed: int
    success_count: int
    failure_count: int
