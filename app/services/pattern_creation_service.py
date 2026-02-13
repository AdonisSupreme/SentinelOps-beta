"""
Enhanced Pattern Creation Service
Ensures patterns are created with their complete schedule data
"""
from uuid import UUID
from typing import Dict, List, Optional
import logging
from app.db.database import get_connection

log = logging.getLogger(__name__)


class PatternCreationService:
    """Service for creating and managing shift patterns with schedules."""
    
    @staticmethod
    def create_pattern_with_schedule(
        name: str,
        description: str,
        section_id: UUID,
        pattern_type: str,  # FIXED, ROTATING, CUSTOM
        schedule_config: Dict[int, Dict],  # {day_of_week: {shift_id, is_off_day}}
        metadata: Dict = None,
        created_by: UUID = None
    ) -> tuple[bool, Optional[str], List[str]]:
        """
        Create a shift pattern with its complete day-by-day schedule.
        
        Args:
            name: Pattern name
            description: Pattern description
            section_id: Section this pattern belongs to
            pattern_type: FIXED, ROTATING, or CUSTOM
            schedule_config: Dictionary mapping day_of_week (0-6) to shift info
            metadata: Additional metadata (colors, display info)
            created_by: UUID of user creating the pattern
            
        Returns:
            (success, pattern_id, errors)
            
        Example:
            success, pattern_id, errors = PatternCreationService.create_pattern_with_schedule(
                name="Standard Weekday Morning",
                description="Mon-Fri 7-3, Weekends Off",
                section_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
                pattern_type="FIXED",
                schedule_config={
                    0: {"shift_id": None, "is_off_day": True},     # Sunday OFF
                    1: {"shift_id": 1, "is_off_day": False},       # Monday MORNING
                    2: {"shift_id": 1, "is_off_day": False},       # Tuesday MORNING
                    3: {"shift_id": 1, "is_off_day": False},       # Wednesday MORNING
                    4: {"shift_id": 1, "is_off_day": False},       # Thursday MORNING
                    5: {"shift_id": 1, "is_off_day": False},       # Friday MORNING
                    6: {"shift_id": None, "is_off_day": True},     # Saturday OFF
                },
                metadata={"display_color": "#00f2ff", "shift_type": "WEEKDAY_MORNING"}
            )
        """
        errors = []
        
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Create pattern
                    cur.execute("""
                        INSERT INTO shift_patterns
                        (name, description, section_id, pattern_type, metadata, created_by)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (
                        name,
                        description,
                        str(section_id),
                        pattern_type,
                        metadata or {},
                        str(created_by) if created_by else None
                    ))
                    
                    pattern_id = cur.fetchone()[0]
                    log.info(f"Created pattern '{name}' (ID: {pattern_id})")
                    
                    # Insert schedule days
                    for day_of_week, config in schedule_config.items():
                        try:
                            cur.execute("""
                                INSERT INTO shift_pattern_days
                                (pattern_id, day_of_week, shift_id, is_off_day)
                                VALUES (%s, %s, %s, %s)
                                ON CONFLICT (pattern_id, day_of_week) DO NOTHING
                            """, (
                                str(pattern_id),
                                day_of_week,
                                config.get('shift_id'),
                                config.get('is_off_day', False)
                            ))
                        except Exception as e:
                            err = f"Failed to add day {day_of_week} to pattern: {str(e)}"
                            errors.append(err)
                            log.error(err)
                    
                    conn.commit()
                    
                    return len(errors) == 0, str(pattern_id), errors
                    
        except Exception as e:
            error = f"Failed to create pattern: {str(e)}"
            log.error(error)
            return False, None, [error]

    @staticmethod
    def get_or_create_standard_patterns(section_id: UUID):
        """
        Ensure the three standard patterns exist for a section.
        
        Standard patterns:
        1. Standard Weekday Morning (Mon-Fri 7-3, weekends off)
        2. Standard Rotating 3-Shift (24/7 coverage)
        3. Weekend Night Coverage (Fri-Sun nights, weekdays off)
        """
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Check if patterns already exist
                cur.execute("""
                    SELECT COUNT(*) FROM shift_patterns 
                    WHERE section_id = %s
                """, (str(section_id),))
                
                count = cur.fetchone()[0]
                if count >= 3:
                    log.info(f"Standard patterns already exist for section {section_id}")
                    return True
                
                # Get shift IDs
                cur.execute("""
                    SELECT id, name FROM shifts 
                    WHERE LOWER(name) IN ('morning', 'afternoon', 'night')
                """)
                
                shifts = {row[1].upper(): row[0] for row in cur.fetchall()}
                
                if not shifts.get('MORNING'):
                    log.warning("Shifts not found - ensure initialization_shifts migration was applied")
                    return False
                
                morning_id = shifts['MORNING']
                afternoon_id = shifts.get('AFTERNOON')
                night_id = shifts.get('NIGHT')
                
                # Create pattern 1: Standard Weekday Morning
                success1, _, _ = PatternCreationService.create_pattern_with_schedule(
                    name="Standard Weekday Morning",
                    description="Monday-Friday Morning (07:00-15:00), Weekends Off",
                    section_id=section_id,
                    pattern_type="FIXED",
                    schedule_config={
                        0: {"shift_id": None, "is_off_day": True},      # Sunday
                        1: {"shift_id": morning_id, "is_off_day": False},  # Monday
                        2: {"shift_id": morning_id, "is_off_day": False},  # Tuesday
                        3: {"shift_id": morning_id, "is_off_day": False},  # Wednesday
                        4: {"shift_id": morning_id, "is_off_day": False},  # Thursday
                        5: {"shift_id": morning_id, "is_off_day": False},  # Friday
                        6: {"shift_id": None, "is_off_day": True},      # Saturday
                    },
                    metadata={"shift_type": "WEEKDAY_MORNING", "display_color": "#00f2ff"}
                )
                
                # Create pattern 2: Rotating 3-Shift
                if afternoon_id and night_id:
                    success2, _, _ = PatternCreationService.create_pattern_with_schedule(
                        name="Standard Rotating 3-Shift",
                        description="Rotates through MORNING → AFTERNOON → NIGHT every day",
                        section_id=section_id,
                        pattern_type="ROTATING",
                        schedule_config={
                            0: {"shift_id": night_id, "is_off_day": False},
                            1: {"shift_id": morning_id, "is_off_day": False},
                            2: {"shift_id": afternoon_id, "is_off_day": False},
                            3: {"shift_id": night_id, "is_off_day": False},
                            4: {"shift_id": morning_id, "is_off_day": False},
                            5: {"shift_id": afternoon_id, "is_off_day": False},
                            6: {"shift_id": night_id, "is_off_day": False},
                        },
                        metadata={"shift_type": "ROTATING_3SHIFT", "display_color": "#00ff88"}
                    )
                    
                    # Create pattern 3: Weekend Night Coverage
                    success3, _, _ = PatternCreationService.create_pattern_with_schedule(
                        name="Weekend Night Coverage",
                        description="Friday Night, Saturday Night, Sunday Night (23:00-07:00)",
                        section_id=section_id,
                        pattern_type="FIXED",
                        schedule_config={
                            0: {"shift_id": night_id, "is_off_day": False},  # Sunday
                            1: {"shift_id": None, "is_off_day": True},       # Monday
                            2: {"shift_id": None, "is_off_day": True},       # Tuesday
                            3: {"shift_id": None, "is_off_day": True},       # Wednesday
                            4: {"shift_id": None, "is_off_day": True},       # Thursday
                            5: {"shift_id": night_id, "is_off_day": False},  # Friday
                            6: {"shift_id": night_id, "is_off_day": False},  # Saturday
                        },
                        metadata={"shift_type": "WEEKEND_NIGHT", "display_color": "#ff00ff"}
                    )
                
                return success1
