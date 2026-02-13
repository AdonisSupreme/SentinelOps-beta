# app/checklists/db_service.py
"""
Database-backed Checklist Service

Complete replacement for file-based services.
All templates, instances, items, activities are DB-driven.
Uses state_transition_rules table for validation.
"""

from typing import Dict, List, Optional, Any
from uuid import UUID, uuid4
from datetime import datetime, date, time, timedelta, timezone
import json

from app.db.database import get_connection
from app.core.logging import get_logger
from app.notifications.db_service import NotificationDBService
from app.ops.events import OpsEventLogger

log = get_logger("checklist-db-service")


class ChecklistDBService:
    """Database-backed checklist service - full replacement for file-based logic"""
    
    # =====================================================
    # TEMPLATE MANAGEMENT
    # =====================================================
    
    @staticmethod
    def get_template(template_id: UUID) -> Optional[dict]:
        """Get a checklist template by ID"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, name, description, shift, is_active, version, 
                               created_by, created_at, section_id
                        FROM checklist_templates
                        WHERE id = %s
                    """, (template_id,))
                    
                    row = cur.fetchone()
                    if not row:
                        return None
                    
                    # Get template items
                    cur.execute("""
                        SELECT id, template_id, title, description, item_type, is_required,
                               scheduled_time, notify_before_minutes, severity, sort_order, created_at
                        FROM checklist_template_items
                        WHERE template_id = %s
                        ORDER BY sort_order
                    """, (template_id,))
                    
                    items = []
                    for item_row in cur.fetchall():
                        items.append({
                            'id': str(item_row[0]),
                            'template_id': item_row[1],
                            'title': item_row[2],
                            'description': item_row[3],
                            'item_type': item_row[4],
                            'is_required': item_row[5],
                            'scheduled_time': item_row[6],
                            'notify_before_minutes': item_row[7],
                            'severity': item_row[8],
                            'sort_order': item_row[9],
                            'created_at': item_row[10].isoformat() if item_row[10] else None
                        })
                    
                    return {
                        'id': str(row[0]),
                        'name': row[1],
                        'description': row[2],
                        'shift': row[3],
                        'is_active': row[4],
                        'version': row[5],
                        'created_by': str(row[6]) if row[6] else None,
                        'created_at': row[7].isoformat() if row[7] else None,
                        'section_id': str(row[8]) if row[8] else None,
                        'items': items
                    }
        except Exception as e:
            log.error(f"Failed to get template {template_id}: {e}")
            return None
    
    @staticmethod
    def get_active_template_for_shift(shift: str) -> Optional[dict]:
        """Get the active template for a given shift type"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id FROM checklist_templates
                        WHERE shift = %s AND is_active = TRUE
                        ORDER BY version DESC
                        LIMIT 1
                    """, (shift,))
                    
                    row = cur.fetchone()
                    if not row:
                        log.warning(f"No active template found for shift: {shift}")
                        return None
                    
                    template_id = row[0]
                    return ChecklistDBService.get_template(template_id)
        except Exception as e:
            log.error(f"Failed to get active template for shift {shift}: {e}")
            return None
    
    @staticmethod
    def list_templates(shift: Optional[str] = None, active_only: bool = True, section_id: Optional[str] = None) -> List[dict]:
        """List templates, optionally filtered by shift"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    query = "SELECT id FROM checklist_templates WHERE 1=1"
                    params = []
                    
                    if active_only:
                        query += " AND is_active = TRUE"
                    
                    if shift:
                        query += " AND shift = %s"
                        params.append(shift)

                    # Section scoping: if provided, only return templates for that section
                    if section_id:
                        query += " AND section_id = %s"
                        params.append(section_id)
                    
                    query += " ORDER BY version DESC"
                    
                    cur.execute(query, params)
                    rows = cur.fetchall()
                    
                    templates = []
                    for (template_id,) in rows:
                        template = ChecklistDBService.get_template(template_id)
                        if template:
                            templates.append(template)
                    
                    return templates
        except Exception as e:
            log.error(f"Failed to list templates: {e}")
            return []
    
    # =====================================================
    # INSTANCE MANAGEMENT
    # =====================================================
    
    @staticmethod
    def create_checklist_instance(
        checklist_date: date,
        shift: str,
        created_by: UUID,
        created_by_username: str,
        template_id: Optional[UUID] = None,
        section_id: Optional[str] = None
    ) -> Optional[dict]:
        """
        Create a new checklist instance
        Uses active template for shift if template_id not provided
        """
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Get template (use provided or get active)
                    if not template_id:
                        cur.execute("""
                            SELECT id FROM checklist_templates
                            WHERE shift = %s AND is_active = TRUE
                            ORDER BY version DESC LIMIT 1
                        """, (shift,))
                        
                        row = cur.fetchone()
                        if not row:
                            raise Exception(f"No active template found for shift: {shift}")
                        template_id = UUID(row[0])
                    
                    # Calculate shift times
                    shift_times = {
                        'MORNING': (time(6, 0), time(14, 0)),
                        'AFTERNOON': (time(14, 0), time(22, 0)),
                        'NIGHT': (time(22, 0), time(6, 0))  # Wraps to next day
                    }
                    
                    if shift not in shift_times:
                        raise ValueError(f"Invalid shift type: {shift}")
                    
                    start_time, end_time = shift_times[shift]
                    shift_start = datetime.combine(checklist_date, start_time, tzinfo=timezone.utc)
                    
                    # Handle night shift wrapping to next day
                    if shift == 'NIGHT' and end_time < start_time:
                        shift_end = datetime.combine(
                            checklist_date + timedelta(days=1), 
                            end_time, 
                            tzinfo=timezone.utc
                        )
                    else:
                        shift_end = datetime.combine(checklist_date, end_time, tzinfo=timezone.utc)
                    
                    # Check if instance already exists for this template, date, and shift
                    cur.execute("""
                        SELECT id FROM checklist_instances 
                        WHERE template_id = %s AND checklist_date = %s AND shift = %s
                    """, (template_id, checklist_date, shift))
                    
                    existing_instance = cur.fetchone()
                    if existing_instance:
                        log.info(f"Instance already exists for template {template_id} on {checklist_date} {shift} shift")
                        existing_id = existing_instance[0]
                        instance_data = ChecklistDBService.get_instance(existing_id)
                        if instance_data:
                            return {
                                'id': existing_id,
                                'message': 'Existing instance returned',
                                'instance': instance_data
                            }
                        else:
                            raise ValueError(f"Failed to retrieve existing instance {existing_id}")
                    
                    # Create instance
                    instance_id = uuid4()
                    cur.execute("""
                        INSERT INTO checklist_instances (
                            id, template_id, checklist_date, shift,
                            shift_start, shift_end, status,
                            created_by, created_at, section_id
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        ) RETURNING id, template_id, checklist_date, shift,
                                  shift_start, shift_end, status, created_by, created_at, section_id
                    """, (
                        instance_id, template_id, checklist_date, shift,
                        shift_start, shift_end, 'OPEN',
                        created_by, datetime.now(timezone.utc), section_id
                    ))
                    
                    instance_row = cur.fetchone()
                    
                    # Populate instance items from template
                    cur.execute("""
                        SELECT id FROM checklist_template_items
                        WHERE template_id = %s
                        ORDER BY sort_order
                    """, (template_id,))
                    
                    for (item_id,) in cur.fetchall():
                        item_instance_id = uuid4()
                        cur.execute("""
                            INSERT INTO checklist_instance_items (
                                id, instance_id, template_item_id, status
                            ) VALUES (%s, %s, %s, %s)
                        """, (item_instance_id, instance_id, item_id, 'PENDING'))
                    
                    # CRITICAL FIX: Auto-populate participants from scheduled_shifts
                    # When a checklist instance is created for a date/shift, automatically add all users
                    # who are scheduled to work that shift on that date
                    try:
                        # Get shift_id for this shift name from the shifts table
                        cur.execute("""
                            SELECT id FROM shifts WHERE UPPER(name) = %s LIMIT 1
                        """, (shift,))
                        
                        shift_row = cur.fetchone()
                        if shift_row:
                            shift_id = shift_row[0]
                            
                            # Query all users scheduled for this shift on this date
                            cur.execute("""
                                SELECT DISTINCT ss.user_id
                                FROM scheduled_shifts ss
                                WHERE ss.date = %s AND ss.shift_id = %s
                                AND ss.status != 'CANCELLED'
                            """, (checklist_date, shift_id))
                            
                            scheduled_users = cur.fetchall()
                            
                            # Add all scheduled users as participants (auto-populating the team)
                            for (user_id,) in scheduled_users:
                                try:
                                    cur.execute("""
                                        INSERT INTO checklist_participants (instance_id, user_id)
                                        VALUES (%s, %s)
                                        ON CONFLICT DO NOTHING
                                    """, (instance_id, user_id))
                                except Exception as participant_error:
                                    log.warning(f"Failed to add participant {user_id}: {participant_error}")
                            
                            if scheduled_users:
                                log.info(f"✨ Auto-populated {len(scheduled_users)} scheduled shift participants for instance {instance_id}")
                    except Exception as e:
                        log.warning(f"⚠️  Failed to auto-populate scheduled shift participants: {e}")
                        # Don't fail the whole checklist creation if this step fails
                        pass
                    
                    conn.commit()
                    
                    # Log event
                    OpsEventLogger.log_checklist_created(
                        instance_id=instance_id,
                        checklist_date=str(checklist_date),
                        shift=shift,
                        template_id=template_id,
                        created_by=created_by,
                        created_by_username=created_by_username
                    )
                    
                    log.info(f"✅ Checklist instance created: {instance_id} ({shift} shift on {checklist_date})")
                    
                    instance_data = ChecklistDBService.get_instance(instance_id)
                    if instance_data:
                        return {
                            'id': instance_id,
                            'message': 'New instance created',
                            'instance': instance_data
                        }
                    else:
                        raise ValueError(f"Failed to retrieve newly created instance {instance_id}")
        
        except Exception as e:
            log.error(f"Failed to create checklist instance: {e}")
            raise
    
    @staticmethod
    def get_instance(instance_id: UUID) -> Optional[dict]:
        """Get a checklist instance with all items and activities"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, template_id, checklist_date, shift,
                               shift_start, shift_end, status,
                               created_by, closed_by, closed_at, created_at, section_id
                        FROM checklist_instances
                        WHERE id = %s
                    """, (instance_id,))
                    
                    row = cur.fetchone()
                    if not row:
                        return None
                    
                    # Get items
                    cur.execute("""
                        SELECT cii.id, cii.template_item_id, cii.status,
                               cii.completed_by, cii.completed_at,
                               cii.skipped_reason, cii.failure_reason
                        FROM checklist_instance_items cii
                        LEFT JOIN checklist_template_items cti ON cii.template_item_id = cti.id
                        WHERE cii.instance_id = %s
                        ORDER BY COALESCE(cti.sort_order, 999), cii.template_item_id
                    """, (instance_id,))
                    
                    items = []
                    for item_row in cur.fetchall():
                        item_id = item_row[0]
                        
                        # Get item template details
                        cur.execute("""
                            SELECT title, description, item_type, is_required,
                                   scheduled_time, notify_before_minutes, severity
                            FROM checklist_template_items
                            WHERE id = %s
                        """, (item_row[1],))
                        
                        template_item = cur.fetchone()
                        
                        # Get item activities
                        activities = []
                        if template_item:
                            cur.execute("""
                                SELECT id, action, comment, created_at, user_id
                                FROM checklist_item_activity
                                WHERE instance_item_id = %s
                                ORDER BY created_at DESC
                            """, (item_id,))
                            
                            for activity_row in cur.fetchall():
                                # Fetch user details for the activity
                                cur.execute("""
                                    SELECT id, username, email FROM users WHERE id = %s
                                """, (activity_row[4],))
                                
                                user_row = cur.fetchone()
                                actor = {
                                    'id': str(activity_row[4]),
                                    'username': user_row[1] if user_row else 'Unknown',
                                    'email': user_row[2] if user_row else None
                                } if activity_row[4] else None
                                
                                activities.append({
                                    'id': str(activity_row[0]),
                                    'action': activity_row[1],
                                    'notes': activity_row[2],
                                    'timestamp': activity_row[3].isoformat() if activity_row[3] else None,
                                    'actor': actor
                                })
                        
                        if template_item:
                            items.append({
                                'id': str(item_id),
                                'template_item_id': str(item_row[1]),
                                'title': template_item[0],
                                'description': template_item[1],
                                'item_type': template_item[2],
                                'is_required': template_item[3],
                                'status': item_row[2],
                                'completed_by': str(item_row[3]) if item_row[3] else None,
                                'completed_at': item_row[4].isoformat() if item_row[4] else None,
                                'skipped_reason': item_row[5],
                                'failure_reason': item_row[6],
                                'severity': template_item[6],
                                'activities': activities
                            })
                    
                    # Get participants with user details (id, username, email, role)
                    cur.execute("""
                        SELECT u.id, u.username, u.email, r.name
                        FROM checklist_participants cp
                        JOIN users u ON cp.user_id = u.id
                        LEFT JOIN user_roles ur ON u.id = ur.user_id
                        LEFT JOIN roles r ON ur.role_id = r.id
                        WHERE cp.instance_id = %s
                        ORDER BY u.username
                    """, (instance_id,))

                    participants = []
                    for user_row in cur.fetchall():
                        uid = user_row[0]
                        username = user_row[1] if user_row[1] else 'Unknown User'
                        email = user_row[2] if user_row[2] else ''
                        role = user_row[3] if user_row[3] else 'Member'
                        participants.append({
                            'id': str(uid),
                            'username': username,
                            'email': email,
                            'role': role
                        })
                    
                    # Calculate stats
                    total = len(items)
                    completed = sum(1 for i in items if i['status'] == 'COMPLETED')
                    skipped = sum(1 for i in items if i['status'] == 'SKIPPED')
                    failed = sum(1 for i in items if i['status'] == 'FAILED')
                    
                    completion_rate = (completed / total * 100) if total > 0 else 0
                    
                    return {
                        'id': str(row[0]),
                        'template_id': str(row[1]),
                        'checklist_date': str(row[2]),
                        'shift': row[3],
                        'shift_start': row[4].isoformat() if row[4] else None,
                        'shift_end': row[5].isoformat() if row[5] else None,
                        'status': row[6],
                        'created_by': str(row[7]) if row[7] else None,
                        'closed_by': str(row[8]) if row[8] else None,
                        'closed_at': row[9].isoformat() if row[9] else None,
                        'created_at': row[10].isoformat() if row[10] else None,
                        'section_id': str(row[11]) if row[11] else None,
                        'items': items,
                        'participants': participants,
                        'stats': {
                            'total_items': total,
                            'completed_items': completed,
                            'skipped_items': skipped,
                            'failed_items': failed,
                            'completion_rate': round(completion_rate, 2)
                        }
                    }
        except Exception as e:
            log.error(f"Failed to get instance {instance_id}: {e}")
            return None
    
    @staticmethod
    def get_instances_by_date(checklist_date: date, shift: Optional[str] = None) -> List[dict]:
        """Get all instances for a given date, optionally filtered by shift"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    query = """
                        SELECT id FROM checklist_instances
                        WHERE checklist_date = %s
                    """
                    params = [checklist_date]
                    
                    if shift:
                        query += " AND shift = %s"
                        params.append(shift)
                    
                    query += " ORDER BY shift"
                    
                    cur.execute(query, params)
                    rows = cur.fetchall()
                    
                    instances = []
                    for (instance_id,) in rows:
                        instance = ChecklistDBService.get_instance(instance_id)
                        if instance:
                            instances.append(instance)
                    
                    return instances
        except Exception as e:
            log.error(f"Failed to get instances for {checklist_date}: {e}")
            return []
    
    # =====================================================
    # ITEM STATUS UPDATES
    # =====================================================
    
    @staticmethod
    def update_item_status(
        item_id: UUID,
        new_status: str,
        user_id: UUID,
        username: str,
        reason: Optional[str] = None,
        comment: Optional[str] = None
    ) -> bool:
        """
        Update checklist item status
        Logs activity, creates notifications on skip/fail
        """
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Get current item state
                    cur.execute("""
                        SELECT status, instance_id, template_item_id
                        FROM checklist_instance_items
                        WHERE id = %s
                    """, (item_id,))
                    
                    row = cur.fetchone()
                    if not row:
                        log.warning(f"Item not found: {item_id}")
                        return False
                    
                    old_status, instance_id, template_item_id = row
                    
                    # Get item and instance details for logging
                    cur.execute("""
                        SELECT title FROM checklist_template_items WHERE id = %s
                    """, (template_item_id,))
                    (item_title,) = cur.fetchone()
                    
                    cur.execute("""
                        SELECT checklist_date, shift FROM checklist_instances WHERE id = %s
                    """, (instance_id,))
                    (checklist_date, shift) = cur.fetchone()
                    
                    # Update item
                    if new_status == 'COMPLETED':
                        cur.execute("""
                            UPDATE checklist_instance_items
                            SET status = %s, completed_by = %s, completed_at = %s
                            WHERE id = %s
                        """, (new_status, user_id, datetime.now(timezone.utc), item_id))
                        
                        # Log ops event
                        OpsEventLogger.log_item_completed(
                            item_id=item_id,
                            instance_id=instance_id,
                            item_title=item_title,
                            completed_by=user_id,
                            completed_by_username=username
                        )
                    
                    elif new_status == 'SKIPPED':
                        cur.execute("""
                            UPDATE checklist_instance_items
                            SET status = %s, skipped_reason = %s
                            WHERE id = %s
                        """, (new_status, reason, item_id))
                        
                        # Log ops event
                        OpsEventLogger.log_item_skipped(
                            item_id=item_id,
                            instance_id=instance_id,
                            item_title=item_title,
                            skipped_by=user_id,
                            skipped_by_username=username,
                            reason=reason or "No reason provided"
                        )
                        
                        # Notify admin/manager
                        NotificationDBService.create_item_skipped_notification(
                            item_id=item_id,
                            item_title=item_title,
                            instance_id=instance_id,
                            checklist_date=str(checklist_date),
                            shift=shift,
                            skipped_reason=reason or "No reason provided"
                        )
                    
                    elif new_status == 'FAILED':
                        cur.execute("""
                            UPDATE checklist_instance_items
                            SET status = %s, failure_reason = %s
                            WHERE id = %s
                        """, (new_status, reason, item_id))
                        
                        # Log ops event
                        OpsEventLogger.log_item_failed(
                            item_id=item_id,
                            instance_id=instance_id,
                            item_title=item_title,
                            failed_by=user_id,
                            failed_by_username=username,
                            reason=reason or "No reason provided"
                        )
                        
                        # Notify admin/manager (CRITICAL)
                        NotificationDBService.create_item_failed_notification(
                            item_id=item_id,
                            item_title=item_title,
                            instance_id=instance_id,
                            checklist_date=str(checklist_date),
                            shift=shift,
                            failure_reason=reason or "No reason provided"
                        )
                    
                    else:
                        cur.execute("""
                            UPDATE checklist_instance_items
                            SET status = %s
                            WHERE id = %s
                        """, (new_status, item_id))
                    
                    # Log activity
                    activity_action = {
                        'IN_PROGRESS': 'STARTED',
                        'COMPLETED': 'COMPLETED',
                        'SKIPPED': 'SKIPPED',
                        'FAILED': 'ESCALATED'
                    }.get(new_status, 'COMMENTED')
                    
                    cur.execute("""
                        INSERT INTO checklist_item_activity (
                            id, instance_item_id, user_id, action, comment, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                    """, (
                        uuid4(), item_id, user_id, activity_action, comment,
                        datetime.now(timezone.utc)
                    ))
                    
                    conn.commit()
                    
                    log.info(f"✅ Item {item_id} status updated: {old_status} → {new_status}")
                    # Return a structured result so callers can inspect the updated item
                    return {
                        'item': {
                            'id': item_id,
                            'status': new_status,
                            'previous_status': old_status,
                        }
                    }
        
        except Exception as e:
            log.error(f"Failed to update item {item_id} status: {e}")
            raise
    
    @staticmethod
    def update_instance_status(
        instance_id: UUID,
        new_status: str,
        user_id: UUID,
        username: str,
        comment: Optional[str] = None
    ) -> bool:
        """Update checklist instance status"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Get instance details
                    cur.execute("""
                        SELECT status, checklist_date, shift FROM checklist_instances
                        WHERE id = %s
                    """, (instance_id,))
                    
                    row = cur.fetchone()
                    if not row:
                        return False
                    
                    old_status, checklist_date, shift = row
                    
                    # Update instance
                    if new_status in ['COMPLETED', 'COMPLETED_WITH_EXCEPTIONS']:
                        cur.execute("""
                            UPDATE checklist_instances
                            SET status = %s, closed_by = %s, closed_at = %s
                            WHERE id = %s
                        """, (new_status, user_id, datetime.now(timezone.utc), instance_id))
                        
                        # Calculate stats for notification
                        cur.execute("""
                            SELECT 
                                COUNT(*),
                                SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END)
                            FROM checklist_instance_items
                            WHERE instance_id = %s
                        """, (instance_id,))
                        
                        (total, completed) = cur.fetchone()
                        completed = completed or 0
                        completion_rate = (completed / total * 100) if total > 0 else 0
                        
                        # Log event and notify
                        if new_status == 'COMPLETED':
                            OpsEventLogger.log_checklist_completed(
                                instance_id=instance_id,
                                checklist_date=str(checklist_date),
                                shift=shift,
                                completion_rate=completion_rate,
                                completed_by=user_id,
                                completed_by_username=username,
                                total_items=total,
                                completed_items=completed
                            )
                            
                            NotificationDBService.create_checklist_completed_notification(
                                instance_id=instance_id,
                                checklist_date=str(checklist_date),
                                shift=shift,
                                completion_rate=completion_rate,
                                completed_by_username=username
                            )
                        
                        else:  # COMPLETED_WITH_EXCEPTIONS
                            skipped = sum(1 for _ in cur.execute(
                                "SELECT 1 FROM checklist_instance_items WHERE instance_id = %s AND status = 'SKIPPED'",
                                (instance_id,)
                            ).fetchall())
                            failed = sum(1 for _ in cur.execute(
                                "SELECT 1 FROM checklist_instance_items WHERE instance_id = %s AND status = 'FAILED'",
                                (instance_id,)
                            ).fetchall())
                            
                            OpsEventLogger.log_checklist_completed_with_exceptions(
                                instance_id=instance_id,
                                checklist_date=str(checklist_date),
                                shift=shift,
                                completion_rate=completion_rate,
                                completed_by=user_id,
                                completed_by_username=username,
                                total_items=total,
                                completed_items=completed,
                                skipped_items=skipped,
                                failed_items=failed
                            )
                    else:
                        cur.execute("""
                            UPDATE checklist_instances SET status = %s WHERE id = %s
                        """, (new_status, instance_id))
                    
                    conn.commit()
                    log.info(f"✅ Instance {instance_id} status updated: {old_status} → {new_status}")
                    return True
        
        except Exception as e:
            log.error(f"Failed to update instance {instance_id} status: {e}")
            raise
    
    @staticmethod
    def add_participant(instance_id: UUID, user_id: UUID, username: str) -> bool:
        """Add a user to checklist participants"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Check if already a participant
                    cur.execute("""
                        SELECT id FROM checklist_participants
                        WHERE instance_id = %s AND user_id = %s
                    """, (instance_id, user_id))
                    
                    if cur.fetchone():
                        log.info(f"User {user_id} already a participant in {instance_id}")
                        return True
                    
                    # Add participant
                    cur.execute("""
                        INSERT INTO checklist_participants (
                            id, instance_id, user_id, joined_at
                        ) VALUES (%s, %s, %s, %s)
                    """, (uuid4(), instance_id, user_id, datetime.now(timezone.utc)))
                    
                    # Get instance details for log
                    cur.execute("""
                        SELECT checklist_date, shift FROM checklist_instances WHERE id = %s
                    """, (instance_id,))
                    (checklist_date, shift) = cur.fetchone()
                    
                    conn.commit()
                    
                    # Log event
                    OpsEventLogger.log_participant_joined(
                        instance_id=instance_id,
                        user_id=user_id,
                        username=username,
                        checklist_date=str(checklist_date),
                        shift=shift
                    )
                    
                    log.info(f"✅ User {username} joined checklist {instance_id}")
                    return True
        
        except Exception as e:
            log.error(f"Failed to add participant: {e}")
            return False
