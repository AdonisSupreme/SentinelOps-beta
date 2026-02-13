#!/usr/bin/env python3
"""
Fix Pattern Schedules: Ensure all shift patterns have their day-by-day schedules configured.
This script populates the shift_pattern_days table for patterns that don't have it yet.
"""
import logging
import sys
import os

# Add parent directory to Python path so we can import app module
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app.db.database import get_connection
from uuid import UUID

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def ensure_pattern_schedules():
    """Populate shift_pattern_days for any patterns that don't have schedules."""
    errors = []
    fixed_count = 0
    
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Get all shifts (we need to map them)
            cur.execute("SELECT id, name FROM shifts ORDER BY name")
            shifts = {row[1].upper(): row[0] for row in cur.fetchall()}
            log.info(f"Found shifts: {list(shifts.keys())}")
            
            if not shifts:
                return False, 0, ["No shifts found in database! Create them first."]
            
            # Get all patterns
            cur.execute("""
                SELECT id, name, pattern_type, metadata
                FROM shift_patterns
                ORDER BY name
            """)
            patterns = cur.fetchall()
            log.info(f"Found {len(patterns)} patterns to check")
            
            for pattern_id, name, pattern_type, metadata in patterns:
                # Check if pattern has schedule
                cur.execute("""
                    SELECT COUNT(*) FROM shift_pattern_days WHERE pattern_id = %s
                """, (str(pattern_id),))
                
                count = cur.fetchone()[0]
                if count == 0:
                    log.info(f"Pattern '{name}' ({pattern_id}) has no schedule - fixing...")
                    
                    # Generate schedule based on pattern name/type
                    try:
                        schedule_data = _generate_schedule_for_pattern(name, pattern_type, shifts)
                        
                        # Insert schedule
                        for day_of_week, day_info in schedule_data.items():
                            cur.execute("""
                                INSERT INTO shift_pattern_days 
                                (pattern_id, day_of_week, shift_id, is_off_day)
                                VALUES (%s, %s, %s, %s)
                                ON CONFLICT (pattern_id, day_of_week) DO NOTHING
                            """, (
                                str(pattern_id),
                                day_of_week,
                                day_info.get('shift_id'),
                                day_info.get('is_off_day', False)
                            ))
                        
                        fixed_count += 1
                        log.info(f"✓ Fixed pattern '{name}'")
                    except Exception as e:
                        err_msg = f"Failed to fix pattern {name}: {str(e)}"
                        errors.append(err_msg)
                        log.error(err_msg)
                else:
                    log.info(f"Pattern '{name}' already has {count} day(s) configured")
            
            conn.commit()
    
    return len(errors) == 0, fixed_count, errors


def _generate_schedule_for_pattern(name: str, pattern_type: str, shifts: dict) -> dict:
    """Generate day-by-day schedule based on pattern name/type."""
    schedule = {}
    
    name_lower = name.lower()
    morning_id = shifts.get('MORNING')
    afternoon_id = shifts.get('AFTERNOON')
    night_id = shifts.get('NIGHT')
    
    if 'weekday' in name_lower and 'morning' in name_lower:
        # Mon-Fri Morning, Weekends Off
        for day in range(1, 6):  # 1=Mon, 2=Tue, ..., 5=Fri
            schedule[day] = {'shift_id': morning_id, 'is_off_day': False}
        schedule[6] = {'shift_id': None, 'is_off_day': True}  # Saturday
        schedule[0] = {'shift_id': None, 'is_off_day': True}  # Sunday
    
    elif 'rotating' in name_lower and '3' in name_lower:
        # 3-shift rotating: Morning -> Afternoon -> Night -> repeat
        shifts_list = [morning_id, afternoon_id, night_id]
        for day in range(7):  # All days
            shift_idx = day % 3
            schedule[day] = {'shift_id': shifts_list[shift_idx], 'is_off_day': False}
    
    elif 'weekend' in name_lower and 'night' in name_lower:
        # Fri-Sun Night, Mon-Thu Off
        schedule[1] = {'shift_id': None, 'is_off_day': True}  # Monday
        schedule[2] = {'shift_id': None, 'is_off_day': True}  # Tuesday
        schedule[3] = {'shift_id': None, 'is_off_day': True}  # Wednesday
        schedule[4] = {'shift_id': None, 'is_off_day': True}  # Thursday
        schedule[5] = {'shift_id': night_id, 'is_off_day': False}  # Friday
        schedule[6] = {'shift_id': night_id, 'is_off_day': False}  # Saturday
        schedule[0] = {'shift_id': night_id, 'is_off_day': False}  # Sunday
    
    else:
        # Default: All weekdays same shift, weekends off
        for day in range(1, 6):  # 1=Mon, ..., 5=Fri
            schedule[day] = {'shift_id': morning_id, 'is_off_day': False}
        schedule[6] = {'shift_id': None, 'is_off_day': True}
        schedule[0] = {'shift_id': None, 'is_off_day': True}
    
    return schedule


if __name__ == '__main__':
    success, count, errors = ensure_pattern_schedules()
    
    print(f"\n{'='*60}")
    print(f"Pattern Schedule Fix Summary")
    print(f"{'='*60}")
    print(f"Fixed: {count} pattern(s)")
    
    if errors:
        print(f"\nErrors ({len(errors)}):")
        for err in errors:
            print(f"  ✗ {err}")
    else:
        print(f"\n✓ All patterns checked and fixed!")
    
    print(f"{'='*60}\n")
    
    sys.exit(0 if success else 1)
