# app/checklists/schemas.py
from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from datetime import datetime, date, time
from enum import Enum
from uuid import UUID

# Enums matching database
class ShiftType(str, Enum):
    MORNING = "MORNING"
    AFTERNOON = "AFTERNOON"
    NIGHT = "NIGHT"

class ChecklistItemType(str, Enum):
    ROUTINE = "ROUTINE"
    TIMED = "TIMED"
    SCHEDULED_EVENT = "SCHEDULED_EVENT"
    CONDITIONAL = "CONDITIONAL"
    INFORMATIONAL = "INFORMATIONAL"

class ItemStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"

class ChecklistStatus(str, Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    PENDING_REVIEW = "PENDING_REVIEW"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_EXCEPTIONS = "COMPLETED_WITH_EXCEPTIONS"
    INCOMPLETE = "INCOMPLETE"

class ActivityAction(str, Enum):
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    COMMENTED = "COMMENTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    SKIPPED = "SKIPPED"
    ESCALATED = "ESCALATED"

# User Info Model - moved here to be available for all response models
class UserInfo(BaseModel):
    id: str  # Changed from UUID to str for proper JSON serialization
    username: str
    email: str
    first_name: str
    last_name: str
    role: str

# Mutation Response Schema - Writes confirm. Reads explain. Never mix them.
class ChecklistMutationResponse(BaseModel):
    """Lightweight response for mutation endpoints - confirms operation success"""
    instance: Dict[str, Any]
    effects: Dict[str, Any]

# Base Models
class ChecklistTemplateBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    shift: ShiftType
    is_active: bool = True

class ChecklistTemplateItemBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None
    item_type: ChecklistItemType
    is_required: bool = True
    scheduled_time: Optional[time] = None
    notify_before_minutes: Optional[int] = Field(None, ge=0, le=1440)
    severity: int = Field(default=1, ge=1, le=5)
    sort_order: int = Field(default=0, ge=0)

# Subitem Models - Hierarchical checklist structure
class ChecklistTemplateSubitemBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None
    item_type: ChecklistItemType = ChecklistItemType.ROUTINE
    is_required: bool = True
    severity: int = Field(default=1, ge=1, le=5)
    sort_order: int = Field(default=0, ge=0)

class ChecklistTemplateSubitemCreate(ChecklistTemplateSubitemBase):
    pass

class ChecklistTemplateItemWithSubitems(ChecklistTemplateItemBase):
    """Template item with nested subitems for creation/update"""
    subitems: Optional[List[ChecklistTemplateSubitemBase]] = []

class ChecklistTemplateSubitemResponse(ChecklistTemplateSubitemBase):
    id: str
    template_item_id: UUID
    created_at: datetime
    
    class Config:
        orm_mode = True

class ChecklistInstanceSubitemResponse(BaseModel):
    id: str
    instance_item_id: UUID
    title: str
    description: Optional[str]
    item_type: ChecklistItemType
    is_required: bool
    severity: int
    sort_order: int
    status: ItemStatus
    completed_by: Optional[UserInfo]
    completed_at: Optional[datetime]
    skipped_reason: Optional[str]
    failure_reason: Optional[str]
    created_at: datetime
    
    class Config:
        orm_mode = True

class SubitemCompletionRequest(BaseModel):
    """Request to update a subitem status"""
    status: ItemStatus = Field(..., description="Status: PENDING, IN_PROGRESS, COMPLETED, SKIPPED, or FAILED")
    reason: Optional[str] = Field(None, max_length=1000, description="Reason for skip/fail")
    comment: Optional[str] = Field(None, max_length=2000)

class ChecklistInstanceBase(BaseModel):
    checklist_date: date
    shift: ShiftType
    shift_start: datetime
    shift_end: datetime
    status: ChecklistStatus = ChecklistStatus.OPEN

# Request Models
class ChecklistTemplateCreate(ChecklistTemplateBase):
    """Create template with nested items and subitems"""
    section_id: Optional[UUID] = None
    items: Optional[List[ChecklistTemplateItemWithSubitems]] = []

class ChecklistTemplateUpdate(BaseModel):
    """Update template (full or partial)"""
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    section_id: Optional[UUID] = None
    items: Optional[List[ChecklistTemplateItemWithSubitems]] = None

class ChecklistTemplateItemCreate(ChecklistTemplateItemWithSubitems):
    """Create item with subitems for a template"""
    pass

class ChecklistTemplateItemUpdate(BaseModel):
    """Update template item with optional subitems"""
    title: Optional[str] = None
    description: Optional[str] = None
    item_type: Optional[ChecklistItemType] = None
    is_required: Optional[bool] = None
    scheduled_time: Optional[time] = None
    notify_before_minutes: Optional[int] = None
    severity: Optional[int] = None
    sort_order: Optional[int] = None
    subitems: Optional[List[ChecklistTemplateSubitemBase]] = None

class ChecklistInstanceCreate(BaseModel):
    checklist_date: date = Field(default_factory=date.today)
    shift: ShiftType
    template_id: Optional[UUID] = None  # If None, uses active template for shift
    section_id: Optional[UUID] = None

class ChecklistItemUpdate(BaseModel):
    status: ItemStatus
    comment: Optional[str] = None
    reason: Optional[str] = Field(None, max_length=1000)
    evidence_data: Optional[Dict[str, Any]] = None
    action_type: Optional[ActivityAction] = None
    metadata: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None

class ItemActivityCreate(BaseModel):
    action: ActivityAction
    comment: Optional[str] = None

class HandoverNoteCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)
    priority: int = Field(default=2, ge=1, le=4)
    to_shift: Optional[ShiftType] = None
    to_date: Optional[date] = None

# Response Models

class UserInfo(BaseModel):
    id: str  # Changed from UUID to str for proper JSON serialization
    username: str
    email: str
    first_name: str
    last_name: str
    role: str

class ChecklistTemplateItemResponse(ChecklistTemplateItemBase):
    id: str
    template_id: UUID
    created_at: datetime
    subitems: List[ChecklistTemplateSubitemResponse] = []

    class Config:
        orm_mode = True

class ChecklistTemplateResponse(ChecklistTemplateBase):
    id: UUID
    version: int
    created_by: Optional[UUID]
    created_at: datetime
    section_id: Optional[UUID]
    items: List[ChecklistTemplateItemResponse] = []

    class Config:
        orm_mode = True

class ChecklistItemActivityResponse(BaseModel):
    id: UUID
    instance_item_id: UUID
    user: UserInfo
    action: ActivityAction
    comment: Optional[str]
    created_at: datetime

    class Config:
        orm_mode = True

class ChecklistInstanceItemResponse(BaseModel):
    id: str  # Changed from UUID to str for proper JSON serialization
    template_item: ChecklistTemplateItemResponse
    status: ItemStatus
    completed_by: Optional[UserInfo]
    completed_at: Optional[datetime]
    skipped_reason: Optional[str]
    failure_reason: Optional[str]
    notes: Optional[str] = None
    activities: List[ChecklistItemActivityResponse] = []
    # Subitems support
    subitems: List[ChecklistInstanceSubitemResponse] = []
    subitems_status: Optional[str] = None  # COMPLETED, COMPLETED_WITH_EXCEPTIONS, IN_PROGRESS, PENDING

    class Config:
        orm_mode = True

class ChecklistInstanceResponse(ChecklistInstanceBase):
    id: str  # Changed from UUID to str for proper JSON serialization
    template: ChecklistTemplateResponse
    created_by: Optional[UserInfo]
    closed_by: Optional[UserInfo]
    closed_at: Optional[datetime]
    created_at: datetime
    items: List[ChecklistInstanceItemResponse] = []
    participants: List[UserInfo] = []
    completion_percentage: float = 0.0
    time_remaining_minutes: Optional[int] = None

    class Config:
        orm_mode = True

class HandoverNoteResponse(BaseModel):
    id: UUID
    from_instance: ChecklistInstanceResponse
    to_instance: Optional[ChecklistInstanceResponse]
    content: str
    priority: int
    acknowledged_by: Optional[UserInfo]
    acknowledged_at: Optional[datetime]
    resolved_by: Optional[UserInfo]
    resolved_at: Optional[datetime]
    resolution_notes: Optional[str]
    created_by: UserInfo
    created_at: datetime

    class Config:
        orm_mode = True

class ChecklistStats(BaseModel):
    total_items: int
    completed_items: int
    skipped_items: int
    failed_items: int
    pending_items: int
    completion_percentage: float
    required_completion_percentage: float
    estimated_time_remaining_minutes: int

class ItemStartWorkResponse(BaseModel):
    """Response when user starts working on an item"""
    item_id: str
    item_title: str
    item_status: ItemStatus
    has_subitems: bool
    subitems: List[ChecklistInstanceSubitemResponse] = []
    next_subitem: Optional[ChecklistInstanceSubitemResponse] = None  # First pending subitem
    subitem_count: int = 0
    completed_subitem_count: int = 0
    
    class Config:
        orm_mode = True

class ShiftPerformance(BaseModel):
    shift_date: date
    shift_type: ShiftType
    total_instances: int
    completed_on_time: int
    completed_with_exceptions: int
    avg_completion_time_minutes: float
    avg_points_per_shift: float
    team_engagement_score: float

class PaginatedResponse(BaseModel):
    items: List[Any]
    total: int
    page: int
    pages: int
    has_next: bool
    has_prev: bool

# Template Mutation Responses
class TemplateMutationResponse(BaseModel):
    """Response for template create/update/delete operations"""
    id: str
    action: str  # "created", "updated", "deleted"
    template: Optional[ChecklistTemplateResponse] = None
    message: str
    
    class Config:
        orm_mode = True

class TemplateItemMutationResponse(BaseModel):
    """Response for template item create/update/delete"""
    id: str
    template_id: str
    action: str  # "created", "updated", "deleted"
    item: Optional[ChecklistTemplateItemResponse] = None
    message: str
    
    class Config:
        orm_mode = True

class TemplateSubitemMutationResponse(BaseModel):
    """Response for subitem create/update/delete"""
    id: str
    item_id: str
    action: str  # "created", "updated", "deleted"
    subitem: Optional[ChecklistTemplateSubitemResponse] = None
    message: str
    
    class Config:
        orm_mode = True