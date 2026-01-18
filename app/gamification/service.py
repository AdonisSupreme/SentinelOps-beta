# app/gamification/service.py
import asyncio
from typing import List, Optional, Dict
from uuid import UUID
from datetime import datetime, date, timedelta

from app.db.database import get_connection
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
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                query = """
                    SELECT 
                        gs.*,
                        ci.checklist_date,
                        ci.shift,
                        u.username as awarded_by_username
                    FROM gamification_scores gs
                    JOIN checklist_instances ci ON gs.shift_instance_id = ci.id
                    LEFT JOIN users u ON gs.awarded_by = u.id
                    WHERE gs.user_id = %s
                    {date_filter}
                    ORDER BY gs.awarded_at DESC
                    LIMIT %s
                """
                
                params = [user_id, limit]
                
                if start_date and end_date:
                    query = query.format(date_filter="AND ci.checklist_date BETWEEN %s AND %s")
                    params.insert(1, end_date)
                    params.insert(1, start_date)
                elif start_date:
                    query = query.format(date_filter="AND ci.checklist_date >= %s")
                    params.insert(1, start_date)
                elif end_date:
                    query = query.format(date_filter="AND ci.checklist_date <= %s")
                    params.insert(1, end_date)
                else:
                    query = query.format(date_filter="")
                
                await cur.execute(query, params)
                rows = await cur.fetchall()
                
                return [
                    {
                        'id': row[0],
                        'user_id': row[1],
                        'shift_instance_id': row[2],
                        'points': row[3],
                        'reason': row[4],
                        'metadata': row[5],
                        'awarded_by': row[6],
                        'awarded_at': row[7],
                        'checklist_date': row[8],
                        'shift': row[9],
                        'awarded_by_username': row[10]
                    }
                    for row in rows
                ]
    
    @staticmethod
    async def get_user_streak(user_id: UUID) -> Optional[dict]:
        """Get user's operational streak"""
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT * FROM user_operational_streaks 
                    WHERE user_id = %s
                """, [user_id])
                
                row = await cur.fetchone()
                
                if row:
                    return {
                        'id': row[0],
                        'user_id': row[1],
                        'current_streak_days': row[2],
                        'longest_streak_days': row[3],
                        'perfect_shifts_count': row[4],
                        'total_points': row[5],
                        'shift_completion_rate': float(row[6]) if row[6] else 0.0,
                        'last_shift_date': row[7],
                        'updated_at': row[8]
                    }
                return None
    
    @staticmethod
    async def get_leaderboard(
        timeframe: str = "weekly",  # daily, weekly, monthly, all_time
        limit: int = 50
    ) -> List[dict]:
        """Get gamification leaderboard"""
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                
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
                
                # Base query
                query = """
                    WITH user_scores AS (
                        SELECT 
                            gs.user_id,
                            SUM(gs.points) as total_points,
                            COUNT(DISTINCT gs.shift_instance_id) as shift_count
                        FROM gamification_scores gs
                        {date_join}
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
                    LIMIT %s
                """
                
                if start_date and end_date:
                    date_join = """
                        JOIN checklist_instances ci ON gs.shift_instance_id = ci.id
                        WHERE ci.checklist_date BETWEEN %s AND %s
                    """
                    await cur.execute(query.format(date_join=date_join), 
                                    [start_date, end_date, limit])
                else:
                    date_join = ""
                    await cur.execute(query.format(date_join=date_join), [limit])
                
                rows = await cur.fetchall()
                
                return [
                    {
                        'user_id': row[0],
                        'username': row[1],
                        'first_name': row[2],
                        'last_name': row[3],
                        'total_points': row[4],
                        'current_streak': row[5],
                        'perfect_shifts': row[6],
                        'shift_count': row[7],
                        'rank': row[8],
                        'avg_points_per_shift': round(row[4] / row[7], 1) if row[7] > 0 else 0
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
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO gamification_scores 
                    (user_id, shift_instance_id, points, reason, metadata, awarded_by)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING *
                """, [user_id, shift_instance_id, points, reason, 
                     metadata, awarded_by])
                
                row = await cur.fetchone()
                
                # Update user streak
                await GamificationService._update_streak(conn, cur, user_id, 
                                                        shift_instance_id)
                
                # Check for achievement unlocks
                await GamificationService._check_achievements(conn, cur, user_id)
                
                await conn.commit()
                
                return {
                    'id': row[0],
                    'user_id': row[1],
                    'shift_instance_id': row[2],
                    'points': row[3],
                    'reason': row[4],
                    'metadata': row[5],
                    'awarded_by': row[6],
                    'awarded_at': row[7]
                }
    
    @staticmethod
    async def _update_streak(conn, cur, user_id: UUID, shift_instance_id: UUID):
        """Update user's operational streak"""
        await cur.execute("""
            SELECT checklist_date FROM checklist_instances 
            WHERE id = %s
        """, [shift_instance_id])
        
        shift_date = (await cur.fetchone())[0]
        
        await cur.execute("""
            SELECT * FROM user_operational_streaks 
            WHERE user_id = %s FOR UPDATE
        """, [user_id])
        
        existing = await cur.fetchone()
        
        if existing:
            streak_id, _, current_streak, longest_streak, perfect_shifts, total_points, completion_rate, last_date, updated = existing
            
            # Check if consecutive day
            if last_date and last_date == shift_date - timedelta(days=1):
                current_streak += 1
            else:
                current_streak = 1
            
            # Update longest streak if needed
            if current_streak > longest_streak:
                longest_streak = current_streak
            
            await cur.execute("""
                UPDATE user_operational_streaks 
                SET current_streak_days = %s,
                    longest_streak_days = %s,
                    last_shift_date = %s,
                    updated_at = %s
                WHERE id = %s
            """, [current_streak, longest_streak, shift_date, 
                 datetime.now(), streak_id])
        else:
            # Create new streak record
            await cur.execute("""
                INSERT INTO user_operational_streaks 
                (user_id, current_streak_days, longest_streak_days, 
                 last_shift_date, total_points)
                VALUES (%s, %s, %s, %s, 0)
            """, [user_id, 1, 1, shift_date])
    
    @staticmethod
    async def _check_achievements(conn, cur, user_id: UUID):
        """Check and unlock achievements for user"""
        # Implementation would check achievement criteria
        # and insert into user_achievements table
        pass