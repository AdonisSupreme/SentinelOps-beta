# app/gamification/service.py
import asyncio
from typing import List, Optional, Dict
from uuid import UUID
from datetime import datetime, date, timedelta

from app.db.database import get_async_connection
from app.core.logging import get_logger

log = get_logger("gamification-service")

class GamificationService:
    """Gamification and leaderboard service"""
    
    @staticmethod
    async def get_user_scores(
        user_id: UUID,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: int = 100
    ) -> List[dict]:
        """Get gamification scores for a user"""
        async with get_async_connection() as conn:
            query = """
                SELECT 
                    gs.*,
                    ci.checklist_date,
                    ci.shift,
                    u.username as awarded_by_username
                FROM gamification_scores gs
                JOIN checklist_instances ci ON gs.shift_instance_id = ci.id
                LEFT JOIN users u ON gs.awarded_by = u.id
                WHERE gs.user_id = $1
                {date_filter}
                ORDER BY gs.awarded_at DESC
                LIMIT $2
            """
            
            params = [user_id, limit]
            
            if start_date and end_date:
                query = query.format(date_filter="AND ci.checklist_date BETWEEN $2 AND $3")
                params = [user_id, start_date, end_date, limit]
            elif start_date:
                query = query.format(date_filter="AND ci.checklist_date >= $2")
                params = [user_id, start_date, limit]
            elif end_date:
                query = query.format(date_filter="AND ci.checklist_date <= $2")
                params = [user_id, end_date, limit]
            else:
                query = query.format(date_filter="")
            
            rows = await conn.fetch(query, *params)
            
            return [
                {
                    'id': row['id'],
                    'user_id': row['user_id'],
                    'shift_instance_id': row['shift_instance_id'],
                    'points': row['points'],
                    'reason': row['reason'],
                    'metadata': row['metadata'],
                    'awarded_by': row['awarded_by'],
                    'awarded_at': row['awarded_at'],
                    'checklist_date': row['checklist_date'],
                    'shift': row['shift'],
                    'awarded_by_username': row['awarded_by_username']
                }
                for row in rows
            ]
    
    @staticmethod
    async def get_user_streak(user_id: UUID) -> Optional[dict]:
        """Get user's operational streak"""
        async with get_async_connection() as conn:
            row = await conn.fetchrow("""
                SELECT * FROM user_operational_streaks 
                WHERE user_id = $1
            """, user_id)
            
            if row:
                return {
                    'id': row['id'],
                    'user_id': row['user_id'],
                    'current_streak_days': row['current_streak_days'],
                    'longest_streak_days': row['longest_streak_days'],
                    'perfect_shifts_count': row['perfect_shifts_count'],
                    'total_points': row['total_points'],
                    'shift_completion_rate': float(row['shift_completion_rate']) if row['shift_completion_rate'] else 0.0,
                    'last_shift_date': row['last_shift_date'],
                    'updated_at': row['updated_at']
                }
            return None
    
    @staticmethod
    async def get_leaderboard(
        timeframe: str = "weekly",  # daily, weekly, monthly, all_time
        limit: int = 50
    ) -> List[dict]:
        """Get gamification leaderboard"""
        async with get_async_connection() as conn:
            
            # Calculate date filter based on timeframe
            today = date.today()
            if timeframe == "daily":
                start_date = today
                end_date = today
            elif timeframe == "weekly":
                start_date = today - timedelta(days=today.weekday())
                end_date = start_date + timedelta(days=6)
            elif timeframe == "monthly":
                start_date = date(today.year, today.month, 1)
                if today.month == 12:
                    end_date = date(today.year + 1, 1, 1) - timedelta(days=1)
                else:
                    end_date = date(today.year, today.month + 1, 1) - timedelta(days=1)
            else:  # all_time
                start_date = None
                end_date = None
            
            # Base query with date join placeholder
            if start_date and end_date:
                query = """
                    WITH user_scores AS (
                        SELECT 
                            gs.user_id,
                            SUM(gs.points) as total_points,
                            COUNT(DISTINCT gs.shift_instance_id) as shift_count
                        FROM gamification_scores gs
                        JOIN checklist_instances ci ON gs.shift_instance_id = ci.id
                        WHERE ci.checklist_date BETWEEN $1 AND $2
                        GROUP BY gs.user_id
                    ),
                    user_streaks AS (
                        SELECT 
                            uos.user_id,
                            uos.current_streak_days,
                            uos.perfect_shifts_count
                        FROM user_operational_streaks uos
                    )
                    SELECT 
                        u.id as user_id,
                        u.username,
                        u.first_name,
                        u.last_name,
                        COALESCE(us.total_points, 0) as total_points,
                        COALESCE(ust.current_streak_days, 0) as current_streak,
                        COALESCE(ust.perfect_shifts_count, 0) as perfect_shifts,
                        COALESCE(us.shift_count, 0) as shift_count,
                        ROW_NUMBER() OVER (ORDER BY COALESCE(us.total_points, 0) DESC) as rank
                    FROM users u
                    LEFT JOIN user_scores us ON u.id = us.user_id
                    LEFT JOIN user_streaks ust ON u.id = ust.user_id
                    WHERE u.is_active = TRUE
                    ORDER BY total_points DESC, current_streak DESC
                    LIMIT $3
                """
                rows = await conn.fetch(query, start_date, end_date, limit)
            else:
                query = """
                    WITH user_scores AS (
                        SELECT 
                            gs.user_id,
                            SUM(gs.points) as total_points,
                            COUNT(DISTINCT gs.shift_instance_id) as shift_count
                        FROM gamification_scores gs
                        GROUP BY gs.user_id
                    ),
                    user_streaks AS (
                        SELECT 
                            uos.user_id,
                            uos.current_streak_days,
                            uos.perfect_shifts_count
                        FROM user_operational_streaks uos
                    )
                    SELECT 
                        u.id as user_id,
                        u.username,
                        u.first_name,
                        u.last_name,
                        COALESCE(us.total_points, 0) as total_points,
                        COALESCE(ust.current_streak_days, 0) as current_streak,
                        COALESCE(ust.perfect_shifts_count, 0) as perfect_shifts,
                        COALESCE(us.shift_count, 0) as shift_count,
                        ROW_NUMBER() OVER (ORDER BY COALESCE(us.total_points, 0) DESC) as rank
                    FROM users u
                    LEFT JOIN user_scores us ON u.id = us.user_id
                    LEFT JOIN user_streaks ust ON u.id = ust.user_id
                    WHERE u.is_active = TRUE
                    ORDER BY total_points DESC, current_streak DESC
                    LIMIT $1
                """
                rows = await conn.fetch(query, limit)
            
            return [
                {
                    'user_id': row['user_id'],
                    'username': row['username'],
                    'first_name': row['first_name'],
                    'last_name': row['last_name'],
                    'total_points': row['total_points'],
                    'current_streak': row['current_streak'],
                    'perfect_shifts': row['perfect_shifts'],
                    'shift_count': row['shift_count'],
                    'rank': row['rank'],
                    'avg_points_per_shift': round(row['total_points'] / row['shift_count'], 1) if row['shift_count'] > 0 else 0
                }
                for row in rows
            ]
    
    @staticmethod
    async def get_user_achievements(user_id: UUID) -> List[dict]:
        """Get achievements unlocked by user"""
        # This would query an achievements table
        # For now, return mock achievements based on user's stats
        streak = await GamificationService.get_user_streak(user_id)
        
        achievements = []
        
        if streak:
            # Perfect Shift Achievements
            if streak['perfect_shifts_count'] >= 1:
                achievements.append({
                    'name': 'First Perfect Shift',
                    'description': 'Complete a shift with no exceptions',
                    'icon': '⭐',
                    'unlocked_date': datetime.now() - timedelta(days=1)
                })
            
            if streak['perfect_shifts_count'] >= 5:
                achievements.append({
                    'name': 'Flawless Operator',
                    'description': 'Complete 5 perfect shifts',
                    'icon': '⭐⭐⭐',
                    'unlocked_date': datetime.now() - timedelta(days=3)
                })
            
            # Streak Achievements
            if streak['current_streak_days'] >= 3:
                achievements.append({
                    'name': 'Consistency Champion',
                    'description': 'Maintain a 3-day operational streak',
                    'icon': '🔥',
                    'unlocked_date': datetime.now() - timedelta(days=1)
                })
            
            if streak['longest_streak_days'] >= 7:
                achievements.append({
                    'name': 'Weekly Warrior',
                    'description': 'Maintain a 7-day operational streak',
                    'icon': '🏆',
                    'unlocked_date': datetime.now() - timedelta(days=1)
                })
            
            # Points Achievements
            if streak['total_points'] >= 100:
                achievements.append({
                    'name': 'Centurion',
                    'description': 'Earn 100+ gamification points',
                    'icon': '💯',
                    'unlocked_date': datetime.now() - timedelta(days=2)
                })
        
        return achievements
    
    @staticmethod
    async def award_points(
        user_id: UUID,
        shift_instance_id: UUID,
        points: int,
        reason: str,
        metadata: Optional[dict] = None,
        awarded_by: Optional[UUID] = None
    ) -> dict:
        """Award points to a user"""
        async with get_async_connection() as conn:
            row = await conn.fetchrow("""
                INSERT INTO gamification_scores 
                (user_id, shift_instance_id, points, reason, metadata, awarded_by)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING *
            """, user_id, shift_instance_id, points, reason, metadata, awarded_by)
            
            # Update user streak
            await GamificationService._update_streak(conn, user_id, 
                                                    shift_instance_id)
            
            # Check for achievement unlocks
            await GamificationService._check_achievements(conn, user_id)
            
            return {
                'id': row['id'],
                'user_id': row['user_id'],
                'shift_instance_id': row['shift_instance_id'],
                'points': row['points'],
                'reason': row['reason'],
                'metadata': row['metadata'],
                'awarded_by': row['awarded_by'],
                'awarded_at': row['awarded_at']
            }
    
    @staticmethod
    async def _update_streak(conn, user_id: UUID, shift_instance_id: UUID):
        """Update user's operational streak"""
        shift_date_row = await conn.fetchval("""
            SELECT checklist_date FROM checklist_instances 
            WHERE id = $1
        """, shift_instance_id)
        
        shift_date = shift_date_row
        
        existing = await conn.fetchrow("""
            SELECT * FROM user_operational_streaks 
            WHERE user_id = $1
        """, user_id)
        
        if existing:
            streak_id = existing['id']
            current_streak = existing['current_streak_days']
            longest_streak = existing['longest_streak_days']
            last_date = existing['last_shift_date']
            
            # Check if consecutive day
            if last_date and last_date == shift_date - timedelta(days=1):
                current_streak += 1
            else:
                current_streak = 1
            
            # Update longest streak if needed
            if current_streak > longest_streak:
                longest_streak = current_streak
            
            await conn.execute("""
                UPDATE user_operational_streaks 
                SET current_streak_days = $1,
                    longest_streak_days = $2,
                    last_shift_date = $3,
                    updated_at = $4
                WHERE id = $5
            """, current_streak, longest_streak, shift_date, 
                 datetime.now(), streak_id)
        else:
            # Create new streak record
            await conn.execute("""
                INSERT INTO user_operational_streaks 
                (user_id, current_streak_days, longest_streak_days, 
                 last_shift_date, total_points)
                VALUES ($1, $2, $3, $4, 0)
            """, user_id, 1, 1, shift_date)
    
    @staticmethod
    async def _check_achievements(conn, user_id: UUID):
        """Check and unlock achievements for user"""
        # Implementation would check achievement criteria
        # and insert into user_achievements table
        pass