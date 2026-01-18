# app/checklists/service.py
import asyncio
from typing import List, Optional, Dict, Any
from uuid import UUID, uuid4
from datetime import datetime, date, time, timedelta
from enum import Enum
import json

from app.db.database import get_connection
from app.core.logging import get_logger
from app.checklists.schemas import (
    ChecklistTemplateCreate, ChecklistTemplateUpdate,
    ChecklistInstanceCreate, ChecklistItemUpdate,
    HandoverNoteCreate, ShiftType, ChecklistStatus,
    ItemStatus, ActivityAction
)
from app.auth.service import get_current_user

log = get_logger("checklists-service")

class ChecklistService:
    """Core checklist business logic service"""
    
    @staticmethod
    async def ensure_default_templates():
        """Create default checklist templates if they don't exist"""
        # This would be called on startup
        # Implementation would create MORNING, AFTERNOON, NIGHT templates
        # based on the DOCX content
        pass
    
    @staticmethod
    async def get_active_templates(shift: Optional[ShiftType] = None) -> List[Dict]:
        """Get active checklist templates"""
        query = """
            SELECT ct.*, 
                   COUNT(cti.id) as item_count,
                   array_agg(
                       jsonb_build_object(
                           'id', cti.id,
                           'title', cti.title,
                           'item_type', cti.item_type,
                           'is_required', cti.is_required,
                           'sort_order', cti.sort_order
                       )
                   ) as items_preview
            FROM checklist_templates ct
            LEFT JOIN checklist_template_items cti ON ct.id = cti.template_id
            WHERE ct.is_active = true
            {shift_filter}
            GROUP BY ct.id
            ORDER BY ct.shift, ct.version DESC
        """
        
        shift_filter = "AND ct.shift = %s" if shift else ""
        params = [shift] if shift else []
        
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query.format(shift_filter=shift_filter), params)
                rows = await cur.fetchall()
                
                return [
                    {
                        'id': row[0],
                        'name': row[1],
                        'description': row[2],
                        'shift': row[3],
                        'is_active': row[4],
                        'version': row[5],
                        'created_at': row[6],
                        'item_count': row[7],
                        'items_preview': row[8]
                    }
                    for row in rows
                ]
    
    @staticmethod
    async def create_checklist_instance(
        checklist_date: date,
        shift: ShiftType,
        template_id: Optional[UUID] = None,
        user_id: UUID = None
    ) -> Dict:
        """Create a new checklist instance for a shift"""
        
        # Calculate shift times
        shift_times = {
            ShiftType.MORNING: {
                'start': time(6, 0),
                'end': time(14, 0)
            },
            ShiftType.AFTERNOON: {
                'start': time(14, 0),
                'end': time(22, 0)
            },
            ShiftType.NIGHT: {
                'start': time(22, 0),
                'end': time(6, 0)
            }
        }
        
        shift_start = datetime.combine(checklist_date, shift_times[shift]['start'])
        shift_end = datetime.combine(
            checklist_date + timedelta(days=1) if shift == ShiftType.NIGHT and shift_times[shift]['end'] < shift_times[shift]['start'] else checklist_date,
            shift_times[shift]['end']
        )
        
        # Get or find template
        if not template_id:
            async with get_connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("""
                        SELECT id FROM checklist_templates 
                        WHERE shift = %s AND is_active = true
                        ORDER BY version DESC LIMIT 1
                    """, [shift.value])
                    row = await cur.fetchone()
                    if row:
                        template_id = row[0]
                    else:
                        raise ValueError(f"No active template found for shift: {shift}")
        
        # Create instance
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                # Create checklist instance
                instance_id = uuid4()
                await cur.execute("""
                    INSERT INTO checklist_instances 
                    (id, template_id, checklist_date, shift, shift_start, shift_end, status, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                """, [
                    instance_id, template_id, checklist_date, shift.value,
                    shift_start, shift_end, ChecklistStatus.OPEN.value, user_id
                ])
                
                instance = await cur.fetchone()
                
                # Populate instance items from template
                await cur.execute("""
                    INSERT INTO checklist_instance_items (id, instance_id, template_item_id, status)
                    SELECT gen_random_uuid(), %s, cti.id, 'PENDING'
                    FROM checklist_template_items cti
                    WHERE cti.template_id = %s
                    ORDER BY cti.sort_order
                """, [instance_id, template_id])
                
                # Add creator as participant
                if user_id:
                    await cur.execute("""
                        INSERT INTO checklist_participants (instance_id, user_id)
                        VALUES (%s, %s)
                        ON CONFLICT DO NOTHING
                    """, [instance_id, user_id])
                
                # Create handover from previous shift if needed
                await ChecklistService._create_handover_notes(
                    conn, cur, instance_id, checklist_date, shift, user_id
                )
                
                # Log creation event
                await cur.execute("""
                    INSERT INTO ops_events 
                    (event_type, entity_type, entity_id, payload)
                    VALUES (%s, %s, %s, %s)
                """, [
                    'CHECKLIST_CREATED',
                    'CHECKLIST_INSTANCE',
                    instance_id,
                    json.dumps({
                        'shift': shift.value,
                        'date': checklist_date.isoformat(),
                        'created_by': str(user_id),
                        'template_id': str(template_id)
                    })
                ])
                
                await conn.commit()
                
                return await ChecklistService.get_instance_by_id(instance_id)
    
    @staticmethod
    async def _create_handover_notes(conn, cur, instance_id: UUID, checklist_date: date, shift: ShiftType, user_id: UUID):
        """Create automatic handover notes from previous shift"""
        
        # Determine previous shift
        shift_order = [ShiftType.MORNING, ShiftType.AFTERNOON, ShiftType.NIGHT]
        current_index = shift_order.index(shift)
        prev_shift = shift_order[(current_index - 1) % 3]
        prev_date = checklist_date if current_index > 0 else checklist_date - timedelta(days=1)
        
        # Find previous instance
        await cur.execute("""
            SELECT ci.id, ci.status, COUNT(CASE WHEN cii.status = 'FAILED' THEN 1 END) as failed_items
            FROM checklist_instances ci
            LEFT JOIN checklist_instance_items cii ON ci.id = cii.instance_id
            WHERE ci.checklist_date = %s AND ci.shift = %s
            GROUP BY ci.id
            LIMIT 1
        """, [prev_date, prev_shift.value])
        
        prev_instance = await cur.fetchone()
        
        if prev_instance:
            prev_id, prev_status, failed_count = prev_instance
            
            # Only create handover if there were exceptions
            if prev_status in [ChecklistStatus.COMPLETED_WITH_EXCEPTIONS.value, 
                              ChecklistStatus.CLOSED_BY_EXCEPTION.value] or failed_count > 0:
                
                await cur.execute("""
                    INSERT INTO handover_notes 
                    (from_instance_id, content, priority, created_by)
                    VALUES (%s, %s, %s, %s)
                """, [
                    prev_id,
                    f"Automatic handover from {prev_shift.value.lower()} shift. "
                    f"Status: {prev_status}, Failed items: {failed_count}",
                    2 if failed_count == 0 else 3 if failed_count <= 3 else 4,
                    user_id
                ])
    
    @staticmethod
    async def get_instance_by_id(instance_id: UUID) -> Dict:
        """Get checklist instance with all details"""
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                # Get instance
                await cur.execute("""
                    SELECT 
                        ci.*,
                        ct.name as template_name,
                        ct.version as template_version,
                        u1.username as created_by_username,
                        u2.username as closed_by_username,
                        COUNT(DISTINCT cp.user_id) as participant_count
                    FROM checklist_instances ci
                    JOIN checklist_templates ct ON ci.template_id = ct.id
                    LEFT JOIN users u1 ON ci.created_by = u1.id
                    LEFT JOIN users u2 ON ci.closed_by = u2.id
                    LEFT JOIN checklist_participants cp ON ci.id = cp.instance_id
                    WHERE ci.id = %s
                    GROUP BY ci.id, ct.id, u1.id, u2.id
                """, [instance_id])
                
                instance = await cur.fetchone()
                if not instance:
                    raise ValueError(f"Checklist instance {instance_id} not found")
                
                # Get items
                await cur.execute("""
                    SELECT 
                        cii.*,
                        cti.title, cti.description, cti.item_type, cti.is_required,
                        cti.scheduled_time, cti.severity, cti.sort_order,
                        u.username as completed_by_username
                    FROM checklist_instance_items cii
                    JOIN checklist_template_items cti ON cii.template_item_id = cti.id
                    LEFT JOIN users u ON cii.completed_by = u.id
                    WHERE cii.instance_id = %s
                    ORDER BY cti.sort_order
                """, [instance_id])
                
                items = await cur.fetchall()
                
                # Get activities for each item
                item_activities = {}
                for item in items:
                    await cur.execute("""
                        SELECT 
                            cia.*,
                            u.username as user_username
                        FROM checklist_item_activity cia
                        JOIN users u ON cia.user_id = u.id
                        WHERE cia.instance_item_id = %s
                        ORDER BY cia.created_at
                    """, [item[0]])  # item[0] is the item ID
                    
                    activities = await cur.fetchall()
                    item_activities[item[0]] = activities
                
                # Get participants
                await cur.execute("""
                    SELECT u.id, u.username, u.email, u.first_name, u.last_name, r.name as role
                    FROM checklist_participants cp
                    JOIN users u ON cp.user_id = u.id
                    JOIN user_roles ur ON u.id = ur.user_id
                    JOIN roles r ON ur.role_id = r.id
                    WHERE cp.instance_id = %s
                """, [instance_id])
                
                participants = await cur.fetchall()
                
                # Calculate statistics
                total_items = len(items)
                completed_items = sum(1 for item in items if item[3] == ItemStatus.COMPLETED.value)
                completion_percentage = (completed_items / total_items * 100) if total_items > 0 else 0
                
                # Calculate time remaining
                shift_end = instance[5]  # shift_end from instance
                time_remaining = max(0, (shift_end - datetime.now()).total_seconds() / 60)
                
                return {
                    'id': instance[0],
                    'template': {
                        'id': instance[1],
                        'name': instance[9],
                        'version': instance[10]
                    },
                    'checklist_date': instance[2],
                    'shift': instance[3],
                    'shift_start': instance[4],
                    'shift_end': instance[5],
                    'status': instance[6],
                    'created_by': {
                        'id': instance[7],
                        'username': instance[11]
                    } if instance[7] else None,
                    'closed_by': {
                        'id': instance[8],
                        'username': instance[12]
                    } if instance[8] else None,
                    'closed_at': instance[13],
                    'created_at': instance[14],
                    'participant_count': instance[15],
                    'items': [
                        {
                            'id': item[0],
                            'instance_id': item[1],
                            'template_item_id': item[2],
                            'status': item[3],
                            'completed_by': {
                                'id': item[4],
                                'username': item[17]
                            } if item[4] else None,
                            'completed_at': item[5],
                            'skipped_reason': item[6],
                            'failure_reason': item[7],
                            'template_item': {
                                'title': item[8],
                                'description': item[9],
                                'item_type': item[10],
                                'is_required': item[11],
                                'scheduled_time': item[12],
                                'severity': item[13],
                                'sort_order': item[14]
                            },
                            'activities': [
                                {
                                    'id': act[0],
                                    'instance_item_id': act[1],
                                    'user_id': act[2],
                                    'user_username': act[9],
                                    'action': act[3],
                                    'comment': act[4],
                                    'created_at': act[5]
                                }
                                for act in item_activities.get(item[0], [])
                            ]
                        }
                        for item in items
                    ],
                    'participants': [
                        {
                            'id': p[0],
                            'username': p[1],
                            'email': p[2],
                            'first_name': p[3],
                            'last_name': p[4],
                            'role': p[5]
                        }
                        for p in participants
                    ],
                    'statistics': {
                        'total_items': total_items,
                        'completed_items': completed_items,
                        'completion_percentage': round(completion_percentage, 2),
                        'time_remaining_minutes': int(time_remaining)
                    }
                }


# Module-level helper so callers can import `ensure_default_templates` directly
async def ensure_default_templates():
    await ChecklistService.ensure_default_templates()
    
    @staticmethod
    async def update_item_status(
        instance_id: UUID,
        item_id: UUID,
        status: ItemStatus,
        user_id: UUID,
        comment: Optional[str] = None,
        reason: Optional[str] = None
    ) -> Dict:
        """Update checklist item status with validation and logging"""
        
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                # Get current item status
                await cur.execute("""
                    SELECT status, template_item_id
                    FROM checklist_instance_items 
                    WHERE id = %s AND instance_id = %s
                    FOR UPDATE
                """, [item_id, instance_id])
                
                item = await cur.fetchone()
                if not item:
                    raise ValueError(f"Item {item_id} not found in instance {instance_id}")
                
                current_status, template_item_id = item
                
                # Validate state transition
                await ChecklistService._validate_state_transition(
                    conn, cur, 'CHECKLIST_ITEM', current_status, status.value, user_id
                )
                
                # Update item
                update_fields = ['status = %s']
                params = [status.value, item_id]
                
                if status == ItemStatus.COMPLETED:
                    update_fields.extend(['completed_by = %s', 'completed_at = %s'])
                    params.extend([user_id, datetime.now()])
                elif status == ItemStatus.SKIPPED:
                    update_fields.append('skipped_reason = %s')
                    params.append(reason)
                elif status == ItemStatus.FAILED:
                    update_fields.append('failure_reason = %s')
                    params.append(reason)
                
                await cur.execute(f"""
                    UPDATE checklist_instance_items 
                    SET {', '.join(update_fields)}
                    WHERE id = %s
                """, params)
                
                # Log activity
                action_map = {
                    ItemStatus.IN_PROGRESS: ActivityAction.STARTED,
                    ItemStatus.COMPLETED: ActivityAction.COMPLETED,
                    ItemStatus.SKIPPED: ActivityAction.SKIPPED,
                    ItemStatus.FAILED: ActivityAction.ESCALATED
                }
                
                action = action_map.get(status, ActivityAction.COMMENTED)
                
                await cur.execute("""
                    INSERT INTO checklist_item_activity 
                    (instance_item_id, user_id, action, comment)
                    VALUES (%s, %s, %s, %s)
                """, [item_id, user_id, action.value, comment])
                
                # Update checklist instance status if needed
                await ChecklistService._update_instance_status(conn, cur, instance_id, user_id)
                
                # Award gamification points if completed
                if status == ItemStatus.COMPLETED:
                    await ChecklistService._award_completion_points(conn, cur, instance_id, item_id, user_id)
                
                # Log ops event
                await cur.execute("""
                    INSERT INTO ops_events 
                    (event_type, entity_type, entity_id, payload)
                    VALUES (%s, %s, %s, %s)
                """, [
                    'ITEM_STATUS_CHANGED',
                    'CHECKLIST_ITEM',
                    item_id,
                    json.dumps({
                        'from_status': current_status,
                        'to_status': status.value,
                        'user_id': str(user_id),
                        'instance_id': str(instance_id),
                        'comment': comment,
                        'reason': reason
                    })
                ])
                
                await conn.commit()
                
                return await ChecklistService.get_instance_by_id(instance_id)
    
    @staticmethod
    async def _validate_state_transition(conn, cur, entity_type: str, from_status: str, to_status: str, user_id: UUID):
        """Validate state transition based on rules"""
        # This would query the state_transition_rules table
        # For now, implement basic validation
        invalid_transitions = {
            'CHECKLIST_ITEM': {
                'COMPLETED': ['PENDING', 'IN_PROGRESS']  # Can't go back to these
            }
        }
        
        if entity_type in invalid_transitions:
            if from_status == 'COMPLETED' and to_status in invalid_transitions[entity_type]['COMPLETED']:
                raise ValueError(f"Cannot transition from {from_status} to {to_status}")
    
    @staticmethod
    async def _update_instance_status(conn, cur, instance_id: UUID, user_id: UUID):
        """Update instance status based on item completion"""
        await cur.execute("""
            SELECT 
                ci.status,
                COUNT(cii.id) as total_items,
                COUNT(CASE WHEN cii.status = 'COMPLETED' THEN 1 END) as completed_items,
                COUNT(CASE WHEN cti.is_required = TRUE AND cii.status = 'COMPLETED' THEN 1 END) as required_completed,
                COUNT(CASE WHEN cti.is_required = TRUE THEN 1 END) as total_required
            FROM checklist_instances ci
            JOIN checklist_instance_items cii ON ci.id = cii.instance_id
            JOIN checklist_template_items cti ON cii.template_item_id = cti.id
            WHERE ci.id = %s
            GROUP BY ci.id
        """, [instance_id])
        
        stats = await cur.fetchone()
        if not stats:
            return
        
        current_status, total_items, completed_items, required_completed, total_required = stats
        
        # Auto-promote to IN_PROGRESS if first item started and checklist is OPEN
        if current_status == ChecklistStatus.OPEN.value and completed_items == 0:
            # Check if any item is IN_PROGRESS
            await cur.execute("""
                SELECT COUNT(*) FROM checklist_instance_items
                WHERE instance_id = %s AND status = 'IN_PROGRESS'
            """, [instance_id])
            
            in_progress_count = (await cur.fetchone())[0]
            
            if in_progress_count > 0:
                await cur.execute("""
                    UPDATE checklist_instances 
                    SET status = %s 
                    WHERE id = %s AND status = %s
                """, [ChecklistStatus.IN_PROGRESS.value, instance_id, ChecklistStatus.OPEN.value])
        
        # Promote to PENDING_REVIEW if all required items completed
        if total_required > 0 and required_completed == total_required:
            if current_status == ChecklistStatus.IN_PROGRESS.value:
                await cur.execute("""
                    UPDATE checklist_instances 
                    SET status = %s 
                    WHERE id = %s
                """, [ChecklistStatus.PENDING_REVIEW.value, instance_id])
    
    @staticmethod
    async def _award_completion_points(conn, cur, instance_id: UUID, item_id: UUID, user_id: UUID):
        """Award gamification points for item completion"""
        # Get item details for scoring
        await cur.execute("""
            SELECT cti.severity, cti.is_required
            FROM checklist_instance_items cii
            JOIN checklist_template_items cti ON cii.template_item_id = cti.id
            WHERE cii.id = %s
        """, [item_id])
        
        item_details = await cur.fetchone()
        if not item_details:
            return
        
        severity, is_required = item_details
        
        # Calculate points based on severity and requirement
        base_points = 10
        severity_multiplier = severity  # 1-5
        requirement_bonus = 5 if is_required else 0
        
        points = base_points * severity_multiplier + requirement_bonus
        
        # Award points
        await cur.execute("""
            INSERT INTO gamification_scores 
            (user_id, shift_instance_id, points, reason)
            VALUES (%s, %s, %s, %s)
        """, [user_id, instance_id, points, 'ON_TIME_COMPLETION'])
    
    @staticmethod
    async def join_checklist(instance_id: UUID, user_id: UUID) -> Dict:
        """Add user as participant to checklist"""
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                # Check if instance exists and is open
                await cur.execute("""
                    SELECT status FROM checklist_instances WHERE id = %s
                """, [instance_id])
                
                instance = await cur.fetchone()
                if not instance:
                    raise ValueError(f"Checklist instance {instance_id} not found")
                
                if instance[0] not in [ChecklistStatus.OPEN.value, ChecklistStatus.IN_PROGRESS.value]:
                    raise ValueError(f"Cannot join checklist in status: {instance[0]}")
                
                # Add participant
                await cur.execute("""
                    INSERT INTO checklist_participants (instance_id, user_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                    RETURNING *
                """, [instance_id, user_id])
                
                # Update instance status to IN_PROGRESS if it was OPEN
                if instance[0] == ChecklistStatus.OPEN.value:
                    await cur.execute("""
                        UPDATE checklist_instances 
                        SET status = %s 
                        WHERE id = %s AND status = %s
                    """, [ChecklistStatus.IN_PROGRESS.value, instance_id, ChecklistStatus.OPEN.value])
                
                # Log event
                await cur.execute("""
                    INSERT INTO ops_events 
                    (event_type, entity_type, entity_id, payload)
                    VALUES (%s, %s, %s, %s)
                """, [
                    'USER_JOINED_CHECKLIST',
                    'CHECKLIST_INSTANCE',
                    instance_id,
                    json.dumps({
                        'user_id': str(user_id),
                        'instance_id': str(instance_id)
                    })
                ])
                
                await conn.commit()
                
                return await ChecklistService.get_instance_by_id(instance_id)
    
    @staticmethod
    async def create_handover_note(
        from_instance_id: UUID,
        content: str,
        priority: int,
        user_id: UUID,
        to_shift: Optional[ShiftType] = None,
        to_date: Optional[date] = None
    ) -> Dict:
        """Create a handover note for shift transition"""
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                # Get from instance
                await cur.execute("""
                    SELECT checklist_date, shift FROM checklist_instances WHERE id = %s
                """, [from_instance_id])
                
                from_instance = await cur.fetchone()
                if not from_instance:
                    raise ValueError(f"From instance {from_instance_id} not found")
                
                from_date, from_shift = from_instance
                
                # Find to_instance if not specified
                if not to_shift or not to_date:
                    # Determine next shift
                    shift_order = [ShiftType.MORNING, ShiftType.AFTERNOON, ShiftType.NIGHT]
                    current_index = shift_order.index(ShiftType(from_shift))
                    to_shift = shift_order[(current_index + 1) % 3]
                    to_date = from_date if current_index < 2 else from_date + timedelta(days=1)
                
                # Find to_instance
                await cur.execute("""
                    SELECT id FROM checklist_instances 
                    WHERE checklist_date = %s AND shift = %s
                    LIMIT 1
                """, [to_date, to_shift.value])
                
                to_instance = await cur.fetchone()
                to_instance_id = to_instance[0] if to_instance else None
                
                # Create handover note
                await cur.execute("""
                    INSERT INTO handover_notes 
                    (from_instance_id, to_instance_id, content, priority, created_by)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING *
                """, [from_instance_id, to_instance_id, content, priority, user_id])
                
                note = await cur.fetchone()
                
                # Create notification for next shift participants
                if to_instance_id:
                    await cur.execute("""
                        INSERT INTO notifications 
                        (user_id, title, message, related_entity, related_id)
                        SELECT 
                            cp.user_id,
                            'Handover Note Received',
                            %s,
                            'HANDOVER_NOTE',
                            %s
                        FROM checklist_participants cp
                        WHERE cp.instance_id = %s
                    """, [
                        f"New handover note from {from_shift} shift (Priority: {priority})",
                        note[0],  # note id
                        to_instance_id
                    ])
                
                # Log event
                await cur.execute("""
                    INSERT INTO ops_events 
                    (event_type, entity_type, entity_id, payload)
                    VALUES (%s, %s, %s, %s)
                """, [
                    'HANDOVER_NOTE_CREATED',
                    'HANDOVER_NOTE',
                    note[0],
                    json.dumps({
                        'from_instance_id': str(from_instance_id),
                        'to_instance_id': str(to_instance_id) if to_instance_id else None,
                        'priority': priority,
                        'created_by': str(user_id)
                    })
                ])
                
                await conn.commit()
                
                return {
                    'id': note[0],
                    'from_instance_id': note[1],
                    'to_instance_id': note[2],
                    'content': note[3],
                    'priority': note[4],
                    'created_by': user_id,
                    'created_at': note[8]
                }
    
    @staticmethod
    async def get_todays_checklists(user_id: UUID) -> List[Dict]:
        """Get all checklist instances for today that user is involved in"""
        today = date.today()
        
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT DISTINCT ci.*,
                           ct.name as template_name,
                           COUNT(DISTINCT cp.user_id) as participant_count,
                           COUNT(DISTINCT cii.id) as total_items,
                           COUNT(DISTINCT CASE WHEN cii.status = 'COMPLETED' THEN cii.id END) as completed_items,
                           BOOL_OR(cp.user_id = %s) as is_participant
                    FROM checklist_instances ci
                    JOIN checklist_templates ct ON ci.template_id = ct.id
                    LEFT JOIN checklist_participants cp ON ci.id = cp.instance_id
                    LEFT JOIN checklist_instance_items cii ON ci.id = cii.instance_id
                    WHERE ci.checklist_date = %s
                    GROUP BY ci.id, ct.id
                    ORDER BY 
                        CASE ci.shift
                            WHEN 'MORNING' THEN 1
                            WHEN 'AFTERNOON' THEN 2
                            WHEN 'NIGHT' THEN 3
                        END,
                        ci.shift_start
                """, [user_id, today])
                
                instances = await cur.fetchall()
                
                return [
                    {
                        'id': inst[0],
                        'template_id': inst[1],
                        'checklist_date': inst[2],
                        'shift': inst[3],
                        'shift_start': inst[4],
                        'shift_end': inst[5],
                        'status': inst[6],
                        'created_at': inst[14],
                        'template_name': inst[15],
                        'participant_count': inst[16],
                        'total_items': inst[17],
                        'completed_items': inst[18],
                        'completion_percentage': round((inst[18] / inst[17] * 100) if inst[17] > 0 else 0, 1),
                        'is_participant': inst[19],
                        'time_remaining_minutes': max(0, int((inst[5] - datetime.now()).total_seconds() / 60)) if inst[5] else 0
                    }
                    for inst in instances
                ]
    
    @staticmethod
    async def get_shift_performance_metrics(
        start_date: date,
        end_date: date,
        user_id: Optional[UUID] = None
    ) -> List[Dict]:
        """Get performance metrics for shifts in date range"""
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                query = """
                    SELECT 
                        ci.checklist_date,
                        ci.shift,
                        COUNT(DISTINCT ci.id) as total_instances,
                        COUNT(DISTINCT CASE WHEN ci.status = 'COMPLETED' AND ci.closed_at <= ci.shift_end THEN ci.id END) as completed_on_time,
                        COUNT(DISTINCT CASE WHEN ci.status IN ('COMPLETED_WITH_EXCEPTIONS', 'CLOSED_BY_EXCEPTION') THEN ci.id END) as completed_with_exceptions,
                        AVG(EXTRACT(EPOCH FROM (ci.closed_at - ci.shift_start)) / 60) FILTER (WHERE ci.closed_at IS NOT NULL) as avg_completion_minutes,
                        COALESCE(AVG(gs.total_points), 0) as avg_points,
                        COUNT(DISTINCT cp.user_id) / GREATEST(COUNT(DISTINCT ci.id), 1) as avg_participants_per_shift
                    FROM checklist_instances ci
                    LEFT JOIN (
                        SELECT shift_instance_id, SUM(points) as total_points
                        FROM gamification_scores
                        GROUP BY shift_instance_id
                    ) gs ON ci.id = gs.shift_instance_id
                    LEFT JOIN checklist_participants cp ON ci.id = cp.instance_id
                    WHERE ci.checklist_date BETWEEN %s AND %s
                    {user_filter}
                    GROUP BY ci.checklist_date, ci.shift
                    ORDER BY ci.checklist_date DESC, 
                        CASE ci.shift
                            WHEN 'MORNING' THEN 1
                            WHEN 'AFTERNOON' THEN 2
                            WHEN 'NIGHT' THEN 3
                        END
                """
                
                if user_id:
                    query = query.format(user_filter="AND cp.user_id = %s")
                    params = [start_date, end_date, user_id]
                else:
                    query = query.format(user_filter="")
                    params = [start_date, end_date]
                
                await cur.execute(query, params)
                
                rows = await cur.fetchall()
                
                return [
                    {
                        'shift_date': row[0],
                        'shift_type': row[1],
                        'total_instances': row[2],
                        'completed_on_time': row[3],
                        'completed_with_exceptions': row[4],
                        'avg_completion_minutes': round(row[5] or 0, 1),
                        'avg_points': round(row[6], 1),
                        'avg_participants': round(row[7], 1),
                        'on_time_percentage': round((row[3] / row[2] * 100) if row[2] > 0 else 0, 1)
                    }
                    for row in rows
                ]