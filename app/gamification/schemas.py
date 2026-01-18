# app/gamification/schemas.py
from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum
from datetime import datetime, date
from uuid import UUID

class ScoreReason(str, Enum):
    ON_TIME_COMPLETION = "ON_TIME_COMPLETION"
    EARLY_COMPLETION = "EARLY_COMPLETION"
    PERFECT_SHIFT = "PERFECT_SHIFT"
    TEAM_COLLABORATION = "TEAM_COLLABORATION"
    QUICK_RESOLUTION = "QUICK_RESOLUTION"
    ESCALATION_HANDLED = "ESCALATION_HANDLED"
    EXCEPTION_PREVENTED = "EXCEPTION_PREVENTED"
    SUPERVISOR_APPROVAL = "SUPERVISOR_APPROVAL"

class GamificationScore(BaseModel):
    id: UUID
    user_id: UUID
    shift_instance_id: UUID
    points: int
    reason: ScoreReason
    metadata: Optional[dict]
    awarded_by: Optional[UUID]
    awarded_at: datetime

class UserStreak(BaseModel):
    user_id: UUID
    current_streak_days: int
    longest_streak_days: int
    perfect_shifts_count: int
    total_points: int
    shift_completion_rate: float
    last_shift_date: Optional[date]

class LeaderboardEntry(BaseModel):
    user_id: UUID
    username: str
    total_points: int
    current_streak: int
    perfect_shifts: int
    rank: int
    avatar_url: Optional[str]

class Achievement(BaseModel):
    id: UUID
    name: str
    description: str
    icon: str
    points_required: int
    streak_required: Optional[int]
    perfect_shifts_required: Optional[int]
    unlocked_by: Optional[List[UUID]]