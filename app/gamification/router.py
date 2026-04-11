# app/gamification/router.py
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from uuid import UUID
from datetime import date, timedelta

from app.auth.service import get_current_user
from app.gamification.schemas import (
    Achievement,
    GamificationScore,
    LeaderboardEntry,
    PerformanceBadgeClaimResponse,
    PerformanceCommandResponse,
    PerformanceWindow,
    UserStreak,
)
from app.gamification.service import GamificationService
from app.gamification.performance_service import PerformanceCommandService
from app.core.logging import get_logger

log = get_logger("gamification-router")

router = APIRouter(prefix="/gamification", tags=["Gamification"])


@router.get("/performance/command", response_model=PerformanceCommandResponse)
async def get_performance_command(
    focus_window: PerformanceWindow = Query(PerformanceWindow.MONTHLY),
    current_user: dict = Depends(get_current_user)
):
    """Get the full performance command deck for the current user."""
    try:
        return await PerformanceCommandService.get_performance_command(
            current_user=current_user,
            focus_window=focus_window.value,
        )
    except Exception as e:
        log.error(f"Error getting performance command deck: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/performance/badges/{badge_key}/claim", response_model=PerformanceBadgeClaimResponse)
async def claim_performance_badge(
    badge_key: str,
    current_user: dict = Depends(get_current_user)
):
    """Claim a performance badge once it has been unlocked."""
    try:
        return await PerformanceCommandService.claim_badge(
            current_user=current_user,
            badge_key=badge_key,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        log.error(f"Error claiming performance badge {badge_key}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/scores", response_model=List[GamificationScore])
async def get_user_scores(
    start_date: Optional[date] = Query(None, description="Start date"),
    end_date: Optional[date] = Query(None, description="End date"),
    limit: int = Query(100, ge=1, le=500),
    current_user: dict = Depends(get_current_user)
):
    """Get gamification scores for current user"""
    try:
        scores = await GamificationService.get_user_scores(
            user_id=current_user["id"],
            start_date=start_date,
            end_date=end_date,
            limit=limit
        )
        return scores
    except Exception as e:
        log.error(f"Error getting user scores: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/streak", response_model=UserStreak)
async def get_user_streak(
    current_user: dict = Depends(get_current_user)
):
    """Get current user's operational streak"""
    try:
        streak = await GamificationService.get_user_streak(current_user["id"])
        if not streak:
            # Return empty streak
            return UserStreak(
                user_id=current_user["id"],
                current_streak_days=0,
                longest_streak_days=0,
                perfect_shifts_count=0,
                total_points=0,
                shift_completion_rate=0.0,
                last_shift_date=None
            )
        return streak
    except Exception as e:
        log.error(f"Error getting user streak: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/leaderboard", response_model=List[LeaderboardEntry])
async def get_leaderboard(
    timeframe: str = Query("weekly", regex="^(daily|weekly|monthly|all_time)$"),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    """Get gamification leaderboard"""
    try:
        leaderboard = await GamificationService.get_leaderboard(
            timeframe,
            limit,
            current_user=current_user,
        )
        
        return [
            LeaderboardEntry(
                user_id=entry['user_id'],
                username=entry['username'],
                total_points=entry['total_points'],
                current_streak=entry['current_streak'],
                perfect_shifts=entry['perfect_shifts'],
                rank=entry['rank'],
                avatar_url=f"https://ui-avatars.com/api/?name={entry['username']}&background=random"
            )
            for entry in leaderboard
        ]
    except Exception as e:
        log.error(f"Error getting leaderboard: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/achievements", response_model=List[Achievement])
async def get_user_achievements(
    current_user: dict = Depends(get_current_user)
):
    """Get achievements unlocked by current user"""
    try:
        achievements = await GamificationService.get_user_achievements(
            current_user["id"]
        )
        return achievements
    except Exception as e:
        log.error(f"Error getting achievements: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/dashboard")
async def get_gamification_dashboard(
    current_user: dict = Depends(get_current_user)
):
    """Get comprehensive gamification dashboard for current user"""
    try:
        # Get multiple data points in parallel
        streak_task = GamificationService.get_user_streak(current_user["id"])
        leaderboard_task = GamificationService.get_leaderboard(
            "weekly",
            10,
            current_user=current_user,
        )
        scores_task = GamificationService.get_user_scores(
            current_user["id"], 
            start_date=date.today() - timedelta(days=7),
            limit=20
        )
        achievements_task = GamificationService.get_user_achievements(
            current_user["id"]
        )
        
        streak, leaderboard, scores, achievements = await asyncio.gather(
            streak_task, leaderboard_task, scores_task, achievements_task
        )
        
        # Calculate rank
        user_rank = None
        for i, entry in enumerate(leaderboard):
            if str(entry['user_id']) == str(current_user["id"]):
                user_rank = i + 1
                break
        
        # Calculate weekly points
        weekly_points = sum(score['points'] for score in scores)
        
        # Get next milestones
        next_milestones = []
        if streak:
            # Next streak milestone
            current_streak = streak.get('current_streak_days', 0)
            streak_milestones = [3, 7, 14, 30]
            for milestone in streak_milestones:
                if current_streak < milestone:
                    next_milestones.append({
                        'type': 'STREAK',
                        'target': milestone,
                        'current': current_streak,
                        'remaining': milestone - current_streak,
                        'reward': milestone * 10
                    })
                    break
            
            # Next points milestone
            total_points = streak.get('total_points', 0)
            points_milestones = [100, 250, 500, 1000, 2500]
            for milestone in points_milestones:
                if total_points < milestone:
                    next_milestones.append({
                        'type': 'POINTS',
                        'target': milestone,
                        'current': total_points,
                        'remaining': milestone - total_points,
                        'reward': milestone // 10
                    })
                    break
        
        return {
            'user': {
                'username': current_user["username"],
                'rank': user_rank,
                'total_points': streak['total_points'] if streak else 0,
                'current_streak': streak['current_streak_days'] if streak else 0,
                'perfect_shifts': streak['perfect_shifts_count'] if streak else 0,
                'weekly_points': weekly_points
            },
            'leaderboard_preview': leaderboard[:5],
            'recent_scores': scores[:5],
            'achievements': achievements,
            'next_milestones': next_milestones
        }
    except Exception as e:
        log.error(f"Error getting gamification dashboard: {e}")
        raise HTTPException(status_code=500, detail=str(e))
