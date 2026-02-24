# app/checklists/handover_service.py
"""
Handover Notes Service

Handles shift-to-shift handover notes with proper sequence logic:
- Morning → Afternoon → Night → Morning (next day)
- Notes belong to current instance AND next instance in sequence
- Supports both manual and automatic handover notes
"""

from typing import List, Optional, Dict, Any
from uuid import UUID, uuid4
from datetime import datetime, date, timedelta
from enum import Enum

from app.db.database import get_async_connection
from app.core.logging import get_logger
from app.checklists.schemas import (
    HandoverNoteCreate, HandoverNoteResponse, 
    ShiftType, ChecklistStatus
)

log = get_logger("handover-service")

class HandoverService:
    """Service for managing shift handover notes"""
    
    @staticmethod
    def get_shift_sequence() -> List[ShiftType]:
        """Get the standard shift sequence: Morning → Afternoon → Night"""
        return [ShiftType.MORNING, ShiftType.AFTERNOON, ShiftType.NIGHT]
    
    @staticmethod
    def get_next_shift(current_shift: ShiftType) -> tuple[ShiftType, date]:
        """
        Get the next shift in sequence and its date.
        Returns: (next_shift, next_shift_date)
        """
        sequence = HandoverService.get_shift_sequence()
        current_index = sequence.index(current_shift)
        
        # Calculate next shift index (wraps around)
        next_index = (current_index + 1) % len(sequence)
        next_shift = sequence[next_index]
        
        # If we wrap around from NIGHT to MORNING, it's the next day
        if current_shift == ShiftType.NIGHT:
            next_date = date.today() + timedelta(days=1)
        else:
            next_date = date.today()
            
        return next_shift, next_date
    
    @staticmethod
    def get_previous_shift(current_shift: ShiftType) -> tuple[ShiftType, date]:
        """
        Get the previous shift in sequence and its date.
        Returns: (prev_shift, prev_shift_date)
        """
        sequence = HandoverService.get_shift_sequence()
        current_index = sequence.index(current_shift)
        
        # Calculate previous shift index (wraps around)
        prev_index = (current_index - 1) % len(sequence)
        prev_shift = sequence[prev_index]
        
        # If we wrap around from MORNING to NIGHT, it's the previous day
        if current_shift == ShiftType.MORNING:
            prev_date = date.today() - timedelta(days=1)
        else:
            prev_date = date.today()
            
        return prev_shift, prev_date
    
    @staticmethod
    async def create_handover_note(
        from_instance_id: UUID,
        content: str,
        priority: int = 2,
        created_by: UUID = None,
        to_shift: Optional[ShiftType] = None,
        to_date: Optional[date] = None
    ) -> Dict[str, Any]:
        """
        Create a handover note that links to the next shift in sequence.
        
        Args:
            from_instance_id: Current checklist instance ID
            content: Handover note content
            priority: Priority level (1-4)
            created_by: User ID creating the note
            to_shift: Optional target shift (defaults to next in sequence)
            to_date: Optional target date (defaults to calculated)
        
        Returns:
            Created handover note data
        """
        async with get_async_connection() as conn:
            # Get current instance details to determine shift sequence
            from_instance = await conn.fetchrow("""
                SELECT checklist_date, shift, status
                FROM checklist_instances
                WHERE id = $1
            """, from_instance_id)
            
            if not from_instance:
                raise ValueError(f"Source instance {from_instance_id} not found")
            
            current_shift = ShiftType(from_instance['shift'])
            current_date = from_instance['checklist_date']
            
            # Determine target shift and date
            if to_shift and to_date:
                # Use provided target
                target_shift = to_shift
                target_date = to_date
            else:
                # Calculate next shift in sequence
                target_shift, target_date = HandoverService.get_next_shift(current_shift)
                # Adjust date based on current instance date
                if current_shift == ShiftType.NIGHT:
                    target_date = current_date + timedelta(days=1)
                else:
                    target_date = current_date
            
            # Find or create the target instance
            to_instance_id = await HandoverService._get_or_create_target_instance(
                conn, target_shift, target_date, created_by
            )
            
            # Create the handover note
            note_id = uuid4()
            note = await conn.fetchrow("""
                INSERT INTO handover_notes 
                (id, from_instance_id, to_instance_id, content, priority, created_by, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING *
            """, note_id, from_instance_id, to_instance_id, content, priority, created_by, datetime.now())
            
            log.info(f"Created handover note {note_id} from {from_instance_id} to {to_instance_id}")
            
            return {
                'id': note['id'],
                'from_instance_id': note['from_instance_id'],
                'to_instance_id': note['to_instance_id'],
                'content': note['content'],
                'priority': note['priority'],
                'created_by': note['created_by'],
                'created_at': note['created_at'],
                'target_shift': target_shift.value,
                'target_date': target_date.isoformat()
            }
    
    @staticmethod
    async def _get_or_create_target_instance(
        conn, 
        target_shift: ShiftType, 
        target_date: date, 
        created_by: UUID
    ) -> UUID:
        """
        Get existing target instance or create a new one.
        Returns the target instance ID.
        """
        # Try to find existing instance
        existing = await conn.fetchrow("""
            SELECT id FROM checklist_instances
            WHERE checklist_date = $1 AND shift = $2
            ORDER BY created_at DESC
            LIMIT 1
        """, target_date, target_shift.value)
        
        if existing:
            return existing['id']
        
        # Create new instance if none exists - use a simple direct approach
        # to avoid circular imports
        from uuid import uuid4
        from datetime import datetime, time, timedelta
        
        # Calculate shift times
        shift_times = {
            ShiftType.MORNING: {'start': time(7, 0), 'end': time(15, 0)},
            ShiftType.AFTERNOON: {'start': time(15, 0), 'end': time(23, 0)},
            ShiftType.NIGHT: {'start': time(23, 0), 'end': time(7, 0)}
        }
        
        shift_start = datetime.combine(target_date, shift_times[target_shift]['start'])
        shift_end = datetime.combine(
            target_date + timedelta(days=1) if target_shift == ShiftType.NIGHT else target_date,
            shift_times[target_shift]['end']
        )
        
        # Create basic instance without template loading for now
        instance_id = uuid4()
        
        # Find the active template for this shift
        template_result = await conn.fetchrow("""
            SELECT id FROM checklist_templates 
            WHERE shift = $1 AND is_active = true 
            ORDER BY version DESC 
            LIMIT 1
        """, target_shift.value)
        
        template_id = template_result['id'] if template_result else None
        
        if not template_id:
            raise ValueError(f"No active template found for {target_shift.value} shift")
        
        await conn.execute("""
            INSERT INTO checklist_instances 
            (id, template_id, checklist_date, shift, shift_start, shift_end, status, created_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """, instance_id, template_id, target_date, target_shift.value,
            shift_start, shift_end, 'OPEN', created_by
        )
        
        # Add creator as participant
        await conn.execute("""
            INSERT INTO checklist_participants (instance_id, user_id)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
        """, instance_id, created_by)
        
        return instance_id
    
    @staticmethod
    async def get_handover_notes_for_instance(
        instance_id: UUID,
        include_outgoing: bool = True,
        include_incoming: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Get handover notes for a specific checklist instance.
        
        Args:
            instance_id: Checklist instance ID
            include_outgoing: Include notes FROM this instance
            include_incoming: Include notes TO this instance
        
        Returns:
            List of handover notes with user details
        """
        async with get_async_connection() as conn:
            notes = []
            
            # Build query based on what to include
            if include_outgoing and include_incoming:
                query = """
                    SELECT 
                        hn.*,
                        from_ci.shift as from_shift,
                        from_ci.checklist_date as from_date,
                        to_ci.shift as to_shift,
                        to_ci.checklist_date as to_date,
                        creator.username as created_by_username,
                        creator.first_name as created_by_first_name,
                        creator.last_name as created_by_last_name,
                        ack_user.username as acknowledged_by_username,
                        resolver.username as resolved_by_username
                    FROM handover_notes hn
                    LEFT JOIN checklist_instances from_ci ON hn.from_instance_id = from_ci.id
                    LEFT JOIN checklist_instances to_ci ON hn.to_instance_id = to_ci.id
                    LEFT JOIN users creator ON hn.created_by = creator.id
                    LEFT JOIN users ack_user ON hn.acknowledged_by = ack_user.id
                    LEFT JOIN users resolver ON hn.resolved_by = resolver.id
                    WHERE hn.from_instance_id = $1 OR hn.to_instance_id = $1
                    ORDER BY hn.created_at DESC
                """
                params = [instance_id]
            elif include_outgoing:
                query = """
                    SELECT 
                        hn.*,
                        from_ci.shift as from_shift,
                        from_ci.checklist_date as from_date,
                        to_ci.shift as to_shift,
                        to_ci.checklist_date as to_date,
                        creator.username as created_by_username,
                        creator.first_name as created_by_first_name,
                        creator.last_name as created_by_last_name,
                        ack_user.username as acknowledged_by_username,
                        resolver.username as resolved_by_username
                    FROM handover_notes hn
                    LEFT JOIN checklist_instances from_ci ON hn.from_instance_id = from_ci.id
                    LEFT JOIN checklist_instances to_ci ON hn.to_instance_id = to_ci.id
                    LEFT JOIN users creator ON hn.created_by = creator.id
                    LEFT JOIN users ack_user ON hn.acknowledged_by = ack_user.id
                    LEFT JOIN users resolver ON hn.resolved_by = resolver.id
                    WHERE hn.from_instance_id = $1
                    ORDER BY hn.created_at DESC
                """
                params = [instance_id]
            else:  # include_incoming only
                query = """
                    SELECT 
                        hn.*,
                        from_ci.shift as from_shift,
                        from_ci.checklist_date as from_date,
                        to_ci.shift as to_shift,
                        to_ci.checklist_date as to_date,
                        creator.username as created_by_username,
                        creator.first_name as created_by_first_name,
                        creator.last_name as created_by_last_name,
                        ack_user.username as acknowledged_by_username,
                        resolver.username as resolved_by_username
                    FROM handover_notes hn
                    LEFT JOIN checklist_instances from_ci ON hn.from_instance_id = from_ci.id
                    LEFT JOIN checklist_instances to_ci ON hn.to_instance_id = to_ci.id
                    LEFT JOIN users creator ON hn.created_by = creator.id
                    LEFT JOIN users ack_user ON hn.acknowledged_by = ack_user.id
                    LEFT JOIN users resolver ON hn.resolved_by = resolver.id
                    WHERE hn.to_instance_id = $1
                    ORDER BY hn.created_at DESC
                """
                params = [instance_id]
            
            rows = await conn.fetch(query, *params)
            
            for row in rows:
                notes.append({
                    'id': row['id'],
                    'from_instance_id': row['from_instance_id'],
                    'to_instance_id': row['to_instance_id'],
                    'content': row['content'],
                    'priority': row['priority'],
                    'acknowledged_by': row['acknowledged_by'],
                    'acknowledged_at': row['acknowledged_at'],
                    'resolved_by': row['resolved_by'],
                    'resolved_at': row['resolved_at'],
                    'resolution_notes': row['resolution_notes'],
                    'created_by': row['created_by'],
                    'created_at': row['created_at'],
                    'from_shift': row['from_shift'],
                    'from_date': row['from_date'],
                    'to_shift': row['to_shift'],
                    'to_date': row['to_date'],
                    'created_by_username': row['created_by_username'],
                    'created_by_first_name': row['created_by_first_name'],
                    'created_by_last_name': row['created_by_last_name'],
                    'acknowledged_by_username': row['acknowledged_by_username'],
                    'resolved_by_username': row['resolved_by_username'],
                    'direction': 'outgoing' if row['from_instance_id'] == instance_id else 'incoming'
                })
            
            return notes
    
    @staticmethod
    async def acknowledge_handover_note(
        note_id: UUID,
        acknowledged_by: UUID
    ) -> Dict[str, Any]:
        """Acknowledge a handover note"""
        async with get_async_connection() as conn:
            note = await conn.fetchrow("""
                UPDATE handover_notes 
                SET acknowledged_by = $1, acknowledged_at = $2
                WHERE id = $3 AND acknowledged_by IS NULL
                RETURNING *
            """, acknowledged_by, datetime.now(), note_id)
            
            if not note:
                raise ValueError(f"Handover note {note_id} not found or already acknowledged")
            
            log.info(f"Handover note {note_id} acknowledged by {acknowledged_by}")
            
            return {
                'id': note['id'],
                'acknowledged_by': note['acknowledged_by'],
                'acknowledged_at': note['acknowledged_at']
            }
    
    @staticmethod
    async def resolve_handover_note(
        note_id: UUID,
        resolved_by: UUID,
        resolution_notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """Resolve a handover note"""
        async with get_async_connection() as conn:
            note = await conn.fetchrow("""
                UPDATE handover_notes 
                SET resolved_by = $1, resolved_at = $2, resolution_notes = $3
                WHERE id = $3 AND resolved_by IS NULL
                RETURNING *
            """, resolved_by, datetime.now(), resolution_notes, note_id)
            
            if not note:
                raise ValueError(f"Handover note {note_id} not found or already resolved")
            
            log.info(f"Handover note {note_id} resolved by {resolved_by}")
            
            return {
                'id': note['id'],
                'resolved_by': note['resolved_by'],
                'resolved_at': note['resolved_at'],
                'resolution_notes': note['resolution_notes']
            }
    
    @staticmethod
    async def create_automatic_handover_notes(
        from_instance_id: UUID,
        checklist_date: date,
        shift: ShiftType,
        user_id: UUID
    ):
        """
        Create automatic handover notes from previous shift if there were issues.
        This is called when a new checklist instance is created.
        """
        async with get_async_connection() as conn:
            # Get previous shift details
            prev_shift, prev_date = HandoverService.get_previous_shift(shift)
            
            # Find previous instance
            prev_instance = await conn.fetchrow("""
                SELECT ci.id, ci.status, COUNT(CASE WHEN cii.status = 'FAILED' THEN 1 END) as failed_items,
                       COUNT(CASE WHEN cii.status != 'COMPLETED' THEN 1 END) as incomplete_items
                FROM checklist_instances ci
                LEFT JOIN checklist_instance_items cii ON ci.id = cii.instance_id
                WHERE ci.checklist_date = $1 AND ci.shift = $2
                GROUP BY ci.id
                LIMIT 1
            """, prev_date, prev_shift.value)
            
            if not prev_instance:
                log.info(f"No previous instance found for {prev_shift.value} on {prev_date}")
                return
            
            prev_id = prev_instance['id']
            prev_status = prev_instance['status']
            failed_count = prev_instance['failed_items'] or 0
            incomplete_count = prev_instance['incomplete_items'] or 0
            
            # Only create handover if there were issues
            if (prev_status in [ChecklistStatus.COMPLETED_WITH_EXCEPTIONS.value, 
                              ChecklistStatus.INCOMPLETE.value] or 
                failed_count > 0 or incomplete_count > 0):
                
                content = (
                    f"Automatic handover from {prev_shift.value.lower()} shift. "
                    f"Status: {prev_status}, "
                    f"Failed items: {failed_count}, "
                    f"Incomplete items: {incomplete_count}"
                )
                
                priority = 2 if failed_count == 0 else 3 if failed_count <= 3 else 4
                
                await HandoverService.create_handover_note(
                    from_instance_id=prev_id,
                    content=content,
                    priority=priority,
                    created_by=user_id
                )
                
                log.info(f"Created automatic handover note from {prev_shift.value} shift")
