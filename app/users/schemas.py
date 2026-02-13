from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class UserBase(BaseModel):
    """Shared fields for SentinelOps user objects."""

    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    department_id: Optional[int] = None
    section_id: Optional[str] = None


class UserCreate(UserBase):
    """Payload for creating a new user."""

    password: str = Field(..., min_length=8, max_length=128)
    # Logical/business role; mapped to DB roles (admin/manager/user)
    role: str = Field(..., description="Role name (admin, manager, user)")


class UserUpdate(BaseModel):
    """Partial update payload for an existing user."""

    username: Optional[str] = Field(None, min_length=3, max_length=50)
    email: Optional[EmailStr] = None
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    department_id: Optional[int] = None
    section_id: Optional[str] = None
    password: Optional[str] = Field(
        None, min_length=8, max_length=128, description="New password (optional)"
    )
    role: Optional[str] = Field(
        None, description="New role (admin, manager, user)"
    )
    is_active: Optional[bool] = Field(
        None, description="Activate/deactivate user instead of hard delete"
    )


class UserListItem(BaseModel):
    """Lightweight representation for list views (mirrors UserResponse where possible)."""

    id: str
    username: str
    email: str
    first_name: str
    last_name: str
    department_id: Optional[int] = None
    section_id: Optional[str] = None
    department_name: str = ""
    section_name: str = ""
    role: str
    is_active: bool
    created_at: datetime

