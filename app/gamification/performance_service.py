from __future__ import annotations

import asyncio

from collections import defaultdict
from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional
from uuid import UUID

from app.core.authorization import is_admin
from app.core.config import settings
from app.core.email_templates import performance_badge_unlocked_template
from app.core.emailer import send_email_fire_and_forget
from app.core.logging import get_logger
from app.db.database import get_async_connection
from app.notifications.db_service import NotificationDBService

log = get_logger("performance-command-service")

PERFORMANCE_WINDOWS = ("weekly", "monthly", "quarterly", "yearly")
TIMED_START_GRACE_MINUTES = 5

WINDOW_LABELS = {
    "weekly": "Week to date",
    "monthly": "Month to date",
    "quarterly": "Quarter to date",
    "yearly": "Year to date",
}

WINDOW_TIERS = {
    "weekly": ((160, "Pulse"), (320, "Vector"), (540, "Command"), (999999, "Apex")),
    "monthly": ((650, "Pulse"), (1450, "Vector"), (2500, "Command"), (999999, "Apex")),
    "quarterly": ((1450, "Pulse"), (3200, "Vector"), (5600, "Command"), (999999, "Apex")),
    "yearly": ((4200, "Pulse"), (9200, "Vector"), (16000, "Command"), (999999, "Apex")),
}

BADGE_DEFINITIONS = (
    {
        "key": "flow_keeper",
        "name": "Flow Keeper",
        "icon": "flow",
        "theme": "azure",
        "description": "Close work on time and keep delivery pressure under control.",
        "hint": "Raise task volume and hold the task on-time rate above 85%.",
    },
    {
        "key": "checklist_vanguard",
        "name": "Checklist Vanguard",
        "icon": "shield",
        "theme": "emerald",
        "description": "Run clean checklist operations with strong required-item discipline.",
        "hint": "Drive more completed checklist threads and keep them clean.",
    },
    {
        "key": "critical_shield",
        "name": "Critical Shield",
        "icon": "bolt",
        "theme": "amber",
        "description": "Handle critical operational work without dropping the tempo.",
        "hint": "Accumulate more critical checklist and task actions.",
    },
    {
        "key": "relay_architect",
        "name": "Relay Architect",
        "icon": "relay",
        "theme": "violet",
        "description": "Keep operational context moving through handovers and shared work.",
        "hint": "Create and resolve more handovers to strengthen the relay.",
    },
    {
        "key": "tempo_builder",
        "name": "Tempo Builder",
        "icon": "orbit",
        "theme": "cyan",
        "description": "Build a consistent rhythm of contribution across the operational calendar.",
        "hint": "Contribute on more days and extend your live streak.",
    },
    {
        "key": "night_owl",
        "name": "Night Owl",
        "icon": "moon",
        "theme": "violet",
        "description": "Carry the operation through the night by landing real checklist work in overnight runs.",
        "hint": "Complete more checklist actions inside night-shift instances.",
    },
    {
        "key": "first_light",
        "name": "First Light",
        "icon": "sunrise",
        "theme": "amber",
        "description": "Open the day with dependable morning-shift execution that keeps the lane stable.",
        "hint": "Complete more morning-shift checklist actions across multiple runs.",
    },
    {
        "key": "swing_captain",
        "name": "Swing Captain",
        "icon": "sun",
        "theme": "azure",
        "description": "Hold the afternoon handoff window together with steady completion pressure.",
        "hint": "Drive more afternoon-shift checklist actions across multiple runs.",
    },
    {
        "key": "sync_catalyst",
        "name": "Sync Catalyst",
        "icon": "allies",
        "theme": "emerald",
        "description": "Earn this by completing real checklist work alongside teammates in the same active shift.",
        "hint": "Contribute more actions inside checklist runs where multiple operators complete the work together.",
    },
    {
        "key": "clockwork",
        "name": "Clockwork",
        "icon": "clock",
        "theme": "cyan",
        "description": "Timed items and subitems are started on cue, not just closed eventually.",
        "hint": "Keep timed-start discipline high across scheduled checklist actions.",
    },
    {
        "key": "sentinel_prime",
        "name": "Sentinel Prime",
        "icon": "crown",
        "theme": "rose",
        "description": "Balance output, quality, reliability, and clean backlog control at elite level.",
        "hint": "Increase command points while keeping grade high and overdue load low.",
    },
)
BADGE_KEYS = frozenset(badge["key"] for badge in BADGE_DEFINITIONS)


@dataclass
class UserDirectoryEntry:
    user_id: UUID
    username: str
    display_name: str
    section_name: Optional[str] = None
    is_current_user: bool = False


@dataclass
class DailyMetrics:
    checklist_points: int = 0
    subitem_points: int = 0
    task_points: int = 0
    items_completed: int = 0
    required_items_completed: int = 0
    critical_items_completed: int = 0
    subitems_completed: int = 0
    checklists_joined: int = 0
    completed_checklists_joined: int = 0
    on_time_checklists: int = 0
    clean_checklists: int = 0
    collaborative_checklists: int = 0
    tasks_completed: int = 0
    tasks_completed_on_time: int = 0
    late_tasks_completed: int = 0
    high_tasks_completed: int = 0
    critical_tasks_completed: int = 0
    collaborative_tasks_completed: int = 0
    collaborative_actions: int = 0
    collaborative_instances_contributed: int = 0
    morning_shift_actions: int = 0
    afternoon_shift_actions: int = 0
    night_shift_actions: int = 0
    morning_shift_runs: int = 0
    afternoon_shift_runs: int = 0
    night_shift_runs: int = 0
    timed_actions_started: int = 0
    timed_actions_started_on_time: int = 0
    timed_actions_started_late: int = 0
    late_start_penalty_points: int = 0
    late_task_penalty_points: int = 0
    handovers_created: int = 0
    handovers_resolved: int = 0
    assigned_shifts: int = 0
    staffed_shifts: int = 0

    def add_values(self, **kwargs) -> None:
        for field_name, value in kwargs.items():
            if value is None:
                continue
            setattr(self, field_name, getattr(self, field_name) + int(value))

    def merge(self, other: "DailyMetrics") -> None:
        for field_def in fields(self):
            setattr(self, field_def.name, getattr(self, field_def.name) + getattr(other, field_def.name))

    def has_activity(self) -> bool:
        return any(getattr(self, field_def.name) for field_def in fields(self))


@dataclass
class WindowSnapshot:
    key: str
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
    late_tasks_completed: int
    critical_tasks_completed: int
    high_tasks_completed: int
    collaborative_tasks_completed: int
    collaborative_actions: int
    collaborative_instances_contributed: int
    morning_shift_actions: int
    afternoon_shift_actions: int
    night_shift_actions: int
    morning_shift_runs: int
    afternoon_shift_runs: int
    night_shift_runs: int
    timed_actions_started: int
    timed_actions_started_on_time: int
    timed_actions_started_late: int
    late_start_penalty_points: int
    late_task_penalty_points: int
    handovers_created: int
    handovers_resolved: int
    overdue_open_tasks: int
    task_on_time_rate: float
    checklist_quality_rate: float
    shift_adherence_rate: float
    timed_start_rate: float
    consistency_rate: float
    summary: str
    execution_points: int
    reliability_points: int
    collaboration_points: int
    quality_points: int


def _weighted_average(pairs: Iterable[tuple[Optional[float], float]]) -> float:
    numerator = 0.0
    denominator = 0.0
    for value, weight in pairs:
        if value is None:
            continue
        numerator += value * weight
        denominator += weight
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _format_display_name(username: Optional[str], first_name: Optional[str], last_name: Optional[str]) -> str:
    full_name = " ".join(part for part in [first_name, last_name] if part).strip()
    return full_name or (username or "SentinelOps operator")


def _period_ranges(today: date) -> Dict[str, tuple[date, date]]:
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    quarter_month = ((today.month - 1) // 3) * 3 + 1
    quarter_start = today.replace(month=quarter_month, day=1)
    year_start = today.replace(month=1, day=1)
    return {
        "weekly": (week_start, today),
        "monthly": (month_start, today),
        "quarterly": (quarter_start, today),
        "yearly": (year_start, today),
    }


def _snapshot_tier(window_key: str, command_points: int) -> str:
    for threshold, label in WINDOW_TIERS[window_key]:
        if command_points < threshold:
            return label
    return "Apex"


def _rate_percentage(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100, 1)


def _compute_live_streak(activity_dates: List[date]) -> tuple[int, int]:
    if not activity_dates:
        return 0, 0

    sorted_dates = sorted(set(activity_dates))
    longest = 1
    current_run = 1

    for index in range(1, len(sorted_dates)):
        if sorted_dates[index] == sorted_dates[index - 1] + timedelta(days=1):
            current_run += 1
            longest = max(longest, current_run)
        else:
            current_run = 1

    anchor = sorted_dates[-1]
    if anchor < (date.today() - timedelta(days=1)):
        live = 0
    else:
        live = 1
        pointer = anchor
        while pointer - timedelta(days=1) in sorted_dates:
            live += 1
            pointer -= timedelta(days=1)

    return live, longest


def _build_summary(
    *,
    task_on_time_rate: float,
    checklist_quality_rate: float,
    shift_adherence_rate: float,
    timed_start_rate: float,
    overdue_open_tasks: int,
    critical_actions: int,
    lateness_events: int,
) -> str:
    highlights: List[str] = []

    if checklist_quality_rate >= 90:
        highlights.append("clean checklist execution is holding strong")
    elif checklist_quality_rate >= 75:
        highlights.append("checklist quality is healthy")
    elif checklist_quality_rate > 0:
        highlights.append("checklist quality still has room to tighten")

    if task_on_time_rate >= 90:
        highlights.append("task closure is landing on time")
    elif task_on_time_rate >= 75:
        highlights.append("task closure is steady")
    elif task_on_time_rate > 0:
        highlights.append("task timeliness needs attention")

    if shift_adherence_rate >= 90:
        highlights.append("shift follow-through is disciplined")
    elif shift_adherence_rate >= 70:
        highlights.append("shift follow-through is stable")
    elif shift_adherence_rate > 0:
        highlights.append("shift adherence is uneven")

    if timed_start_rate >= 90:
        highlights.append("timed starts are landing on cue")
    elif timed_start_rate >= 75:
        highlights.append("timed start discipline is steady")
    elif timed_start_rate > 0:
        highlights.append("timed starts still need tightening")

    if overdue_open_tasks > 0:
        suffix = "s" if overdue_open_tasks != 1 else ""
        verb = "need" if overdue_open_tasks != 1 else "needs"
        highlights.append(f"{overdue_open_tasks} overdue task{suffix} still {verb} clearing")

    if lateness_events >= 6:
        highlights.append("repeat lateness is shaving points off the run")

    if critical_actions >= 10:
        highlights.append("critical load is being handled with confidence")

    if not highlights:
        return "This window is still warming up. More real work will sharpen the signal."

    return highlights[0].capitalize() + ". " + (" ".join(sentence.capitalize() + "." for sentence in highlights[1:3]))


def _score_aggregate(
    *,
    aggregate: DailyMetrics,
    elapsed_days: int,
    contribution_days: int,
    current_streak: int,
    overdue_open_tasks: int = 0,
) -> dict:
    task_on_time_rate = _rate_percentage(aggregate.tasks_completed_on_time, aggregate.tasks_completed)
    checklist_on_time_rate = _rate_percentage(
        aggregate.on_time_checklists,
        aggregate.completed_checklists_joined,
    )
    checklist_clean_rate = _rate_percentage(
        aggregate.clean_checklists,
        aggregate.completed_checklists_joined,
    )
    checklist_quality_rate = round(
        _weighted_average(
            [
                (checklist_clean_rate if aggregate.completed_checklists_joined else None, 0.65),
                (checklist_on_time_rate if aggregate.completed_checklists_joined else None, 0.35),
            ]
        ),
        1,
    )
    shift_adherence_rate = _rate_percentage(aggregate.staffed_shifts, aggregate.assigned_shifts)
    timed_start_rate = _rate_percentage(
        aggregate.timed_actions_started_on_time,
        aggregate.timed_actions_started,
    )
    consistency_rate = round(min(100.0, (contribution_days / max(elapsed_days, 1)) * 100), 1)
    late_events = aggregate.late_tasks_completed + aggregate.timed_actions_started_late
    repeat_lateness_penalty = max(late_events - 1, 0) * 4

    execution_points = aggregate.checklist_points + aggregate.subitem_points + aggregate.task_points
    reliability_points = (
        (aggregate.on_time_checklists * 14)
        + (aggregate.clean_checklists * 18)
        + (aggregate.tasks_completed_on_time * 7)
        + (aggregate.staffed_shifts * 9)
        + (aggregate.timed_actions_started_on_time * 4)
        + (max(current_streak - 1, 0) * 6)
    )
    collaboration_points = (
        (aggregate.collaborative_checklists * 8)
        + (aggregate.collaborative_tasks_completed * 12)
        + (aggregate.collaborative_instances_contributed * 6)
        + (aggregate.collaborative_actions * 2)
        + (aggregate.handovers_created * 6)
        + (aggregate.handovers_resolved * 12)
    )
    quality_points = (
        (aggregate.critical_items_completed * 10)
        + (aggregate.critical_tasks_completed * 22)
        + (aggregate.high_tasks_completed * 8)
        - (overdue_open_tasks * 15)
        - aggregate.late_start_penalty_points
        - aggregate.late_task_penalty_points
        - repeat_lateness_penalty
    )
    command_points = max(
        0,
        execution_points + reliability_points + collaboration_points + quality_points,
    )
    operational_grade = int(
        round(
            _weighted_average(
                [
                    (task_on_time_rate if aggregate.tasks_completed > 0 else None, 0.28),
                    (checklist_quality_rate if aggregate.completed_checklists_joined > 0 else None, 0.27),
                    (shift_adherence_rate if aggregate.assigned_shifts > 0 else None, 0.12),
                    (timed_start_rate if aggregate.timed_actions_started > 0 else None, 0.18),
                    (consistency_rate if contribution_days > 0 else None, 0.15),
                ]
            )
        )
    )

    return {
        "task_on_time_rate": task_on_time_rate,
        "checklist_quality_rate": checklist_quality_rate,
        "shift_adherence_rate": shift_adherence_rate,
        "timed_start_rate": timed_start_rate,
        "consistency_rate": consistency_rate,
        "late_events": late_events,
        "repeat_lateness_penalty": repeat_lateness_penalty,
        "execution_points": execution_points,
        "reliability_points": reliability_points,
        "collaboration_points": collaboration_points,
        "quality_points": quality_points,
        "command_points": command_points,
        "operational_grade": operational_grade,
    }


class PerformanceCommandService:
    @staticmethod
    def schedule_badge_unlock_sync(user_id: Optional[UUID]) -> None:
        if not user_id:
            return

        try:
            normalized_user_id = UUID(str(user_id))
            loop = asyncio.get_running_loop()
        except Exception:
            return

        task = loop.create_task(
            PerformanceCommandService.sync_badge_unlocks_for_user(normalized_user_id)
        )

        def _log_failure(completed_task: "asyncio.Task[None]") -> None:
            try:
                completed_task.result()
            except Exception as exc:
                log.warning(
                    "Deferred badge unlock sync failed for user %s: %s",
                    normalized_user_id,
                    exc,
                )

        task.add_done_callback(_log_failure)

    @staticmethod
    async def sync_badge_unlocks_for_user(user_id: UUID) -> None:
        today = date.today()
        windows = _period_ranges(today)
        year_start, year_end = windows["yearly"]

        async with get_async_connection() as conn:
            user_row = await conn.fetchrow(
                """
                SELECT
                    u.id,
                    u.username,
                    u.first_name,
                    u.last_name,
                    s.section_name
                FROM users u
                LEFT JOIN sections s ON s.id = u.section_id
                WHERE u.id = $1
                LIMIT 1
                """,
                user_id,
            )

        if not user_row:
            return

        current_entry = UserDirectoryEntry(
            user_id=user_row["id"],
            username=user_row["username"],
            display_name=_format_display_name(
                user_row["username"],
                user_row["first_name"],
                user_row["last_name"],
            ),
            section_name=user_row["section_name"],
            is_current_user=True,
        )

        directory = {user_id: current_entry}
        daily_maps = await PerformanceCommandService._load_daily_maps(
            [user_id],
            year_start,
            year_end,
        )
        overdue_snapshot = await PerformanceCommandService._load_overdue_snapshot([user_id])
        yearly_snapshot = PerformanceCommandService._build_window_snapshots(
            window_key="yearly",
            start_date=year_start,
            end_date=year_end,
            directory=directory,
            daily_maps=daily_maps,
            overdue_snapshot=overdue_snapshot,
        )[user_id]
        badge_states = await PerformanceCommandService._load_badge_states(user_id)
        badges = PerformanceCommandService._build_badges(yearly_snapshot, badge_states)
        await PerformanceCommandService._sync_badge_unlocks(
            user_id=user_id,
            current_entry=current_entry,
            badges=badges,
        )

    @staticmethod
    async def get_performance_command(current_user: dict, focus_window: str = "monthly") -> dict:
        if focus_window not in PERFORMANCE_WINDOWS:
            focus_window = "monthly"

        today = date.today()
        windows = _period_ranges(today)
        year_start, year_end = windows["yearly"]

        directory = await PerformanceCommandService._load_directory(current_user)
        if not directory:
            raise ValueError("No visible users available for performance reporting")

        user_ids = [entry.user_id for entry in directory.values()]
        current_user_id = UUID(str(current_user["id"]))

        daily_maps = await PerformanceCommandService._load_daily_maps(user_ids, year_start, year_end)
        overdue_snapshot = await PerformanceCommandService._load_overdue_snapshot(user_ids)

        snapshots_by_user: Dict[str, Dict[UUID, WindowSnapshot]] = {}
        for window_key, (window_start, window_end) in windows.items():
            snapshots_by_user[window_key] = PerformanceCommandService._build_window_snapshots(
                window_key=window_key,
                start_date=window_start,
                end_date=window_end,
                directory=directory,
                daily_maps=daily_maps,
                overdue_snapshot=overdue_snapshot,
            )

        current_snapshot = snapshots_by_user[focus_window][current_user_id]
        yearly_snapshot = snapshots_by_user["yearly"][current_user_id]
        badge_states = await PerformanceCommandService._load_badge_states(current_user_id)
        badges = PerformanceCommandService._build_badges(yearly_snapshot, badge_states)
        pending_badges = [badge for badge in badges if not badge["earned"]]
        next_badge = (
            sorted(
                pending_badges,
                key=lambda badge: (-badge["progress"], badge["name"]),
            )[0]
            if pending_badges
            else None
        )
        leaderboard = PerformanceCommandService._build_leaderboard(
            snapshots_by_user[focus_window],
            directory,
            current_user_id,
        )
        trend = PerformanceCommandService._build_trend(
            user_id=current_user_id,
            daily_maps=daily_maps,
            window_key=focus_window,
            start_date=windows[focus_window][0],
            end_date=windows[focus_window][1],
            snapshot=current_snapshot,
        )
        recent_events = await PerformanceCommandService._load_recent_events(
            user_id=current_user_id,
            start_date=windows[focus_window][0],
            end_date=windows[focus_window][1],
        )

        current_entry = directory[current_user_id]
        await PerformanceCommandService._sync_badge_unlocks(
            user_id=current_user_id,
            current_entry=current_entry,
            badges=badges,
        )
        scope_label = "Organization-wide"
        if current_user.get("section_id"):
            scope_label = current_entry.section_name or "Current section"
        elif not is_admin(current_user):
            scope_label = "Your scope"

        return {
            "generated_at": datetime.utcnow(),
            "focus_window": focus_window,
            "scope_label": scope_label,
            "profile": {
                "user_id": current_entry.user_id,
                "username": current_entry.username,
                "display_name": current_entry.display_name,
                "section_name": current_entry.section_name,
                "badge_count": sum(1 for badge in badges if badge["claimed"]),
            },
            "windows": {
                key: PerformanceCommandService._snapshot_to_dict(snapshots_by_user[key][current_user_id])
                for key in PERFORMANCE_WINDOWS
            },
            "active_window": PerformanceCommandService._snapshot_to_dict(current_snapshot),
            "leaderboard": leaderboard,
            "badges": badges,
            "next_badge": next_badge,
            "trend": trend,
            "recent_events": recent_events,
        }

    @staticmethod
    async def _column_exists(conn, table_name: str, column_name: str) -> bool:
        return bool(
            await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = $1
                      AND column_name = $2
                )
                """,
                table_name,
                column_name,
            )
        )

    @staticmethod
    async def _table_exists(conn, table_name: str) -> bool:
        return bool(
            await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = $1
                )
                """,
                table_name,
            )
        )

    @staticmethod
    async def _load_badge_states(user_id: UUID) -> Dict[str, Dict[str, Optional[datetime]]]:
        async with get_async_connection() as conn:
            if not await PerformanceCommandService._table_exists(conn, "user_performance_badge_claims"):
                return {}

            supports_unlocked_at = await PerformanceCommandService._column_exists(
                conn,
                "user_performance_badge_claims",
                "unlocked_at",
            )
            supports_unlock_notified_at = await PerformanceCommandService._column_exists(
                conn,
                "user_performance_badge_claims",
                "unlock_notified_at",
            )
            badge_state_order_expr = "COALESCE(claimed_at, unlocked_at)" if supports_unlocked_at else "claimed_at"

            rows = await conn.fetch(
                f"""
                SELECT
                    badge_key,
                    claimed_at,
                    {"unlocked_at" if supports_unlocked_at else "NULL::timestamptz AS unlocked_at"},
                    {"unlock_notified_at" if supports_unlock_notified_at else "NULL::timestamptz AS unlock_notified_at"}
                FROM user_performance_badge_claims
                WHERE user_id = $1
                  AND badge_key = ANY($2::text[])
                ORDER BY {badge_state_order_expr} DESC NULLS LAST
                """,
                user_id,
                list(BADGE_KEYS),
            )

        return {
            row["badge_key"]: {
                "claimed_at": row["claimed_at"],
                "unlocked_at": row["unlocked_at"],
                "unlock_notified_at": row["unlock_notified_at"],
            }
            for row in rows
        }

    @staticmethod
    async def _sync_badge_unlocks(
        *,
        user_id: UUID,
        current_entry: UserDirectoryEntry,
        badges: List[dict],
    ) -> None:
        unlock_candidates = [
            badge
            for badge in badges
            if badge.get("claimable")
            and not badge.get("unlock_notified_at")
        ]
        if not unlock_candidates:
            return

        async with get_async_connection() as conn:
            if not await PerformanceCommandService._table_exists(conn, "user_performance_badge_claims"):
                return

            supports_unlocked_at = await PerformanceCommandService._column_exists(
                conn,
                "user_performance_badge_claims",
                "unlocked_at",
            )
            supports_unlock_notified_at = await PerformanceCommandService._column_exists(
                conn,
                "user_performance_badge_claims",
                "unlock_notified_at",
            )
            if not supports_unlocked_at or not supports_unlock_notified_at:
                return

            user_row = await conn.fetchrow(
                """
                SELECT username, first_name, last_name, email
                FROM users
                WHERE id = $1
                LIMIT 1
                """,
                user_id,
            )
            recipient_name = (
                _format_display_name(
                    user_row["username"] if user_row else current_entry.username,
                    user_row["first_name"] if user_row else None,
                    user_row["last_name"] if user_row else None,
                )
                if user_row
                else current_entry.display_name
            )
            recipient_email = ((user_row["email"] if user_row else "") or "").strip()

            for badge in unlock_candidates:
                badge_key = badge["key"]
                unlock_state = await conn.fetchrow(
                    f"""
                    INSERT INTO user_performance_badge_claims (user_id, badge_key, unlocked_at)
                    VALUES ($1, $2, NOW())
                    ON CONFLICT (user_id, badge_key)
                    DO UPDATE SET unlocked_at = COALESCE(user_performance_badge_claims.unlocked_at, EXCLUDED.unlocked_at)
                    RETURNING
                        claimed_at,
                        unlocked_at,
                        {"unlock_notified_at" if supports_unlock_notified_at else "NULL::timestamptz AS unlock_notified_at"}
                    """,
                    user_id,
                    badge_key,
                )
                if not unlock_state:
                    continue

                if unlock_state["unlock_notified_at"] is not None:
                    continue

                sent_any = False
                title = f"Badge Unlocked | {badge['name']}"
                message = (
                    f"You unlocked {badge['name']}. Open Performance and claim it from Badge Forge."
                )

                try:
                    NotificationDBService.create_notification(
                        title=title,
                        message=message,
                        user_id=user_id,
                        related_entity="performance_badge",
                    )
                    sent_any = True
                except Exception as exc:
                    log.warning(
                        "Failed to create badge unlock notification for user %s badge %s: %s",
                        user_id,
                        badge_key,
                        exc,
                    )

                if recipient_email:
                    subject, text_body, html_body = performance_badge_unlocked_template(
                        recipient_name=recipient_name,
                        badge_name=badge["name"],
                        badge_description=badge["description"],
                        badge_target=badge["target"],
                        badge_hint=badge["hint"],
                    )
                    send_email_fire_and_forget([recipient_email], subject, text_body, html_body)
                    sent_any = True

                if sent_any and supports_unlock_notified_at:
                    await conn.execute(
                        """
                        UPDATE user_performance_badge_claims
                        SET unlock_notified_at = COALESCE(unlock_notified_at, NOW())
                        WHERE user_id = $1
                          AND badge_key = $2
                        """,
                        user_id,
                        badge_key,
                    )

    @staticmethod
    async def claim_badge(current_user: dict, badge_key: str) -> dict:
        normalized_key = (badge_key or "").strip().lower()
        if normalized_key not in BADGE_KEYS:
            raise ValueError("Unknown performance badge")

        current_user_id = UUID(str(current_user["id"]))

        async with get_async_connection() as conn:
            if not await PerformanceCommandService._table_exists(conn, "user_performance_badge_claims"):
                raise RuntimeError("Badge claiming requires the latest database migration.")

        command = await PerformanceCommandService.get_performance_command(
            current_user=current_user,
            focus_window="monthly",
        )
        badge_lookup = {badge["key"]: badge for badge in command["badges"]}
        current_badge = badge_lookup.get(normalized_key)

        if current_badge is None:
            raise ValueError("Unknown performance badge")

        if current_badge["claimed"]:
            return {
                "badge": current_badge,
                "claimed_badge_count": command["profile"]["badge_count"],
            }

        if not current_badge["claimable"]:
            raise ValueError("This badge is not ready to claim yet.")

        async with get_async_connection() as conn:
            supports_unlocked_at = await PerformanceCommandService._column_exists(
                conn,
                "user_performance_badge_claims",
                "unlocked_at",
            )
            if supports_unlocked_at:
                await conn.execute(
                    """
                    INSERT INTO user_performance_badge_claims (user_id, badge_key, unlocked_at, claimed_at)
                    VALUES ($1, $2, NOW(), NOW())
                    ON CONFLICT (user_id, badge_key)
                    DO UPDATE SET
                        unlocked_at = COALESCE(user_performance_badge_claims.unlocked_at, EXCLUDED.unlocked_at),
                        claimed_at = COALESCE(user_performance_badge_claims.claimed_at, EXCLUDED.claimed_at)
                    """,
                    current_user_id,
                    normalized_key,
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO user_performance_badge_claims (user_id, badge_key)
                    VALUES ($1, $2)
                    ON CONFLICT (user_id, badge_key) DO NOTHING
                    """,
                    current_user_id,
                    normalized_key,
                )

        refreshed_command = await PerformanceCommandService.get_performance_command(
            current_user=current_user,
            focus_window="monthly",
        )
        refreshed_badge = next(
            (badge for badge in refreshed_command["badges"] if badge["key"] == normalized_key),
            None,
        )

        if refreshed_badge is None:
            raise RuntimeError("Claimed badge could not be reloaded.")

        return {
            "badge": refreshed_badge,
            "claimed_badge_count": refreshed_command["profile"]["badge_count"],
        }

    @staticmethod
    async def _load_directory(current_user: dict) -> Dict[UUID, UserDirectoryEntry]:
        async with get_async_connection() as conn:
            params: List[object] = []
            section_filter = ""
            section_id = current_user.get("section_id")
            if section_id:
                section_filter = "AND u.section_id = $1"
                params.append(UUID(str(section_id)))
            elif not is_admin(current_user):
                section_filter = "AND u.id = $1"
                params.append(UUID(str(current_user["id"])))

            rows = await conn.fetch(
                f"""
                SELECT
                    u.id,
                    u.username,
                    u.first_name,
                    u.last_name,
                    s.section_name
                FROM users u
                LEFT JOIN sections s ON s.id = u.section_id
                WHERE u.is_active = TRUE
                {section_filter}
                ORDER BY COALESCE(NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.last_name)), ''), u.username)
                """,
                *params,
            )

        directory = {
            row["id"]: UserDirectoryEntry(
                user_id=row["id"],
                username=row["username"],
                display_name=_format_display_name(row["username"], row["first_name"], row["last_name"]),
                section_name=row["section_name"],
                is_current_user=str(row["id"]) == str(current_user["id"]),
            )
            for row in rows
        }

        current_user_uuid = UUID(str(current_user["id"]))
        if current_user_uuid not in directory:
            directory[current_user_uuid] = UserDirectoryEntry(
                user_id=current_user_uuid,
                username=current_user.get("username") or "you",
                display_name=_format_display_name(
                    current_user.get("username"),
                    current_user.get("first_name"),
                    current_user.get("last_name"),
                ),
                section_name=None,
                is_current_user=True,
            )

        return directory

    @staticmethod
    async def _load_daily_maps(
        user_ids: List[UUID],
        start_date: date,
        end_date: date,
    ) -> Dict[UUID, Dict[date, DailyMetrics]]:
        daily_maps: Dict[UUID, Dict[date, DailyMetrics]] = defaultdict(lambda: defaultdict(DailyMetrics))

        if not user_ids:
            return daily_maps

        business_timezone = settings.TRUSTLINK_SCHEDULE_TIMEZONE

        async with get_async_connection() as conn:
            item_started_by_supported = await PerformanceCommandService._column_exists(
                conn,
                "checklist_instance_items",
                "started_by",
            )
            subitem_started_by_supported = await PerformanceCommandService._column_exists(
                conn,
                "checklist_instance_subitems",
                "started_by",
            )
            item_started_by_expr = (
                "COALESCE(cii.started_by, starter.user_id, cii.completed_by)"
                if item_started_by_supported
                else "COALESCE(starter.user_id, cii.completed_by)"
            )
            subitem_started_by_expr = (
                "COALESCE(cis.started_by, cis.completed_by)"
                if subitem_started_by_supported
                else "cis.completed_by"
            )

            completion_rows = await conn.fetch(
                """
                WITH user_actions AS (
                    SELECT
                        cii.completed_by AS user_id,
                        ci.checklist_date AS activity_date,
                        ci.id AS instance_id,
                        ci.shift::text AS shift_name,
                        1 AS items_completed,
                        CASE WHEN cti.is_required = TRUE THEN 1 ELSE 0 END AS required_items_completed,
                        CASE WHEN COALESCE(cti.severity, 1) >= 4 THEN 1 ELSE 0 END AS critical_items_completed,
                        0 AS subitems_completed,
                        (
                            5
                            + (COALESCE(cti.severity, 1) * 3)
                            + CASE WHEN cti.is_required THEN 2 ELSE 0 END
                            + CASE WHEN COALESCE(cti.severity, 1) >= 4 THEN 3 ELSE 0 END
                        ) AS checklist_points,
                        0 AS subitem_points
                    FROM checklist_instance_items cii
                    JOIN checklist_template_items cti ON cti.id = cii.template_item_id
                    JOIN checklist_instances ci ON ci.id = cii.instance_id
                    WHERE
                        cii.status = 'COMPLETED'
                        AND cii.completed_by = ANY($1::uuid[])
                        AND ci.checklist_date BETWEEN $2 AND $3

                    UNION ALL

                    SELECT
                        cis.completed_by AS user_id,
                        ci.checklist_date AS activity_date,
                        ci.id AS instance_id,
                        ci.shift::text AS shift_name,
                        0 AS items_completed,
                        0 AS required_items_completed,
                        0 AS critical_items_completed,
                        1 AS subitems_completed,
                        0 AS checklist_points,
                        (
                            2
                            + COALESCE(cis.severity, 1)
                            + CASE WHEN cis.is_required THEN 1 ELSE 0 END
                            + CASE WHEN COALESCE(cis.severity, 1) >= 4 THEN 2 ELSE 0 END
                        ) AS subitem_points
                    FROM checklist_instance_subitems cis
                    JOIN checklist_instance_items cii ON cii.id = cis.instance_item_id
                    JOIN checklist_instances ci ON ci.id = cii.instance_id
                    WHERE
                        cis.status = 'COMPLETED'
                        AND cis.completed_by = ANY($1::uuid[])
                        AND ci.checklist_date BETWEEN $2 AND $3
                ),
                relevant_instances AS (
                    SELECT DISTINCT instance_id
                    FROM user_actions
                ),
                completion_contributors AS (
                    SELECT
                        contributors.instance_id,
                        COUNT(DISTINCT contributors.user_id) AS contributor_count
                    FROM (
                        SELECT cii.instance_id, cii.completed_by AS user_id
                        FROM checklist_instance_items cii
                        JOIN relevant_instances ri ON ri.instance_id = cii.instance_id
                        WHERE cii.status = 'COMPLETED' AND cii.completed_by IS NOT NULL

                        UNION ALL

                        SELECT cii.instance_id, cis.completed_by AS user_id
                        FROM checklist_instance_subitems cis
                        JOIN checklist_instance_items cii ON cii.id = cis.instance_item_id
                        JOIN relevant_instances ri ON ri.instance_id = cii.instance_id
                        WHERE cis.status = 'COMPLETED' AND cis.completed_by IS NOT NULL
                    ) contributors
                    GROUP BY contributors.instance_id
                )
                SELECT
                    ua.user_id,
                    ua.activity_date,
                    SUM(ua.items_completed) AS items_completed,
                    SUM(ua.required_items_completed) AS required_items_completed,
                    SUM(ua.critical_items_completed) AS critical_items_completed,
                    SUM(ua.subitems_completed) AS subitems_completed,
                    SUM(ua.checklist_points) AS checklist_points,
                    SUM(ua.subitem_points) AS subitem_points,
                    COUNT(*) FILTER (WHERE ua.shift_name = 'MORNING') AS morning_shift_actions,
                    COUNT(*) FILTER (WHERE ua.shift_name = 'AFTERNOON') AS afternoon_shift_actions,
                    COUNT(*) FILTER (WHERE ua.shift_name = 'NIGHT') AS night_shift_actions,
                    COUNT(DISTINCT ua.instance_id) FILTER (WHERE ua.shift_name = 'MORNING') AS morning_shift_runs,
                    COUNT(DISTINCT ua.instance_id) FILTER (WHERE ua.shift_name = 'AFTERNOON') AS afternoon_shift_runs,
                    COUNT(DISTINCT ua.instance_id) FILTER (WHERE ua.shift_name = 'NIGHT') AS night_shift_runs,
                    COUNT(*) FILTER (WHERE COALESCE(cc.contributor_count, 0) > 1) AS collaborative_actions,
                    COUNT(DISTINCT ua.instance_id) FILTER (WHERE COALESCE(cc.contributor_count, 0) > 1) AS collaborative_instances_contributed
                FROM user_actions ua
                LEFT JOIN completion_contributors cc ON cc.instance_id = ua.instance_id
                GROUP BY ua.user_id, ua.activity_date
                """,
                user_ids,
                start_date,
                end_date,
            )

            participation_rows = await conn.fetch(
                """
                WITH instance_state AS (
                    SELECT
                        ci.id,
                        ci.checklist_date,
                        ci.status,
                        CASE
                            WHEN ci.closed_at IS NOT NULL AND ci.closed_at <= ci.shift_end THEN TRUE
                            ELSE FALSE
                        END AS completed_on_time,
                        COUNT(*) FILTER (WHERE cti.is_required = TRUE) AS required_total,
                        COUNT(*) FILTER (WHERE cti.is_required = TRUE AND cii.status = 'COMPLETED') AS required_completed,
                        COUNT(*) FILTER (WHERE cii.status IN ('FAILED', 'SKIPPED')) AS exception_items
                    FROM checklist_instances ci
                    LEFT JOIN checklist_instance_items cii ON cii.instance_id = ci.id
                    LEFT JOIN checklist_template_items cti ON cti.id = cii.template_item_id
                    WHERE ci.checklist_date BETWEEN $2 AND $3
                    GROUP BY ci.id
                ),
                participant_counts AS (
                    SELECT instance_id, COUNT(*) AS participant_count
                    FROM checklist_participants
                    GROUP BY instance_id
                )
                SELECT
                    cp.user_id,
                    ins.checklist_date AS activity_date,
                    COUNT(*) AS checklists_joined,
                    COUNT(*) FILTER (WHERE ins.status IN ('COMPLETED', 'COMPLETED_WITH_EXCEPTIONS')) AS completed_checklists_joined,
                    COUNT(*) FILTER (WHERE ins.status IN ('COMPLETED', 'COMPLETED_WITH_EXCEPTIONS') AND ins.completed_on_time = TRUE) AS on_time_checklists,
                    COUNT(*) FILTER (
                        WHERE
                            ins.status = 'COMPLETED'
                            AND ins.required_total = ins.required_completed
                            AND ins.exception_items = 0
                    ) AS clean_checklists,
                    COUNT(*) FILTER (WHERE COALESCE(pc.participant_count, 0) > 1) AS collaborative_checklists
                FROM checklist_participants cp
                JOIN instance_state ins ON ins.id = cp.instance_id
                LEFT JOIN participant_counts pc ON pc.instance_id = cp.instance_id
                WHERE cp.user_id = ANY($1::uuid[])
                GROUP BY cp.user_id, ins.checklist_date
                """,
                user_ids,
                start_date,
                end_date,
            )

            task_rows = await conn.fetch(
                f"""
                SELECT
                    t.assigned_to_id AS user_id,
                    timezone('{business_timezone}', t.completed_at)::date AS activity_date,
                    COUNT(*) AS tasks_completed,
                    COUNT(*) FILTER (WHERE t.due_date IS NULL OR t.completed_at <= t.due_date) AS tasks_completed_on_time,
                    COUNT(*) FILTER (WHERE t.due_date IS NOT NULL AND t.completed_at > t.due_date) AS late_tasks_completed,
                    COUNT(*) FILTER (WHERE t.priority = 'HIGH') AS high_tasks_completed,
                    COUNT(*) FILTER (WHERE t.priority = 'CRITICAL') AS critical_tasks_completed,
                    COUNT(*) FILTER (WHERE t.task_type IN ('TEAM', 'DEPARTMENT')) AS collaborative_tasks_completed,
                    SUM(
                        CASE t.priority
                            WHEN 'LOW' THEN 10
                            WHEN 'MEDIUM' THEN 16
                            WHEN 'HIGH' THEN 26
                            WHEN 'CRITICAL' THEN 38
                            ELSE 10
                        END
                        + CASE
                            WHEN t.due_date IS NULL OR t.completed_at <= t.due_date THEN
                                CASE t.priority
                                    WHEN 'LOW' THEN 3
                                    WHEN 'MEDIUM' THEN 5
                                    WHEN 'HIGH' THEN 8
                                    WHEN 'CRITICAL' THEN 12
                                    ELSE 0
                                END
                            ELSE 0
                        END
                        + CASE WHEN t.task_type IN ('TEAM', 'DEPARTMENT') THEN 5 ELSE 0 END
                    ) AS task_points,
                    SUM(
                        CASE
                            WHEN t.due_date IS NULL OR t.completed_at <= t.due_date THEN 0
                            ELSE
                                CASE t.priority
                                    WHEN 'LOW' THEN 2
                                    WHEN 'MEDIUM' THEN 4
                                    WHEN 'HIGH' THEN 6
                                    WHEN 'CRITICAL' THEN 9
                                    ELSE 2
                                END
                                + CASE
                                    WHEN t.completed_at <= t.due_date + INTERVAL '4 hours' THEN 1
                                    WHEN t.completed_at <= t.due_date + INTERVAL '1 day' THEN 3
                                    WHEN t.completed_at <= t.due_date + INTERVAL '3 days' THEN 5
                                    ELSE 8
                                END
                        END
                    ) AS late_task_penalty_points
                FROM tasks t
                WHERE
                    t.deleted_at IS NULL
                    AND t.assigned_to_id = ANY($1::uuid[])
                    AND t.status = 'COMPLETED'
                    AND timezone('{business_timezone}', t.completed_at)::date BETWEEN $2 AND $3
                GROUP BY t.assigned_to_id, timezone('{business_timezone}', t.completed_at)::date
                """,
                user_ids,
                start_date,
                end_date,
            )

            timed_start_rows = await conn.fetch(
                f"""
                SELECT
                    starts.user_id,
                    starts.activity_date,
                    COUNT(*) AS timed_actions_started,
                    COUNT(*) FILTER (WHERE starts.started_on_time = TRUE) AS timed_actions_started_on_time,
                    COUNT(*) FILTER (WHERE starts.started_on_time = FALSE) AS timed_actions_started_late,
                    SUM(starts.late_start_penalty_points) AS late_start_penalty_points
                FROM (
                    SELECT
                        {item_started_by_expr} AS user_id,
                        ci.checklist_date AS activity_date,
                        CASE
                            WHEN cii.started_at <= cii.scheduled_at + INTERVAL '{TIMED_START_GRACE_MINUTES} minutes' THEN TRUE
                            ELSE FALSE
                        END AS started_on_time,
                        CASE
                            WHEN cii.started_at <= cii.scheduled_at + INTERVAL '{TIMED_START_GRACE_MINUTES} minutes' THEN 0
                            WHEN cii.started_at <= cii.scheduled_at + INTERVAL '15 minutes' THEN 2
                            WHEN cii.started_at <= cii.scheduled_at + INTERVAL '1 hour' THEN 4
                            ELSE 7
                        END AS late_start_penalty_points
                    FROM checklist_instance_items cii
                    JOIN checklist_instances ci ON ci.id = cii.instance_id
                    LEFT JOIN (
                        SELECT DISTINCT ON (activity.instance_item_id)
                            activity.instance_item_id,
                            activity.user_id
                        FROM checklist_item_activity activity
                        WHERE activity.action = 'STARTED'
                        ORDER BY activity.instance_item_id, activity.created_at ASC
                    ) starter ON starter.instance_item_id = cii.id
                    WHERE
                        {item_started_by_expr} = ANY($1::uuid[])
                        AND cii.started_at IS NOT NULL
                        AND cii.scheduled_at IS NOT NULL
                        AND ci.checklist_date BETWEEN $2 AND $3

                    UNION ALL

                    SELECT
                        {subitem_started_by_expr} AS user_id,
                        ci.checklist_date AS activity_date,
                        CASE
                            WHEN cis.started_at <= cis.scheduled_at + INTERVAL '{TIMED_START_GRACE_MINUTES} minutes' THEN TRUE
                            ELSE FALSE
                        END AS started_on_time,
                        CASE
                            WHEN cis.started_at <= cis.scheduled_at + INTERVAL '{TIMED_START_GRACE_MINUTES} minutes' THEN 0
                            WHEN cis.started_at <= cis.scheduled_at + INTERVAL '15 minutes' THEN 2
                            WHEN cis.started_at <= cis.scheduled_at + INTERVAL '1 hour' THEN 4
                            ELSE 7
                        END AS late_start_penalty_points
                    FROM checklist_instance_subitems cis
                    JOIN checklist_instance_items cii ON cii.id = cis.instance_item_id
                    JOIN checklist_instances ci ON ci.id = cii.instance_id
                    WHERE
                        {subitem_started_by_expr} = ANY($1::uuid[])
                        AND cis.started_at IS NOT NULL
                        AND cis.scheduled_at IS NOT NULL
                        AND ci.checklist_date BETWEEN $2 AND $3
                ) starts
                GROUP BY starts.user_id, starts.activity_date
                """,
                user_ids,
                start_date,
                end_date,
            )

            handover_rows = await conn.fetch(
                f"""
                SELECT
                    user_id,
                    activity_date,
                    SUM(handovers_created) AS handovers_created,
                    SUM(handovers_resolved) AS handovers_resolved
                FROM (
                    SELECT
                        hn.created_by AS user_id,
                        timezone('{business_timezone}', hn.created_at)::date AS activity_date,
                        COUNT(*) AS handovers_created,
                        0 AS handovers_resolved
                    FROM handover_notes hn
                    WHERE
                        hn.created_by = ANY($1::uuid[])
                        AND timezone('{business_timezone}', hn.created_at)::date BETWEEN $2 AND $3
                    GROUP BY hn.created_by, timezone('{business_timezone}', hn.created_at)::date

                    UNION ALL

                    SELECT
                        hn.resolved_by AS user_id,
                        timezone('{business_timezone}', hn.resolved_at)::date AS activity_date,
                        0 AS handovers_created,
                        COUNT(*) AS handovers_resolved
                    FROM handover_notes hn
                    WHERE
                        hn.resolved_by = ANY($1::uuid[])
                        AND hn.resolved_at IS NOT NULL
                        AND timezone('{business_timezone}', hn.resolved_at)::date BETWEEN $2 AND $3
                    GROUP BY hn.resolved_by, timezone('{business_timezone}', hn.resolved_at)::date
                ) events
                GROUP BY user_id, activity_date
                """,
                user_ids,
                start_date,
                end_date,
            )

            shift_rows = await conn.fetch(
                """
                WITH shift_participation AS (
                    SELECT DISTINCT
                        cp.user_id,
                        ci.checklist_date,
                        ci.shift
                    FROM checklist_participants cp
                    JOIN checklist_instances ci ON ci.id = cp.instance_id
                    WHERE
                        cp.user_id = ANY($1::uuid[])
                        AND ci.checklist_date BETWEEN $2 AND $3
                )
                SELECT
                    ss.user_id,
                    ss.date AS activity_date,
                    COUNT(*) AS assigned_shifts,
                    COUNT(*) FILTER (WHERE sp.user_id IS NOT NULL) AS staffed_shifts
                FROM scheduled_shifts ss
                LEFT JOIN shifts sh ON sh.id = ss.shift_id
                LEFT JOIN shift_participation sp
                    ON sp.user_id = ss.user_id
                    AND sp.checklist_date = ss.date
                    AND UPPER(COALESCE(sh.name, '')) = sp.shift::text
                WHERE
                    ss.user_id = ANY($1::uuid[])
                    AND ss.date BETWEEN $2 AND $3
                GROUP BY ss.user_id, ss.date
                """,
                user_ids,
                start_date,
                end_date,
            )

        for row in completion_rows:
            daily_maps[row["user_id"]][row["activity_date"]].add_values(
                checklist_points=row["checklist_points"],
                items_completed=row["items_completed"],
                required_items_completed=row["required_items_completed"],
                critical_items_completed=row["critical_items_completed"],
                subitem_points=row["subitem_points"],
                subitems_completed=row["subitems_completed"],
                collaborative_actions=row["collaborative_actions"],
                collaborative_instances_contributed=row["collaborative_instances_contributed"],
                morning_shift_actions=row["morning_shift_actions"],
                afternoon_shift_actions=row["afternoon_shift_actions"],
                night_shift_actions=row["night_shift_actions"],
                morning_shift_runs=row["morning_shift_runs"],
                afternoon_shift_runs=row["afternoon_shift_runs"],
                night_shift_runs=row["night_shift_runs"],
            )

        for row in participation_rows:
            daily_maps[row["user_id"]][row["activity_date"]].add_values(
                checklists_joined=row["checklists_joined"],
                completed_checklists_joined=row["completed_checklists_joined"],
                on_time_checklists=row["on_time_checklists"],
                clean_checklists=row["clean_checklists"],
                collaborative_checklists=row["collaborative_checklists"],
            )

        for row in task_rows:
            daily_maps[row["user_id"]][row["activity_date"]].add_values(
                task_points=row["task_points"],
                tasks_completed=row["tasks_completed"],
                tasks_completed_on_time=row["tasks_completed_on_time"],
                late_tasks_completed=row["late_tasks_completed"],
                high_tasks_completed=row["high_tasks_completed"],
                critical_tasks_completed=row["critical_tasks_completed"],
                collaborative_tasks_completed=row["collaborative_tasks_completed"],
                late_task_penalty_points=row["late_task_penalty_points"],
            )

        for row in timed_start_rows:
            daily_maps[row["user_id"]][row["activity_date"]].add_values(
                timed_actions_started=row["timed_actions_started"],
                timed_actions_started_on_time=row["timed_actions_started_on_time"],
                timed_actions_started_late=row["timed_actions_started_late"],
                late_start_penalty_points=row["late_start_penalty_points"],
            )

        for row in handover_rows:
            daily_maps[row["user_id"]][row["activity_date"]].add_values(
                handovers_created=row["handovers_created"],
                handovers_resolved=row["handovers_resolved"],
            )

        for row in shift_rows:
            daily_maps[row["user_id"]][row["activity_date"]].add_values(
                assigned_shifts=row["assigned_shifts"],
                staffed_shifts=row["staffed_shifts"],
            )

        return daily_maps

    @staticmethod
    async def _load_overdue_snapshot(user_ids: List[UUID]) -> Dict[UUID, int]:
        if not user_ids:
            return {}

        async with get_async_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    assigned_to_id AS user_id,
                    COUNT(*) AS overdue_open_tasks
                FROM tasks
                WHERE
                    deleted_at IS NULL
                    AND assigned_to_id = ANY($1::uuid[])
                    AND status IN ('ACTIVE', 'IN_PROGRESS', 'ON_HOLD')
                    AND due_date IS NOT NULL
                    AND due_date < NOW()
                GROUP BY assigned_to_id
                """,
                user_ids,
            )

        return {row["user_id"]: int(row["overdue_open_tasks"]) for row in rows}

    @staticmethod
    def _build_window_snapshots(
        *,
        window_key: str,
        start_date: date,
        end_date: date,
        directory: Dict[UUID, UserDirectoryEntry],
        daily_maps: Dict[UUID, Dict[date, DailyMetrics]],
        overdue_snapshot: Dict[UUID, int],
    ) -> Dict[UUID, WindowSnapshot]:
        window_snapshots: Dict[UUID, WindowSnapshot] = {}
        elapsed_days = max((end_date - start_date).days + 1, 1)

        for user_id in directory.keys():
            aggregate = DailyMetrics()
            active_dates: List[date] = []

            for activity_date, metrics in daily_maps.get(user_id, {}).items():
                if activity_date < start_date or activity_date > end_date:
                    continue
                aggregate.merge(metrics)
                if metrics.has_activity():
                    active_dates.append(activity_date)

            current_streak, longest_streak = _compute_live_streak(active_dates)
            contribution_days = len(set(active_dates))
            score = _score_aggregate(
                aggregate=aggregate,
                elapsed_days=elapsed_days,
                contribution_days=contribution_days,
                current_streak=current_streak,
                overdue_open_tasks=overdue_snapshot.get(user_id, 0),
            )

            window_snapshots[user_id] = WindowSnapshot(
                key=window_key,
                label=WINDOW_LABELS[window_key],
                start_date=start_date,
                end_date=end_date,
                command_points=score["command_points"],
                operational_grade=score["operational_grade"],
                tier=_snapshot_tier(window_key, score["command_points"]),
                rank=0,
                total_users=len(directory),
                contribution_days=contribution_days,
                current_streak=current_streak,
                longest_streak=longest_streak,
                items_completed=aggregate.items_completed + aggregate.subitems_completed,
                critical_items_completed=aggregate.critical_items_completed,
                checklists_joined=aggregate.checklists_joined,
                clean_checklists=aggregate.clean_checklists,
                tasks_completed=aggregate.tasks_completed,
                tasks_completed_on_time=aggregate.tasks_completed_on_time,
                late_tasks_completed=aggregate.late_tasks_completed,
                critical_tasks_completed=aggregate.critical_tasks_completed,
                high_tasks_completed=aggregate.high_tasks_completed,
                collaborative_tasks_completed=aggregate.collaborative_tasks_completed,
                collaborative_actions=aggregate.collaborative_actions,
                collaborative_instances_contributed=aggregate.collaborative_instances_contributed,
                morning_shift_actions=aggregate.morning_shift_actions,
                afternoon_shift_actions=aggregate.afternoon_shift_actions,
                night_shift_actions=aggregate.night_shift_actions,
                morning_shift_runs=aggregate.morning_shift_runs,
                afternoon_shift_runs=aggregate.afternoon_shift_runs,
                night_shift_runs=aggregate.night_shift_runs,
                timed_actions_started=aggregate.timed_actions_started,
                timed_actions_started_on_time=aggregate.timed_actions_started_on_time,
                timed_actions_started_late=aggregate.timed_actions_started_late,
                late_start_penalty_points=aggregate.late_start_penalty_points,
                late_task_penalty_points=aggregate.late_task_penalty_points,
                handovers_created=aggregate.handovers_created,
                handovers_resolved=aggregate.handovers_resolved,
                overdue_open_tasks=overdue_snapshot.get(user_id, 0),
                task_on_time_rate=score["task_on_time_rate"],
                checklist_quality_rate=score["checklist_quality_rate"],
                shift_adherence_rate=score["shift_adherence_rate"],
                timed_start_rate=score["timed_start_rate"],
                consistency_rate=score["consistency_rate"],
                summary=_build_summary(
                    task_on_time_rate=score["task_on_time_rate"],
                    checklist_quality_rate=score["checklist_quality_rate"],
                    shift_adherence_rate=score["shift_adherence_rate"],
                    timed_start_rate=score["timed_start_rate"],
                    overdue_open_tasks=overdue_snapshot.get(user_id, 0),
                    critical_actions=aggregate.critical_items_completed + aggregate.critical_tasks_completed,
                    lateness_events=score["late_events"],
                ),
                execution_points=score["execution_points"],
                reliability_points=score["reliability_points"],
                collaboration_points=score["collaboration_points"],
                quality_points=score["quality_points"],
            )

        ranked = sorted(
            window_snapshots.values(),
            key=lambda snapshot: (
                snapshot.command_points,
                snapshot.operational_grade,
                snapshot.clean_checklists,
                snapshot.tasks_completed_on_time,
            ),
            reverse=True,
        )

        for position, snapshot in enumerate(ranked, start=1):
            snapshot.rank = position
            snapshot.total_users = len(ranked)

        return window_snapshots

    @staticmethod
    def _snapshot_to_dict(snapshot: WindowSnapshot) -> dict:
        return {
            "key": snapshot.key,
            "label": snapshot.label,
            "start_date": snapshot.start_date,
            "end_date": snapshot.end_date,
            "command_points": snapshot.command_points,
            "operational_grade": snapshot.operational_grade,
            "tier": snapshot.tier,
            "rank": snapshot.rank,
            "total_users": snapshot.total_users,
            "contribution_days": snapshot.contribution_days,
            "current_streak": snapshot.current_streak,
            "longest_streak": snapshot.longest_streak,
            "items_completed": snapshot.items_completed,
            "critical_items_completed": snapshot.critical_items_completed,
            "checklists_joined": snapshot.checklists_joined,
            "clean_checklists": snapshot.clean_checklists,
            "tasks_completed": snapshot.tasks_completed,
            "tasks_completed_on_time": snapshot.tasks_completed_on_time,
            "critical_tasks_completed": snapshot.critical_tasks_completed,
            "high_tasks_completed": snapshot.high_tasks_completed,
            "collaborative_tasks_completed": snapshot.collaborative_tasks_completed,
            "handovers_created": snapshot.handovers_created,
            "handovers_resolved": snapshot.handovers_resolved,
            "overdue_open_tasks": snapshot.overdue_open_tasks,
            "task_on_time_rate": snapshot.task_on_time_rate,
            "checklist_quality_rate": snapshot.checklist_quality_rate,
            "shift_adherence_rate": snapshot.shift_adherence_rate,
            "consistency_rate": snapshot.consistency_rate,
            "summary": snapshot.summary,
            "breakdown": {
                "execution_points": snapshot.execution_points,
                "reliability_points": snapshot.reliability_points,
                "collaboration_points": snapshot.collaboration_points,
                "quality_points": snapshot.quality_points,
            },
        }

    @staticmethod
    def _build_leaderboard(
        snapshots: Dict[UUID, WindowSnapshot],
        directory: Dict[UUID, UserDirectoryEntry],
        current_user_id: UUID,
    ) -> List[dict]:
        ranked = sorted(
            snapshots.items(),
            key=lambda item: (item[1].rank, -item[1].command_points),
        )
        return [
            {
                "user_id": user_id,
                "username": directory[user_id].username,
                "display_name": directory[user_id].display_name,
                "section_name": directory[user_id].section_name,
                "command_points": snapshot.command_points,
                "operational_grade": snapshot.operational_grade,
                "rank": snapshot.rank,
                "current_streak": snapshot.current_streak,
                "tasks_completed": snapshot.tasks_completed,
                "clean_checklists": snapshot.clean_checklists,
                "tier": snapshot.tier,
                "is_current_user": user_id == current_user_id,
            }
            for user_id, snapshot in ranked[:8]
        ]

    @staticmethod
    def _build_trend(
        *,
        user_id: UUID,
        daily_maps: Dict[UUID, Dict[date, DailyMetrics]],
        window_key: str,
        start_date: date,
        end_date: date,
        snapshot: WindowSnapshot,
    ) -> List[dict]:
        buckets: List[tuple[str, date, date]] = []

        if window_key == "weekly":
            cursor = start_date
            while cursor <= end_date:
                buckets.append((cursor.strftime("%a"), cursor, cursor))
                cursor += timedelta(days=1)
        elif window_key == "monthly":
            cursor = start_date
            while cursor <= end_date:
                bucket_end = min(cursor + timedelta(days=6 - cursor.weekday()), end_date)
                buckets.append((f"Week of {cursor.strftime('%d %b')}", cursor, bucket_end))
                cursor = bucket_end + timedelta(days=1)
        else:
            cursor = start_date
            while cursor <= end_date:
                next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
                bucket_end = min(next_month - timedelta(days=1), end_date)
                buckets.append((cursor.strftime("%b"), cursor, bucket_end))
                cursor = bucket_end + timedelta(days=1)

        trend: List[dict] = []
        user_daily = daily_maps.get(user_id, {})

        for label, bucket_start, bucket_end in buckets:
            aggregate = DailyMetrics()
            active_days = 0
            for activity_date, metrics in user_daily.items():
                if bucket_start <= activity_date <= bucket_end:
                    aggregate.merge(metrics)
                    if metrics.has_activity():
                        active_days += 1

            score = _score_aggregate(
                aggregate=aggregate,
                elapsed_days=max((bucket_end - bucket_start).days + 1, 1),
                contribution_days=active_days,
                current_streak=min(active_days, snapshot.current_streak),
                overdue_open_tasks=0,
            )

            trend.append(
                {
                    "label": label,
                    "period_start": bucket_start,
                    "period_end": bucket_end,
                    "command_points": score["command_points"],
                    "operational_grade": score["operational_grade"],
                    "tasks_completed": aggregate.tasks_completed,
                    "checklist_items_completed": aggregate.items_completed + aggregate.subitems_completed,
                    "handovers_resolved": aggregate.handovers_resolved,
                }
            )

        if not trend:
            trend.append(
                {
                    "label": WINDOW_LABELS[window_key],
                    "period_start": start_date,
                    "period_end": end_date,
                    "command_points": snapshot.command_points,
                    "operational_grade": snapshot.operational_grade,
                    "tasks_completed": snapshot.tasks_completed,
                    "checklist_items_completed": snapshot.items_completed,
                    "handovers_resolved": snapshot.handovers_resolved,
                }
            )

        return trend

    @staticmethod
    def _build_badges(
        yearly_snapshot: WindowSnapshot,
        badge_states: Optional[Dict[str, Dict[str, Optional[datetime]]]] = None,
    ) -> List[dict]:
        critical_load = yearly_snapshot.critical_items_completed + yearly_snapshot.critical_tasks_completed
        relay_total = yearly_snapshot.handovers_created + yearly_snapshot.handovers_resolved
        late_events = yearly_snapshot.late_tasks_completed + yearly_snapshot.timed_actions_started_late
        badge_states = badge_states or {}
        progress_rows: List[dict] = []

        for badge in BADGE_DEFINITIONS:
            key = badge["key"]
            if key == "flow_keeper":
                progress = _weighted_average(
                    [
                        (min((yearly_snapshot.tasks_completed / 12) * 100, 100), 0.45),
                        (yearly_snapshot.task_on_time_rate if yearly_snapshot.tasks_completed else 0, 0.35),
                        (
                            100
                            if yearly_snapshot.late_tasks_completed <= 2
                            else max(0, 100 - ((yearly_snapshot.late_tasks_completed - 2) * 18)),
                            0.20,
                        ),
                    ]
                )
                unlocked = (
                    yearly_snapshot.tasks_completed >= 12
                    and yearly_snapshot.task_on_time_rate >= 85
                    and yearly_snapshot.late_tasks_completed <= 2
                )
                target = "12 completed tasks, 85% on-time delivery, and no more than 2 late closes"
            elif key == "checklist_vanguard":
                progress = _weighted_average(
                    [
                        (min((yearly_snapshot.items_completed / 90) * 100, 100), 0.55),
                        (min((yearly_snapshot.clean_checklists / 6) * 100, 100), 0.45),
                    ]
                )
                unlocked = yearly_snapshot.items_completed >= 90 and yearly_snapshot.clean_checklists >= 6
                target = "90 checklist actions and 6 clean runs"
            elif key == "critical_shield":
                progress = min((critical_load / 18) * 100, 100)
                unlocked = critical_load >= 18
                target = "18 critical actions handled"
            elif key == "relay_architect":
                progress = _weighted_average(
                    [
                        (min((relay_total / 10) * 100, 100), 0.6),
                        (min((yearly_snapshot.handovers_resolved / 4) * 100, 100), 0.4),
                    ]
                )
                unlocked = relay_total >= 10 and yearly_snapshot.handovers_resolved >= 4
                target = "10 handovers touched with 4 resolved"
            elif key == "tempo_builder":
                progress = _weighted_average(
                    [
                        (min((yearly_snapshot.contribution_days / 45) * 100, 100), 0.65),
                        (min((yearly_snapshot.longest_streak / 7) * 100, 100), 0.35),
                    ]
                )
                unlocked = yearly_snapshot.contribution_days >= 45 and yearly_snapshot.longest_streak >= 7
                target = "45 contribution days and a 7-day streak"
            elif key == "night_owl":
                progress = _weighted_average(
                    [
                        (min((yearly_snapshot.night_shift_runs / 6) * 100, 100), 0.45),
                        (min((yearly_snapshot.night_shift_actions / 36) * 100, 100), 0.55),
                    ]
                )
                unlocked = yearly_snapshot.night_shift_runs >= 6 and yearly_snapshot.night_shift_actions >= 36
                target = "36 night-shift actions across 6 runs"
            elif key == "first_light":
                progress = _weighted_average(
                    [
                        (min((yearly_snapshot.morning_shift_runs / 6) * 100, 100), 0.45),
                        (min((yearly_snapshot.morning_shift_actions / 36) * 100, 100), 0.55),
                    ]
                )
                unlocked = yearly_snapshot.morning_shift_runs >= 6 and yearly_snapshot.morning_shift_actions >= 36
                target = "36 morning-shift actions across 6 runs"
            elif key == "swing_captain":
                progress = _weighted_average(
                    [
                        (min((yearly_snapshot.afternoon_shift_runs / 6) * 100, 100), 0.45),
                        (min((yearly_snapshot.afternoon_shift_actions / 36) * 100, 100), 0.55),
                    ]
                )
                unlocked = (
                    yearly_snapshot.afternoon_shift_runs >= 6
                    and yearly_snapshot.afternoon_shift_actions >= 36
                )
                target = "36 afternoon-shift actions across 6 runs"
            elif key == "sync_catalyst":
                progress = _weighted_average(
                    [
                        (min((yearly_snapshot.collaborative_instances_contributed / 6) * 100, 100), 0.45),
                        (min((yearly_snapshot.collaborative_actions / 24) * 100, 100), 0.55),
                    ]
                )
                unlocked = (
                    yearly_snapshot.collaborative_instances_contributed >= 6
                    and yearly_snapshot.collaborative_actions >= 24
                )
                target = "24 shared checklist actions across 6 collaborative runs"
            elif key == "clockwork":
                progress = _weighted_average(
                    [
                        (min((yearly_snapshot.timed_actions_started_on_time / 18) * 100, 100), 0.55),
                        (
                            yearly_snapshot.timed_start_rate if yearly_snapshot.timed_actions_started else 0,
                            0.45,
                        ),
                    ]
                )
                unlocked = (
                    yearly_snapshot.timed_actions_started_on_time >= 18
                    and yearly_snapshot.timed_start_rate >= 85
                )
                target = "18 on-time timed starts and 85% start discipline"
            else:
                progress = _weighted_average(
                    [
                        (min((yearly_snapshot.command_points / 2800) * 100, 100), 0.40),
                        (yearly_snapshot.operational_grade, 0.25),
                        (
                            100
                            if yearly_snapshot.overdue_open_tasks <= 2
                            else max(0, 100 - (yearly_snapshot.overdue_open_tasks * 20)),
                            0.15,
                        ),
                        (
                            100 if late_events <= 3 else max(0, 100 - ((late_events - 3) * 12)),
                            0.20,
                        ),
                    ]
                )
                unlocked = (
                    yearly_snapshot.command_points >= 2800
                    and yearly_snapshot.operational_grade >= 88
                    and yearly_snapshot.overdue_open_tasks <= 2
                    and late_events <= 3
                    and yearly_snapshot.tasks_completed >= 15
                    and yearly_snapshot.clean_checklists >= 6
                )
                target = "2,800 command points, 88 grade, low overdue load, and disciplined lateness control"

            badge_state = badge_states.get(badge["key"], {})
            claimed_at = badge_state.get("claimed_at")
            unlocked_at = badge_state.get("unlocked_at") or claimed_at
            unlock_notified_at = badge_state.get("unlock_notified_at")
            claimed = claimed_at is not None
            earned = unlocked or unlocked_at is not None or claimed
            claimable = earned and not claimed
            display_progress = 100.0 if earned else round(min(progress, 100), 1)

            progress_rows.append(
                {
                    "key": badge["key"],
                    "name": badge["name"],
                    "icon": badge["icon"],
                    "theme": badge["theme"],
                    "description": badge["description"],
                    "hint": badge["hint"],
                    "earned": earned,
                    "claimed": claimed,
                    "claimable": claimable,
                    "progress": display_progress,
                    "target": target,
                    "unlocked_by_metrics": unlocked,
                    "unlocked_at": unlocked_at,
                    "unlock_notified_at": unlock_notified_at,
                    "claimed_at": claimed_at,
                }
            )

        return progress_rows

    @staticmethod
    async def _load_recent_events(
        *,
        user_id: UUID,
        start_date: date,
        end_date: date,
    ) -> List[dict]:
        business_timezone = settings.TRUSTLINK_SCHEDULE_TIMEZONE

        async with get_async_connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT *
                FROM (
                    SELECT
                        CONCAT('checklist-', cii.id::text) AS id,
                        'CHECKLIST_ITEM' AS event_type,
                        cti.title AS title,
                        CONCAT(pi.shift, ' shift on ', pi.checklist_date::text) AS detail,
                        cii.completed_at AS occurred_at,
                        (
                            5
                            + (COALESCE(cti.severity, 1) * 3)
                            + CASE WHEN cti.is_required THEN 2 ELSE 0 END
                            + CASE WHEN COALESCE(cti.severity, 1) >= 4 THEN 3 ELSE 0 END
                        ) AS points
                    FROM checklist_instance_items cii
                    JOIN checklist_template_items cti ON cti.id = cii.template_item_id
                    JOIN checklist_instances pi ON pi.id = cii.instance_id
                    WHERE
                        cii.completed_by = $1
                        AND cii.status = 'COMPLETED'
                        AND pi.checklist_date BETWEEN $2 AND $3

                    UNION ALL

                    SELECT
                        CONCAT('task-', t.id::text) AS id,
                        'TASK' AS event_type,
                        t.title AS title,
                        CONCAT(t.priority, ' priority ', LOWER(t.task_type::text), ' task') AS detail,
                        t.completed_at AS occurred_at,
                        (
                            CASE t.priority
                                WHEN 'LOW' THEN 10
                                WHEN 'MEDIUM' THEN 16
                                WHEN 'HIGH' THEN 26
                                WHEN 'CRITICAL' THEN 38
                                ELSE 10
                            END
                            + CASE
                                WHEN t.due_date IS NULL OR t.completed_at <= t.due_date THEN
                                    CASE t.priority
                                        WHEN 'LOW' THEN 3
                                        WHEN 'MEDIUM' THEN 5
                                        WHEN 'HIGH' THEN 8
                                        WHEN 'CRITICAL' THEN 12
                                        ELSE 0
                                    END
                                ELSE 0
                            END
                            + CASE WHEN t.task_type IN ('TEAM', 'DEPARTMENT') THEN 5 ELSE 0 END
                        ) AS points
                    FROM tasks t
                    WHERE
                        t.deleted_at IS NULL
                        AND t.assigned_to_id = $1
                        AND t.status = 'COMPLETED'
                        AND timezone('{business_timezone}', t.completed_at)::date BETWEEN $2 AND $3

                    UNION ALL

                    SELECT
                        CONCAT('handover-created-', hn.id::text) AS id,
                        'HANDOVER_CREATED' AS event_type,
                        'Logged handover note' AS title,
                        LEFT(hn.content, 120) AS detail,
                        hn.created_at AS occurred_at,
                        6 AS points
                    FROM handover_notes hn
                    WHERE
                        hn.created_by = $1
                        AND timezone('{business_timezone}', hn.created_at)::date BETWEEN $2 AND $3

                    UNION ALL

                    SELECT
                        CONCAT('handover-resolved-', hn.id::text) AS id,
                        'HANDOVER_RESOLVED' AS event_type,
                        'Resolved handover note' AS title,
                        COALESCE(NULLIF(LEFT(hn.resolution_notes, 120), ''), 'Handover closed cleanly') AS detail,
                        hn.resolved_at AS occurred_at,
                        12 AS points
                    FROM handover_notes hn
                    WHERE
                        hn.resolved_by = $1
                        AND hn.resolved_at IS NOT NULL
                        AND timezone('{business_timezone}', hn.resolved_at)::date BETWEEN $2 AND $3
                ) recent_events
                ORDER BY occurred_at DESC
                LIMIT 8
                """,
                user_id,
                start_date,
                end_date,
            )

        return [
            {
                "id": row["id"],
                "event_type": row["event_type"],
                "title": row["title"],
                "detail": row["detail"],
                "occurred_at": row["occurred_at"],
                "points": int(row["points"]),
            }
            for row in rows
        ]
