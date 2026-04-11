# app/gamification/schemas.py
from pydantic import BaseModel, Field
from typing import Dict, Optional, List
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


class PerformanceWindow(str, Enum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class PerformanceBreakdown(BaseModel):
    execution_points: int
    reliability_points: int
    collaboration_points: int
    quality_points: int


class PerformanceWindowSnapshot(BaseModel):
    key: PerformanceWindow
    label: str
    start_date: date
    end_date: date
    command_points: int
    operational_grade: int
    tier: str
    rank: int
    total_users: int
    contribution_days: int
    current_streak: int
    longest_streak: int
    items_completed: int
    critical_items_completed: int
    checklists_joined: int
    clean_checklists: int
    tasks_completed: int
    tasks_completed_on_time: int
    critical_tasks_completed: int
    high_tasks_completed: int
    collaborative_tasks_completed: int
    handovers_created: int
    handovers_resolved: int
    overdue_open_tasks: int
    task_on_time_rate: float
    checklist_quality_rate: float
    shift_adherence_rate: float
    consistency_rate: float
    summary: str
    breakdown: PerformanceBreakdown


class PerformanceProfile(BaseModel):
    user_id: UUID
    username: str
    display_name: str
    section_name: Optional[str]
    badge_count: int


class PerformanceLeaderboardEntry(BaseModel):
    user_id: UUID
    username: str
    display_name: str
    section_name: Optional[str]
    command_points: int
    operational_grade: int
    rank: int
    current_streak: int
    tasks_completed: int
    clean_checklists: int
    tier: str
    is_current_user: bool


class PerformanceBadge(BaseModel):
    key: str
    name: str
    icon: str
    theme: str
    description: str
    hint: str
    earned: bool
    claimed: bool
    claimable: bool
    progress: float
    target: str
    claimed_at: Optional[datetime] = None


class PerformanceTrendPoint(BaseModel):
    label: str
    period_start: date
    period_end: date
    command_points: int
    operational_grade: int
    tasks_completed: int
    checklist_items_completed: int
    handovers_resolved: int


class PerformanceRecentEvent(BaseModel):
    id: str
    event_type: str
    title: str
    detail: str
    occurred_at: datetime
    points: int


class PerformanceCommandResponse(BaseModel):
    generated_at: datetime
    focus_window: PerformanceWindow
    scope_label: str
    profile: PerformanceProfile
    windows: Dict[str, PerformanceWindowSnapshot]
    active_window: PerformanceWindowSnapshot
    leaderboard: List[PerformanceLeaderboardEntry]
    badges: List[PerformanceBadge]
    next_badge: Optional[PerformanceBadge] = None
    trend: List[PerformanceTrendPoint]
    recent_events: List[PerformanceRecentEvent]


class PerformanceBadgeClaimResponse(BaseModel):
    badge: PerformanceBadge
    claimed_badge_count: int
