# app/notifications/schemas.py
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from uuid import UUID

class NotificationBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=1000)
    related_entity: Optional[str] = None
    related_id: Optional[UUID] = None

class NotificationResponse(NotificationBase):
    id: UUID
    user_id: Optional[UUID]
    role_id: Optional[UUID]
    is_read: bool
    created_at: datetime
    
    class Config:
        orm_mode = True

class NotificationUpdate(BaseModel):
    is_read: Optional[bool] = None

class NotificationPreferences(BaseModel):
    email_notifications: bool = True
    push_notifications: bool = True
    sms_notifications: bool = False
    desktop_notifications: bool = True
    quiet_hours_start: Optional[str] = None  # Format: "HH:MM"
    quiet_hours_end: Optional[str] = None