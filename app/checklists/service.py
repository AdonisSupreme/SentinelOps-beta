# app/checklists/service.py
"""
Checklist Architecture Principles

1. Definitions live in files
2. State lives in PostgreSQL
3. Writes confirm, reads explain
4. No runtime joins on definitions
5. Files are immutable, instances are not

Writes confirm. Reads explain. Never mix them.

🏁 One-Line Principle: Writes confirm. Reads explain. Never mix them.
- Mutation endpoints (create/join/update) return minimal confirmations
- Full instance fetch happens only via GET /instances/{id}
- No redundant database calls after mutations
- Consistent 'id' field across all service responses
- Template items are loaded from files, not database
"""

import asyncio
from typing import List, Optional, Dict, Any
from uuid import UUID, uuid4
from datetime import datetime, date, time, timedelta
from enum import Enum
import json

from app.db.database import get_async_connection
from app.core.logging import get_logger
from app.checklists.schemas import (
    ChecklistTemplateCreate, ChecklistTemplateUpdate,
    ChecklistInstanceCreate, ChecklistItemUpdate,
    HandoverNoteCreate, ShiftType, ChecklistStatus,
    ItemStatus, ActivityAction
)
# Fallback template loading to handle missing dependencies
try:
    from app.checklists.template_loader import load_template, get_latest_template
    TEMPLATE_LOADER_AVAILABLE = True
except ImportError as e:
    log.warning(f"Template loader not available: {e}. Using fallback template.")
    TEMPLATE_LOADER_AVAILABLE = False
    
    # Fallback template data
    FALLBACK_TEMPLATE = {
        'version': 1,
        'name': 'Morning Shift – Core Banking & Digital Operations',
        'items': [
            {
                'id': 'uptime_0700',
                'title': 'Share Systems Uptime Status via email & WhatsApp @ 07:00',
                'description': 'ICT & Digital. Refer to night shift handover notes if pending.',
                'item_type': 'ROUTINE',
                'is_required': True,
                'scheduled_time': None,
                'severity': 5,
                'sort_order': 100
            },
            {
                'id': 'idc_services_check',
                'title': 'Check all IDC services are functioning (OF Services)',
                'description': 'Confirm IDC operational status @ 07:00',
                'item_type': 'ROUTINE',
                'is_required': True,
                'scheduled_time': None,
                'severity': 5,
                'sort_order': 200
            },
            {
                'id': 'handover_notes',
                'title': 'Attend to handover notes from previous shift',
                'description': 'Escalate unresolved issues and document at end of checklist',
                'item_type': 'ROUTINE',
                'is_required': True,
                'scheduled_time': None,
                'severity': 4,
                'sort_order': 300
            }
        ]
    }
    
    class FallbackTemplate:
        def __init__(self, data):
            self.version = data['version']
            self.name = data['name']
            self.items = data['items']
    
    def get_latest_template_fallback(shift):
        return FallbackTemplate(FALLBACK_TEMPLATE)

from app.auth.service import get_current_user

log = get_logger("checklists-service")

class ChecklistService:
    """Core checklist business logic service"""
    
    @staticmethod
    async def emit_ops_event_async(
        event_type: str,
        entity_type: str,
        entity_id: UUID,
        payload: Dict[str, Any]
    ):
        """Emit ops event asynchronously in a separate connection"""
        async with get_async_connection() as conn:
            await conn.execute("""
                INSERT INTO ops_events 
                (event_type, entity_type, entity_id, payload)
                VALUES ($1, $2, $3, $4)
            """, event_type, entity_type, entity_id, json.dumps(payload))
    
    @staticmethod
    async def ensure_default_templates():
        """Create default checklist templates if they don't exist"""
        # This would be called on startup
        # Implementation would create MORNING, AFTERNOON, NIGHT templates
        # based on the DOCX content
        pass
    
    @staticmethod
    async def get_active_templates(shift: Optional[str] = None) -> List[Dict]:
        """Get active checklist templates"""
        async with get_async_connection() as conn:
            # Base query without complex JSON aggregation
            base_query = """
                SELECT 
                    ct.id,
                    ct.name,
                    ct.description,
                    ct.shift,
                    ct.is_active,
                    ct.version,
                    ct.created_at,
                    ct.created_by,
                    COUNT(cti.id) as item_count
                FROM checklist_templates ct
                LEFT JOIN checklist_template_items cti ON ct.id = cti.template_id
                WHERE ct.is_active = true
            """

            # Add shift filter if provided
            if shift:
                base_query += " AND ct.shift = $1"

            base_query += " GROUP BY ct.id ORDER BY ct.shift, ct.version DESC"

            # Execute query using asyncpg
            if shift:
                rows = await conn.fetch(base_query, shift)
            else:
                rows = await conn.fetch(base_query)

            # Get items for each template (preview)
            templates = []
            for row in rows:
                template_id = row['id']

                items_query = """
                    SELECT 
                        id,
                        title,
                        item_type,
                        is_required,
                        scheduled_time,
                        severity,
                        sort_order
                    FROM checklist_template_items
                    WHERE template_id = $1
                    ORDER BY sort_order
                    LIMIT 5
                """

                items = await conn.fetch(items_query, template_id)

                templates.append({
                    'id': template_id,
                    'name': row['name'],
                    'description': row['description'],
                    'shift': row['shift'],
                    'is_active': row['is_active'],
                    'version': row['version'],
                    'created_at': row['created_at'],
                    'created_by': row['created_by'],
                    'item_count': row['item_count'],
                    'items_preview': [
                        {
                            'id': item['id'],
                            'title': item['title'],
                            'item_type': item['item_type'],
                            'is_required': item['is_required'],
                            'scheduled_time': item['scheduled_time'].isoformat() if item['scheduled_time'] else None,
                            'severity': item['severity'],
                            'sort_order': item['sort_order']
                        }
                        for item in items
                    ]
                })

            return templates
    @staticmethod
    async def create_checklist_instance(
        checklist_date: date,
        shift: ShiftType,
        template_id: Optional[UUID] = None,  # Deprecated: Use file-based templates
        user_id: UUID = None
    ) -> Dict:
        """Create a new checklist instance for a shift using file-based templates"""
        
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
        
        # Load template from file (NEW ARCHITECTURE) with fallback
        try:
            if TEMPLATE_LOADER_AVAILABLE:
                template = get_latest_template(shift)
                log.info(f"Loaded file-based template: {shift.value} v{template.version} with {len(template.items)} items")
            else:
                template = get_latest_template_fallback(shift)
                log.info(f"Using fallback template: {template.name} with {len(template.items)} items")
        except Exception as e:
            log.error(f"Failed to load template for shift {shift}: {e}")
            # Use fallback as last resort
            if TEMPLATE_LOADER_AVAILABLE:
                template = get_latest_template_fallback(shift)
                log.warning(f"Fallback to hardcoded template due to error: {e}")
            else:
                raise ValueError(f"No template available for shift: {shift}. Error: {e}")
        
        # Create instance and instance items in a single transaction
        async with get_async_connection() as conn:
            # Create checklist instance
            instance_id = uuid4()
            await conn.execute("""
                INSERT INTO checklist_instances 
                (id, template_id, checklist_date, shift, shift_start, shift_end, status, created_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """, instance_id, template_id, checklist_date, shift.value,
                shift_start, shift_end, ChecklistStatus.OPEN.value, user_id)
            
            # Bulk insert instance items using file template data (PERFORMANCE OPTIMIZATION)
            instance_items_data = []
            for item in template.items:
                instance_items_data.append((
                    uuid4(),  # Generate unique ID for each instance item
                    instance_id,
                    item.id,  # Use file-based template item key
                    ItemStatus.PENDING.value
                ))
            
            # Use executemany for bulk insert (PERFORMANCE CRITICAL)
            await conn.executemany("""
                INSERT INTO checklist_instance_items (id, instance_id, template_item_key, status)
                VALUES ($1, $2, $3, $4)
            """, instance_items_data)
            
            # Add creator as participant
            if user_id:
                await conn.execute("""
                    INSERT INTO checklist_participants (instance_id, user_id)
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                """, instance_id, user_id)
            
            # Create handover from previous shift if needed
            await ChecklistService._create_handover_notes(
                conn, instance_id, checklist_date, shift, user_id
            )
            
            # Log to backend file instead of database for detailed tracking
            log.info(f"Checklist instance created: {instance_id} for {shift.value} shift on {checklist_date} by user {user_id}")
            log.info(f"Created {len(template.items)} instance items from file template")
            
            # Return minimal instance data with deferred ops event
            return {
                'instance': {
                    'id': instance_id,  # MANDATORY: Single identifier convention
                    'template_id': template_id,
                    'template_version': template.version,
                    'template_name': template.name,
                    'checklist_date': checklist_date,
                    'shift': shift.value,
                    'shift_start': shift_start,
                    'shift_end': shift_end,
                    'status': ChecklistStatus.OPEN.value,
                    'created_by': user_id,
                    'created_at': datetime.now(),
                    'total_items': len(template.items)
                },
                'ops_event': {
                    'event_type': 'CHECKLIST_CREATED',
                    'entity_type': 'CHECKLIST_INSTANCE',
                    'entity_id': instance_id,
                    'payload': {
                        'shift': shift.value,
                        'date': checklist_date.isoformat(),
                        'created_by': str(user_id),
                        'template_id': str(template_id) if template_id else None,
                        'template_version': template.version,
                        'total_items': len(template.items)
                    }
                }
            }
    
    @staticmethod
    async def _create_handover_notes(conn, instance_id: UUID, checklist_date: date, shift: ShiftType, user_id: UUID):
        """Create automatic handover notes from previous shift"""
        
        # Determine previous shift
        shift_order = [ShiftType.MORNING, ShiftType.AFTERNOON, ShiftType.NIGHT]
        current_index = shift_order.index(shift)
        prev_shift = shift_order[(current_index - 1) % 3]
        prev_date = checklist_date if current_index > 0 else checklist_date - timedelta(days=1)
        
        # Find previous instance
        prev_instance = await conn.fetchrow("""
            SELECT ci.id, ci.status, COUNT(CASE WHEN cii.status = 'FAILED' THEN 1 END) as failed_items
            FROM checklist_instances ci
            LEFT JOIN checklist_instance_items cii ON ci.id = cii.instance_id
            WHERE ci.checklist_date = $1 AND ci.shift = $2
            GROUP BY ci.id
            LIMIT 1
        """, prev_date, prev_shift.value)
        
        if prev_instance:
            prev_id = prev_instance['id']
            prev_status = prev_instance['status'] 
            failed_count = prev_instance['failed_items']
            
            # Only create handover if there were exceptions
            if prev_status in [ChecklistStatus.COMPLETED_WITH_EXCEPTIONS.value, 
                              ChecklistStatus.INCOMPLETE.value] or failed_count > 0:
                
                await conn.execute("""
                    INSERT INTO handover_notes 
                    (from_instance_id, content, priority, created_by)
                    VALUES ($1, $2, $3, $4)
                """,  prev_id,
                    f"Automatic handover from {prev_shift.value.lower()} shift. "
                    f"Status: {prev_status}, Failed items: {failed_count}",
                    2 if failed_count == 0 else 3 if failed_count <= 3 else 4,
                    user_id)
    
    @staticmethod
    async def get_instance_by_id(instance_id: UUID) -> Dict:
        """Get checklist instance with basic details - uses file-based templates"""
        async with get_async_connection() as conn:
            
            # Get basic instance info (no template join)
            instance = await conn.fetchrow("""
                SELECT 
                    ci.id,
                    ci.template_id,
                    ci.checklist_date,
                    ci.shift,
                    ci.shift_start,
                    ci.shift_end,
                    ci.status,
                    ci.created_by,
                    ci.closed_by,
                    ci.closed_at,
                    ci.created_at
                FROM checklist_instances ci
                WHERE ci.id = $1
            """, instance_id)
            
            if not instance:
                raise ValueError(f"Checklist instance {instance_id} not found")
            
            # Load template from file (NEW ARCHITECTURE) with fallback
            try:
                if TEMPLATE_LOADER_AVAILABLE:
                    template = get_latest_template(ShiftType(instance['shift']))
                    log.debug(f"Loaded template for instance {instance_id}: {template.name} v{template.version}")
                else:
                    template = get_latest_template_fallback(ShiftType(instance['shift']))
                    log.debug(f"Using fallback template for instance {instance_id}: {template.name}")
            except Exception as e:
                log.warning(f"Could not load template for instance {instance_id}: {e}")
                # Use fallback as last resort
                if TEMPLATE_LOADER_AVAILABLE:
                    template = get_latest_template_fallback(ShiftType(instance['shift']))
                    log.warning(f"Fallback to hardcoded template for instance {instance_id}: {e}")
                else:
                    # Create empty template structure
                    template = type('Template', (), {
                        'name': 'Unknown Template',
                        'version': 1,
                        'items': []
                    })()
            
            # Get instance items using template_item_key (no template join)
            instance_items = await conn.fetch("""
                SELECT 
                    cii.id,
                    cii.template_item_key,
                    cii.status,
                    cii.completed_by,
                    cii.completed_at,
                    cii.skipped_reason,
                    cii.failure_reason
                FROM checklist_instance_items cii
                WHERE cii.instance_id = $1
                ORDER BY cii.template_item_key
            """, instance_id)
            
            # Get participants info
            participants = await conn.fetch("""
                SELECT u.id, u.username, u.email, u.first_name, u.last_name, r.name as role
                FROM checklist_participants cp
                JOIN users u ON cp.user_id = u.id
                JOIN user_roles ur ON u.id = ur.user_id
                JOIN roles r ON ur.role_id = r.id
                WHERE cp.instance_id = $1
            """, instance_id)
            
            # IN-MEMORY JOIN: Combine instance items with template items
            # Create a mapping of template items by their ID for fast lookup
            template_items_map = {item.id: item for item in template.items}
            
            items = []
            for instance_item in instance_items:
                template_item = template_items_map.get(instance_item['template_item_key'])
                
                if template_item:
                    # Merge instance data with template data
                    items.append({
                        'id': instance_item['id'],
                        'instance_id': instance_id,
                        'template_item_key': instance_item['template_item_key'],
                        'status': instance_item['status'],
                        'completed_by': instance_item['completed_by'],
                        'completed_at': instance_item['completed_at'],
                        'skipped_reason': instance_item['skipped_reason'],
                        'failure_reason': instance_item['failure_reason'],
                        'template_item': {
                            'id': template_item.id,
                            'title': template_item.title,
                            'description': template_item.description,
                            'item_type': template_item.item_type.value,
                            'is_required': template_item.is_required,
                            'scheduled_time': template_item.scheduled_time.isoformat() if template_item.scheduled_time else None,
                            'severity': template_item.severity,
                            'sort_order': template_item.sort_order
                        }
                    })
                else:
                    # Template item not found - create fallback entry
                    log.warning(f"Template item {instance_item['template_item_key']} not found in template for instance {instance_id}")
                    items.append({
                        'id': instance_item['id'],
                        'instance_id': instance_id,
                        'template_item_key': instance_item['template_item_key'],
                        'status': instance_item['status'],
                        'completed_by': instance_item['completed_by'],
                        'completed_at': instance_item['completed_at'],
                        'skipped_reason': instance_item['skipped_reason'],
                        'failure_reason': instance_item['failure_reason'],
                        'template_item': {
                            'id': instance_item['template_item_key'],
                            'title': f'Unknown Item ({instance_item["template_item_key"]})',
                            'description': 'Template item not found',
                            'item_type': 'ROUTINE',
                            'is_required': True,
                            'scheduled_time': None,
                            'severity': 1,
                            'sort_order': 999
                        }
                    })
            
            # Sort by template sort_order
            items.sort(key=lambda x: x['template_item']['sort_order'])
            
            # Calculate statistics
            total_items = len(items)
            completed_items = sum(1 for item in items if item['status'] == ItemStatus.COMPLETED.value)
            completion_percentage = (completed_items / total_items * 100) if total_items > 0 else 0
            
            # Calculate time remaining
            time_remaining = max(0, (instance['shift_end'] - datetime.now()).total_seconds() / 60)
            
            return {
                'id': instance['id'],
                'template': {
                    'id': instance['template_id'],
                    'name': template.name,
                    'version': template.version
                },
                'checklist_date': instance['checklist_date'],
                'shift': instance['shift'],
                'shift_start': instance['shift_start'],
                'shift_end': instance['shift_end'],
                'status': instance['status'],
                'created_by': instance['created_by'],
                'closed_by': instance['closed_by'],
                'closed_at': instance['closed_at'],
                'created_at': instance['created_at'],
                'items': items,
                'participants': [
                    {
                        'id': p['id'],
                        'username': p['username'],
                        'email': p['email'],
                        'first_name': p['first_name'],
                        'last_name': p['last_name'],
                        'role': p['role']
                    }
                    for p in participants
                ],
                'statistics': {
                    'total_items': total_items,
                    'completed_items': completed_items,
                    'completion_percentage': round(completion_percentage, 1),
                    'time_remaining_minutes': int(time_remaining)
                }
            }

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
        
        async with get_async_connection() as conn:
            
            # Get current item status with proper asyncpg fetchrow
            item = await conn.fetchrow("""
                SELECT status, template_item_id
                FROM checklist_instance_items 
                WHERE id = $1 AND instance_id = $2
                FOR UPDATE
            """, item_id, instance_id)
            
            if not item:
                raise ValueError(f"Item {item_id} not found in instance {instance_id}")
            
            current_status, template_item_id = item['status'], item['template_item_id']
            
            # Validate state transition
            await ChecklistService._validate_state_transition(
                conn, None, 'CHECKLIST_ITEM', current_status, status.value, user_id
            )
            
            # Update item
            update_fields = ['status = $1']
            params = [status.value, item_id]
            
            if status == ItemStatus.COMPLETED:
                update_fields.extend(['completed_by = $2', 'completed_at = $3'])
                params.extend([user_id, datetime.now()])
            elif status == ItemStatus.SKIPPED:
                update_fields.append('skipped_reason = $2')
                params.append(reason)
            elif status == ItemStatus.FAILED:
                update_fields.append('failure_reason = $2')
                params.append(reason)
            
            await conn.execute(f"""
                UPDATE checklist_instance_items 
                SET {', '.join(update_fields)}
                WHERE id = ${len(params)}
            """, *params)
            
            # Update checklist instance status if needed
            await ChecklistService._update_instance_status(conn, None, instance_id, user_id)
            
            # Award gamification points if completed
            if status == ItemStatus.COMPLETED:
                await ChecklistService._award_completion_points(conn, None, instance_id, item_id, user_id)
            
            # Log to backend file instead of database for detailed tracking
            log.info(f"Item {item_id} status updated to {status.value} in instance {instance_id} by user {user_id}")
            
            # Remove manual commit - async with handles it
            
            # Return minimal update confirmation with deferred ops event
            return {
                'instance': {
                    'id': instance_id,  # MANDATORY: Single identifier convention
                    'item_id': item_id,
                    'status': status.value,
                    'updated_by': user_id,
                    'updated_at': datetime.now()
                },
                'ops_event': {
                    'event_type': 'ITEM_STATUS_CHANGED',
                    'entity_type': 'CHECKLIST_ITEM',
                    'entity_id': item_id,
                    'payload': {
                        'from_status': current_status,
                        'to_status': status.value,
                        'user_id': str(user_id),
                        'instance_id': str(instance_id),
                        'comment': comment,
                        'reason': reason
                    }
                }
            }
    
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
        stats = await conn.fetchrow("""
            SELECT 
                ci.status,
                COUNT(cii.id) as total_items,
                COUNT(CASE WHEN cii.status = 'COMPLETED' THEN 1 END) as completed_items,
                COUNT(CASE WHEN cti.is_required = TRUE AND cii.status = 'COMPLETED' THEN 1 END) as required_completed,
                COUNT(CASE WHEN cti.is_required = TRUE THEN 1 END) as total_required
            FROM checklist_instances ci
            JOIN checklist_instance_items cii ON ci.id = cii.instance_id
            JOIN checklist_template_items cti ON cii.template_item_id = cti.id
            WHERE ci.id = $1
            GROUP BY ci.id
        """, instance_id)
        
        if not stats:
            return
        
        current_status = stats['status']
        total_items = stats['total_items']
        completed_items = stats['completed_items']
        required_completed = stats['required_completed']
        total_required = stats['total_required']
        
        # Auto-promote to IN_PROGRESS if first item started and checklist is OPEN
        if current_status == ChecklistStatus.OPEN.value and completed_items == 0:
            # Check if any item is IN_PROGRESS
            in_progress_count = await conn.fetchval("""
                SELECT COUNT(*) FROM checklist_instance_items
                WHERE instance_id = $1 AND status = 'IN_PROGRESS'
            """, instance_id)
            
            if in_progress_count > 0:
                await conn.execute("""
                    UPDATE checklist_instances 
                    SET status = $1 
                    WHERE id = $2 AND status = $3
                """, ChecklistStatus.IN_PROGRESS.value, instance_id, ChecklistStatus.OPEN.value)
        
        # Promote to PENDING_REVIEW if all required items completed
        if total_required > 0 and required_completed == total_required:
            if current_status == ChecklistStatus.IN_PROGRESS.value:
                await conn.execute("""
                    UPDATE checklist_instances 
                    SET status = $1 
                    WHERE id = $2
                """, ChecklistStatus.PENDING_REVIEW.value, instance_id)
    
    @staticmethod
    async def _award_completion_points(conn, cur, instance_id: UUID, item_id: UUID, user_id: UUID):
        """Award gamification points for item completion"""
        # Get item details for scoring
        item_details = await conn.fetchrow("""
            SELECT cti.severity, cti.is_required
            FROM checklist_instance_items cii
            JOIN checklist_template_items cti ON cii.template_item_id = cti.id
            WHERE cii.id = $1
        """, item_id)
        
        if not item_details:
            return
        
        severity = item_details['severity']
        is_required = item_details['is_required']
        
        # Calculate points based on severity and requirement
        base_points = 10
        severity_multiplier = severity  # 1-5
        requirement_bonus = 5 if is_required else 0
        
        points = base_points * severity_multiplier + requirement_bonus
        
        # Award points
        await conn.execute("""
            INSERT INTO gamification_scores 
            (user_id, shift_instance_id, points, reason)
            VALUES ($1, $2, $3, $4)
        """, user_id, instance_id, points, 'ON_TIME_COMPLETION')
    
    @staticmethod
    async def join_checklist(instance_id: UUID, user_id: UUID) -> Dict:
        """Add user as participant to checklist"""
        async with get_async_connection() as conn:
            
            # Check if instance exists and is open
            instance = await conn.fetchrow("""
                SELECT status FROM checklist_instances WHERE id = $1
            """, instance_id)
            
            if not instance:
                raise ValueError(f"Checklist instance {instance_id} not found")
            
            if instance['status'] not in [ChecklistStatus.OPEN.value, ChecklistStatus.IN_PROGRESS.value]:
                raise ValueError(f"Cannot join checklist in status: {instance['status']}")
            
            # Add participant
            await conn.execute("""
                INSERT INTO checklist_participants (instance_id, user_id)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
            """, instance_id, user_id)
            
            # Update instance status to IN_PROGRESS if it was OPEN
            if instance['status'] == ChecklistStatus.OPEN.value:
                await conn.execute("""
                    UPDATE checklist_instances 
                    SET status = $1 
                    WHERE id = $2 AND status = $3
                """, ChecklistStatus.IN_PROGRESS.value, instance_id, ChecklistStatus.OPEN.value)
            
            # Log to backend file instead of database for detailed tracking
            log.info(f"User {user_id} joined checklist instance {instance_id}")
            
            # Remove manual commit - async with handles it
            
            # Return minimal join confirmation with deferred ops event
            return {
                'instance': {
                    'id': instance_id,  # MANDATORY: Single identifier convention
                    'user_id': user_id,
                    'joined_at': datetime.now()
                },
                'ops_event': {
                    'event_type': 'USER_JOINED_CHECKLIST',
                    'entity_type': 'CHECKLIST_INSTANCE',
                    'entity_id': instance_id,
                    'payload': {
                        'user_id': str(user_id),
                        'instance_id': str(instance_id)
                    }
                }
            }
    
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
        async with get_async_connection() as conn:
            
            # Get from instance with proper asyncpg fetchrow
            from_instance = await conn.fetchrow("""
                SELECT checklist_date, shift FROM checklist_instances WHERE id = $1
            """, from_instance_id)
            
            if not from_instance:
                raise ValueError(f"From instance {from_instance_id} not found")
            
            from_date = from_instance['checklist_date']
            from_shift = from_instance['shift']
            
            # Find to_instance if not specified
            if not to_shift or not to_date:
                # Determine next shift
                shift_order = [ShiftType.MORNING, ShiftType.AFTERNOON, ShiftType.NIGHT]
                current_index = shift_order.index(ShiftType(from_shift))
                to_shift = shift_order[(current_index + 1) % 3]
                to_date = from_date if current_index < 2 else from_date + timedelta(days=1)
            
            # Find to_instance with proper asyncpg fetch
            to_instance = await conn.fetchrow("""
                SELECT id FROM checklist_instances 
                WHERE checklist_date = $1 AND shift = $2
                LIMIT 1
            """, to_date, to_shift.value)
            
            to_instance_id = to_instance['id'] if to_instance else None
            
            # Create handover note with proper asyncpg
            note = await conn.fetchrow("""
                INSERT INTO handover_notes 
                (from_instance_id, to_instance_id, content, priority, created_by)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING *
            """, from_instance_id, to_instance_id, content, priority, user_id)
            
            # Create notification for next shift participants
            if to_instance_id:
                await conn.execute("""
                    INSERT INTO notifications 
                    (user_id, title, message, related_entity, related_id)
                    SELECT 
                        cp.user_id,
                        'Handover Note Received',
                        $1,
                        'HANDOVER_NOTE',
                        $2
                    FROM checklist_participants cp
                    WHERE cp.instance_id = $3
                """, [
                    f"New handover note from {from_shift} shift (Priority: {priority})",
                    note['id'],  # note id
                    to_instance_id
                ])
            
            # Remove manual commit - async with handles it
            
            # Return handover note with deferred ops event
            return {
                'instance': {
                    'id': note['id'],
                    'from_instance_id': note['from_instance_id'],
                    'to_instance_id': note['to_instance_id'],
                    'content': note['content'],
                    'priority': note['priority'],
                    'created_by': user_id,
                    'created_at': note['created_at']
                },
                'ops_event': {
                    'event_type': 'HANDOVER_NOTE_CREATED',
                    'entity_type': 'HANDOVER_NOTE',
                    'entity_id': note['id'],
                    'payload': {
                        'from_instance_id': str(from_instance_id),
                        'to_instance_id': str(to_instance_id) if to_instance_id else None,
                        'priority': priority,
                        'created_by': str(user_id)
                    }
                }
            }
    
    @staticmethod
    async def get_todays_checklists(user_id: UUID) -> List[Dict]:
        """Get all checklist instances for today that user is involved in"""
        today = date.today()
        
        async with get_async_connection() as conn:
            instances = await conn.fetch("""
                SELECT ci.id, ci.template_id, ci.checklist_date, ci.shift, 
                       ci.shift_start, ci.shift_end, ci.status, ci.created_at,
                       ct.name as template_name,
                       COUNT(DISTINCT cp.user_id) as participant_count,
                       COUNT(DISTINCT cii.id) as total_items,
                       COUNT(DISTINCT CASE WHEN cii.status = 'COMPLETED' THEN cii.id END) as completed_items,
                       BOOL_OR(cp.user_id = $1) as is_participant
                FROM checklist_instances ci
                JOIN checklist_templates ct ON ci.template_id = ct.id
                LEFT JOIN checklist_participants cp ON ci.id = cp.instance_id
                LEFT JOIN checklist_instance_items cii ON ci.id = cii.instance_id
                WHERE ci.checklist_date = $2
                GROUP BY ci.id, ct.id, ci.template_id, ci.checklist_date, ci.shift, 
                         ci.shift_start, ci.shift_end, ci.status, ci.created_at, ct.name
                ORDER BY 
                    CASE ci.shift
                        WHEN 'MORNING' THEN 1
                        WHEN 'AFTERNOON' THEN 2
                        WHEN 'NIGHT' THEN 3
                    END,
                    ci.shift_start
            """, user_id, today)
            
            return [
                {
                    'id': inst['id'],
                    'template_id': inst['template_id'],
                    'checklist_date': inst['checklist_date'],
                    'shift': inst['shift'],
                    'shift_start': inst['shift_start'],
                    'shift_end': inst['shift_end'],
                    'status': inst['status'],
                    'created_at': inst['created_at'],
                    'template_name': inst['template_name'],
                    'participant_count': inst['participant_count'],
                    'total_items': inst['total_items'],
                    'completed_items': inst['completed_items'],
                    'completion_percentage': round((inst['completed_items'] / inst['total_items'] * 100) if inst['total_items'] > 0 else 0, 1),
                    'is_participant': inst['is_participant'],
                    'time_remaining_minutes': max(0, int((inst['shift_end'] - datetime.now()).total_seconds() / 60)) if inst['shift_end'] else 0
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
        async with get_async_connection() as conn:
            
                query = """
                    SELECT 
                        ci.checklist_date,
                        ci.shift,
                        COUNT(DISTINCT ci.id) as total_instances,
                        COUNT(DISTINCT CASE WHEN ci.status = 'COMPLETED' AND ci.closed_at <= ci.shift_end THEN ci.id END) as completed_on_time,
                        COUNT(DISTINCT CASE WHEN ci.status IN ('COMPLETED_WITH_EXCEPTIONS', 'INCOMPLETE') THEN ci.id END) as completed_with_exceptions,
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
                    WHERE ci.checklist_date BETWEEN $1 AND $2
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
                    query = query.format(user_filter="AND cp.user_id = $3")
                    params = [start_date, end_date, user_id]
                else:
                    query = query.format(user_filter="")
                    params = [start_date, end_date]
                
                rows = await conn.fetch(query, *params)
                
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


# Module-level helper so callers can import `ensure_default_templates` directly
async def ensure_default_templates():
    await ChecklistService.ensure_default_templates()
