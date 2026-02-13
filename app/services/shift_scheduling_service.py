"""
Advanced Shift Scheduling Service
Handles intelligent bulk assignments, pattern-based scheduling, and days-off management
"""
import logging
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple
from uuid import UUID
import json

from app.db.database import get_connection

log = logging.getLogger(__name__)


class ShiftSchedulingService:
    """Service for intelligent shift scheduling with patterns and bulk assignment"""

    @staticmethod
    def get_available_patterns(section_id: Optional[UUID]) -> List[Dict]:
        """Fetch shift patterns for a section. If `section_id` is None, return all patterns."""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    if section_id is None:
                        cur.execute("""
                            SELECT 
                                id, name, description, pattern_type, metadata, created_at
                            FROM shift_patterns
                            ORDER BY name
                        """)
                    else:
                        cur.execute("""
                            SELECT 
                                id, name, description, pattern_type, metadata, created_at
                            FROM shift_patterns
                            WHERE section_id = %s
                            ORDER BY name
                        """, (str(section_id),))
                    rows = cur.fetchall()
                    return [
                        {
                            'id': str(r[0]),
                            'name': r[1],
                            'description': r[2],
                            'pattern_type': r[3],
                            'metadata': r[4] or {},
                            'created_at': r[5].isoformat() if r[5] else None
                        }
                        for r in rows
                    ]
        except Exception as e:
            log.error(f"Error fetching patterns: {e}")
            return []

    @staticmethod
    def get_pattern_schedule(pattern_id: UUID) -> Dict:
        """Get the day-by-day schedule for a pattern (What shift on what day?)"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Get pattern info
                    cur.execute("""
                        SELECT id, name, pattern_type, metadata
                        FROM shift_patterns
                        WHERE id = %s
                    """, (str(pattern_id),))
                    pattern_row = cur.fetchone()
                    if not pattern_row:
                        return {}

                    pattern_info = {
                        'id': str(pattern_row[0]),
                        'name': pattern_row[1],
                        'pattern_type': pattern_row[2],
                        'metadata': pattern_row[3],
                        'schedule': {}
                    }

                    # Get days configuration
                    cur.execute("""
                        SELECT day_of_week, shift_id, is_off_day
                        FROM shift_pattern_days
                        WHERE pattern_id = %s
                        ORDER BY day_of_week
                    """, (str(pattern_id),))
                    
                    day_names = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
                    
                    for day_row in cur.fetchall():
                        day_of_week = day_row[0]
                        shift_id = day_row[1]
                        is_off_day = day_row[2]
                        
                        if not is_off_day and shift_id:
                            # Get shift details
                            cur.execute("""
                                SELECT name, start_time, end_time, color
                                FROM shifts
                                WHERE id = %s
                            """, (shift_id,))
                            shift_row = cur.fetchone()
                            if shift_row:
                                pattern_info['schedule'][day_names[day_of_week]] = {
                                    'shift_id': shift_id,
                                    'shift_name': shift_row[0],
                                    'start_time': str(shift_row[1]),
                                    'end_time': str(shift_row[2]),
                                    'color': shift_row[3]
                                }
                        elif is_off_day:
                            pattern_info['schedule'][day_names[day_of_week]] = {
                                'off_day': True
                            }

                    return pattern_info
        except Exception as e:
            log.error(f"Error fetching pattern schedule: {e}")
            return {}

    @staticmethod
    def bulk_assign_pattern(
        users: List[str],
        pattern_id: UUID,
        start_date: date,
        end_date: Optional[date],
        section_id: UUID,
        assigned_by: UUID
    ) -> Tuple[bool, int, List[str]]:
        """
        Bulk assign a shift pattern to multiple users.
        
        Args:
            users: List of user IDs
            pattern_id: The shift pattern to assign
            start_date: When to start applying the pattern
            end_date: When to stop (None = ongoing)
            section_id: Section context
            assigned_by: The user making the assignment
            
        Returns:
            (success, assignments_created, errors)
        """
        try:
            errors = []
            created_count = 0

            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Verify pattern exists and belongs to section
                    cur.execute("""
                        SELECT id FROM shift_patterns
                        WHERE id = %s AND section_id = %s
                    """, (str(pattern_id), str(section_id)))
                    
                    if not cur.fetchone():
                        return False, 0, ["Pattern not found in this section"]

                    # Get the pattern's day schedule
                    cur.execute("""
                        SELECT day_of_week, shift_id, is_off_day
                        FROM shift_pattern_days
                        WHERE pattern_id = %s
                    """, (str(pattern_id),))
                    
                    pattern_days = {}
                    for row in cur.fetchall():
                        pattern_days[row[0]] = {'shift_id': row[1], 'is_off_day': row[2]}

                    # For each user, create assignment and generate scheduled_shifts
                    for user_id in users:
                        try:
                            # Create user_shift_assignment
                            cur.execute("""
                                INSERT INTO user_shift_assignments 
                                (user_id, shift_pattern_id, start_date, end_date, assigned_by, status)
                                VALUES (%s, %s, %s, %s, %s, 'ACTIVE')
                                RETURNING id
                            """, (user_id, str(pattern_id), start_date, end_date, str(assigned_by)))
                            
                            assignment_id = cur.fetchone()[0]
                            created_count += 1

                            # Now generate scheduled_shifts for date range
                            current = start_date
                            end = end_date or (start_date + timedelta(days=90))

                            while current <= end:
                                day_of_week = current.weekday()
                                # Python weekday: 0=Monday, 6=Sunday; DB uses 0=Sunday, 1=Monday, etc.
                                db_day_of_week = (day_of_week + 1) % 7

                                if db_day_of_week in pattern_days:
                                    pattern_day = pattern_days[db_day_of_week]
                                    
                                    if not pattern_day['is_off_day'] and pattern_day['shift_id']:
                                        # Check for days off or exceptions
                                        cur.execute("""
                                            SELECT id FROM user_days_off
                                            WHERE user_id = %s
                                            AND start_date <= %s AND end_date >= %s
                                            AND status IN ('APPROVED', 'PENDING')
                                        """, (user_id, current, current))
                                        
                                        if not cur.fetchone():
                                            # Check for exceptions
                                            cur.execute("""
                                                SELECT shift_id, is_day_off FROM shift_exceptions
                                                WHERE user_id = %s AND exception_date = %s
                                            """, (user_id, current))
                                            
                                            exc = cur.fetchone()
                                            if exc:
                                                shift_to_assign = exc[0] if not exc[1] else None
                                            else:
                                                shift_to_assign = pattern_day['shift_id']

                                            if shift_to_assign:
                                                # Insert scheduled shift (check for conflicts)
                                                cur.execute("""
                                                    INSERT INTO scheduled_shifts
                                                    (shift_id, user_id, date, assigned_by, status, 
                                                     pattern_id, assignment_id, from_bulk_assign)
                                                    VALUES (%s, %s, %s, %s, 'ASSIGNED', %s, %s, TRUE)
                                                    ON CONFLICT (shift_id, user_id, date) DO NOTHING
                                                """, (pattern_day['shift_id'], user_id, current,
                                                      str(assigned_by), str(pattern_id), str(assignment_id)))

                                current += timedelta(days=1)

                        except Exception as user_err:
                            errors.append(f"User {user_id}: {str(user_err)}")
                            log.error(f"Error assigning pattern to user {user_id}: {user_err}")

                    conn.commit()
                    return True, created_count, errors

        except Exception as e:
            log.error(f"Error in bulk_assign_pattern: {e}")
            return False, 0, [str(e)]

    @staticmethod
    def add_days_off(
        user_id: str,
        start_date: date,
        end_date: date,
        reason: str,
        approved: bool = False,
        approved_by: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Register days off for a user (vacation, sick leave, etc.)"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Check for overlapping days off
                    cur.execute("""
                        SELECT id FROM user_days_off
                        WHERE user_id = %s
                        AND start_date <= %s AND end_date >= %s
                        AND status IN ('APPROVED', 'PENDING')
                    """, (user_id, end_date, start_date))
                    
                    if cur.fetchone():
                        return False, "Days off already registered for this period"

                    # Insert days off
                    cur.execute("""
                        INSERT INTO user_days_off
                        (user_id, start_date, end_date, reason, status, approved_by)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (
                        user_id,
                        start_date,
                        end_date,
                        reason,
                        'APPROVED' if approved else 'PENDING',
                        approved_by if approved else None
                    ))
                    
                    days_off_id = str(cur.fetchone()[0])
                    
                    # If approved, remove corresponding scheduled shifts
                    if approved:
                        cur.execute("""
                            DELETE FROM scheduled_shifts
                            WHERE user_id = %s AND date >= %s AND date <= %s
                        """, (user_id, start_date, end_date))

                    conn.commit()
                    return True, f"Days off registered (ID: {days_off_id})"

        except Exception as e:
            log.error(f"Error adding days off: {e}")
            return False, str(e)

    @staticmethod
    def get_user_schedule(user_id: str, start_date: date, end_date: date) -> List[Dict]:
        """Get a user's complete schedule for a date range (shifts + days off + exceptions)"""
        try:
            schedule = []
            current = start_date

            with get_connection() as conn:
                with conn.cursor() as cur:
                    while current <= end_date:
                        # Check if day off
                        cur.execute("""
                            SELECT id, reason, status
                            FROM user_days_off
                            WHERE user_id = %s
                            AND start_date <= %s AND end_date >= %s
                            AND status = 'APPROVED'
                        """, (user_id, current, current))
                        
                        day_off = cur.fetchone()
                        if day_off:
                            schedule.append({
                                'date': current.isoformat(),
                                'type': 'OFF_DAY',
                                'reason': day_off[1],
                                'status': day_off[2]
                            })
                        else:
                            # Check for scheduled shift
                            cur.execute("""
                                SELECT ss.id, s.name, s.start_time, s.end_time, s.color, ss.status
                                FROM scheduled_shifts ss
                                JOIN shifts s ON ss.shift_id = s.id
                                WHERE ss.user_id = %s AND ss.date = %s
                                LIMIT 1
                            """, (user_id, current))
                            
                            shift = cur.fetchone()
                            if shift:
                                schedule.append({
                                    'date': current.isoformat(),
                                    'type': 'SHIFT',
                                    'shift_id': shift[0],
                                    'shift_name': shift[1],
                                    'start_time': str(shift[2]),
                                    'end_time': str(shift[3]),
                                    'color': shift[4],
                                    'status': shift[5]
                                })
                            else:
                                schedule.append({
                                    'date': current.isoformat(),
                                    'type': 'UNSCHEDULED'
                                })

                        current += timedelta(days=1)

            return schedule
        except Exception as e:
            log.error(f"Error fetching user schedule: {e}")
            return []

    @staticmethod
    def set_shift_exception(
        user_id: str,
        exception_date: date,
        shift_id: Optional[int] = None,
        is_day_off: bool = False,
        reason: str = None,
        created_by: str = None
    ) -> Tuple[bool, str]:
        """Create a one-off exception for a specific date (override pattern or mark as day off)"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO shift_exceptions
                        (user_id, exception_date, shift_id, is_day_off, reason, created_by)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (user_id, exception_date) 
                        DO UPDATE SET shift_id = EXCLUDED.shift_id,
                                     is_day_off = EXCLUDED.is_day_off,
                                     reason = EXCLUDED.reason
                        RETURNING id
                    """, (user_id, exception_date, shift_id, is_day_off, reason, created_by))
                    
                    exc_id = str(cur.fetchone()[0])
                    
                    # Update corresponding scheduled shift
                    if is_day_off:
                        cur.execute("""
                            DELETE FROM scheduled_shifts
                            WHERE user_id = %s AND date = %s
                        """, (user_id, exception_date))
                    elif shift_id:
                        cur.execute("""
                            INSERT INTO scheduled_shifts
                            (shift_id, user_id, date, assigned_by, status)
                            VALUES (%s, %s, %s, %s, 'ASSIGNED')
                            ON CONFLICT (shift_id, user_id, date)
                            DO UPDATE SET status = 'ASSIGNED'
                        """, (shift_id, user_id, exception_date, created_by))

                    conn.commit()
                    return True, f"Exception created (ID: {exc_id})"

        except Exception as e:
            log.error(f"Error setting shift exception: {e}")
            return False, str(e)
