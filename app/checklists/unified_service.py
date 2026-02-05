# app/checklists/unified_service.py
"""
Unified file-based checklist service
Replaces database-dependent service.py with file-based storage
Maintains the same interface for compatibility with existing routers
"""

import json
from datetime import datetime, date, time, timedelta
from typing import Dict, List, Optional, Any
from uuid import UUID, uuid4
from pathlib import Path

from app.checklists.instance_storage import (
    save_instance, load_instance, update_instance, 
    update_item_status, add_participant, list_instances,
    get_today_instances, join_instance
)
from app.checklists.user_service import UserService

# Simple fallback logger to avoid dependency issues
class SimpleLogger:
    def __init__(self, name):
        self.name = name
    def debug(self, msg): print(f"DEBUG: {msg}")
    def info(self, msg): print(f"INFO: {msg}")
    def warning(self, msg): print(f"WARNING: {msg}")
    def error(self, msg): print(f"ERROR: {msg}")

log = SimpleLogger("unified-service")

class UnifiedChecklistService:
    """Unified file-based checklist service - maintains same interface as database service"""
    
    @staticmethod
    async def create_checklist_instance(
        checklist_date: date,
        shift: str,
        template_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None
    ) -> Dict[str, Any]:
        """Create a new checklist instance using file-based storage"""
        try:
            # Load template from file
            template = UnifiedChecklistService._load_template_from_file(shift)
            
            # Calculate shift times
            shift_times = {
                'MORNING': {'start': time(7, 0), 'end': time(15, 0)},
                'AFTERNOON': {'start': time(15, 0), 'end': time(23, 0)},
                'NIGHT': {'start': time(23, 0), 'end': time(7, 0)}
            }
            
            shift_config = shift_times.get(shift, shift_times['MORNING'])
            shift_start = datetime.combine(checklist_date, shift_config['start'])
            shift_end = datetime.combine(
                checklist_date + timedelta(days=1) if shift == 'NIGHT' and shift_config['end'] < shift_config['start'] else checklist_date,
                shift_config['end']
            )
            
            # Generate instance ID
            instance_id = uuid4()
            
            # Create instance items from template
            instance_items = []
            for item in template['items']:
                instance_items.append({
                    'id': str(uuid4()),
                    'instance_id': str(instance_id),
                    'template_item_key': item['id'],
                    'status': 'PENDING',
                    'created_at': datetime.now().isoformat(),
                    'template_item': {
                        **item,
                        'id': item['id'],
                        'template_id': str(uuid4()),
                        'created_at': datetime.now().isoformat()
                    },
                    'item': {
                        'id': item['id'],
                        'title': item['title'],
                        'description': item.get('description', ''),
                        'item_type': item['item_type'],
                        'is_required': item['is_required'],
                        'scheduled_time': item.get('scheduled_time'),
                        'notify_before_minutes': item.get('notify_before_minutes'),
                        'severity': item.get('severity', 1),
                        'sort_order': item.get('sort_order', 0)
                    },
                    'completed_by': None,
                    'completed_at': None,
                    'skipped_reason': None,
                    'failure_reason': None,
                    'notes': None,
                    'activities': []
                })
            
            # Create instance data
            instance_data = {
                'id': str(instance_id),
                'template_id': str(template_id) if template_id else None,
                'template_version': template['version'],
                'checklist_date': checklist_date.isoformat(),
                'shift': shift,
                'shift_start': shift_start.isoformat(),
                'shift_end': shift_end.isoformat(),
                'status': 'OPEN',
                'created_by': str(user_id) if user_id else None,
                'created_at': datetime.now().isoformat(),
                'items': instance_items,
                'participants': [],
                'notes': [],
                'attachments': [],
                'exceptions': [],
                'handover_notes': []
            }
            
            # Add creator as participant if provided
            if user_id:
                instance_data['participants'].append({
                    'user_id': str(user_id),
                    'joined_at': datetime.now().isoformat()
                })
            
            # Save instance to file
            if save_instance(instance_data):
                log.info(f"Created checklist instance {instance_id} for {shift} shift on {checklist_date}")
                
                return {
                    'instance': {
                        'id': instance_id,
                        'template_id': template_id,
                        'checklist_date': checklist_date.isoformat(),
                        'shift': shift,
                        'status': 'OPEN',
                        'created_by': user_id
                    },
                    'ops_event': {
                        'event_type': 'CHECKLIST_INSTANCE_CREATED',
                        'entity_type': 'CHECKLIST_INSTANCE',
                        'entity_id': instance_id,
                        'payload': {
                            'template_id': str(template_id) if template_id else None,
                            'checklist_date': checklist_date.isoformat(),
                            'shift': shift,
                            'user_id': str(user_id) if user_id else None
                        }
                    }
                }
            else:
                raise Exception("Failed to save checklist instance")
                
        except Exception as e:
            log.error(f"Failed to create checklist instance: {e}")
            raise Exception(f"Failed to create checklist instance: {e}")
    
    @staticmethod
    async def get_instance_by_id(instance_id: UUID) -> Dict[str, Any]:
        """Get checklist instance by ID using file storage"""
        try:
            instance_data = load_instance(instance_id)
            
            if not instance_data:
                raise ValueError(f"Checklist instance {instance_id} not found")
            
            # Convert to match expected format from database service
            return UnifiedChecklistService._format_instance_response(instance_data)
            
        except Exception as e:
            log.error(f"Error getting instance by ID: {e}")
            raise ValueError(f"Failed to get instance: {e}")
    
    @staticmethod
    async def update_item_status(
        instance_id: UUID,
        item_id: UUID,
        status: str,
        user_id: Optional[UUID] = None,
        comment: Optional[str] = None,
        reason: Optional[str] = None,
        action_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """Update checklist item status using file storage with state validation"""
        try:
            # Get current instance to find the item and current status
            instance_data = load_instance(instance_id)
            if not instance_data:
                raise ValueError(f"Checklist instance {instance_id} not found")
            
            # Find the item and get current status
            current_item = None
            for item in instance_data.get('items', []):
                if item.get('id') == str(item_id) or item.get('template_item_key') == str(item_id):
                    current_item = item
                    break
            
            if not current_item:
                raise ValueError(f"Item {item_id} not found in instance {instance_id}")
            
            current_status = current_item.get('status')
            
            # Validate state transition using state machine
            from app.checklists.state_machine import ITEM_TRANSITIONS, ItemStatus
            
            # Check if transition is allowed
            allowed_transitions = ITEM_TRANSITIONS.get(current_status, [])
            transition_allowed = any(rule.to_status == status for rule in allowed_transitions)
            
            if not transition_allowed:
                raise ValueError(f"Invalid state transition from {current_status} to {status}")
            
            # Check if reason is required for this transition
            transition_rule = next(
                (rule for rule in allowed_transitions if rule.to_status == status), 
                None
            )
            
            if transition_rule and transition_rule.requires_reason and not reason and not comment:
                raise ValueError(f"Transition from {current_status} to {status} requires a reason")
            
            # Update the item status using enhanced file storage
            success = update_item_status(
                instance_id=instance_id,
                item_id=str(item_id),
                status=status,
                user_id=user_id,
                comment=comment,
                reason=reason,
                action_type=action_type,
                metadata=metadata,
                notes=notes
            )
            
            if success:
                log.info(f"Updated item {item_id} status to {status} in instance {instance_id}")
                
                return {
                    'instance': {
                        'id': instance_id,
                        'item_id': item_id,
                        'status': status,
                        'updated_by': user_id,
                        'updated_at': datetime.now(),
                        'previous_status': current_status
                    },
                    'ops_event': {
                        'event_type': 'ITEM_STATUS_CHANGED',
                        'entity_type': 'CHECKLIST_ITEM',
                        'entity_id': item_id,
                        'payload': {
                            'from_status': current_status,
                            'to_status': status,
                            'user_id': str(user_id) if user_id else None,
                            'instance_id': str(instance_id),
                            'comment': comment,
                            'reason': reason,
                            'action_type': action_type,
                            'metadata': metadata,
                            'notes': notes
                        }
                    }
                }
            else:
                raise Exception("Failed to update item status")
                
        except Exception as e:
            log.error(f"Failed to update item status: {e}")
            raise ValueError(f"Failed to update item status: {e}")
    
    @staticmethod
    async def complete_instance(
        instance_id: UUID,
        user_id: UUID,
        with_exceptions: bool = False
    ) -> Dict[str, Any]:
        """
        Complete a checklist instance (admin/supervisor action).
        
        Args:
            instance_id: The checklist instance ID
            user_id: The user completing the checklist
            with_exceptions: If True, marks as COMPLETED_WITH_EXCEPTIONS when not 100% done
            
        Returns:
            Dict with instance data and ops event
        """
        try:
            # Load instance
            instance_data = load_instance(instance_id)
            if not instance_data:
                raise ValueError(f"Checklist instance {instance_id} not found")
            
            # Calculate completion stats
            items = instance_data.get('items', [])
            total_items = len(items)
            completed_items = sum(1 for item in items if item.get('status') == 'COMPLETED')
            skipped_items = sum(1 for item in items if item.get('status') == 'SKIPPED')
            failed_items = sum(1 for item in items if item.get('status') == 'FAILED')
            
            completion_percentage = (completed_items / total_items * 100) if total_items > 0 else 0
            has_exceptions = skipped_items > 0 or failed_items > 0 or completion_percentage < 100
            
            # Determine final status
            if has_exceptions and with_exceptions:
                final_status = 'COMPLETED_WITH_EXCEPTIONS'
            elif completion_percentage == 100:
                final_status = 'COMPLETED'
            elif with_exceptions:
                # Allow completion with exceptions even if not all items done
                final_status = 'COMPLETED_WITH_EXCEPTIONS'
            else:
                raise ValueError(f"Cannot complete checklist: only {completion_percentage:.1f}% complete. Use with_exceptions=True to force completion.")
            
            # Update instance
            from app.checklists.user_service import UserService
            user_info = UserService.create_user_info(user_id=user_id)
            
            instance_data['status'] = final_status
            instance_data['closed_at'] = datetime.now().isoformat()
            instance_data['closed_by'] = {
                'id': str(user_info['id']),
                'username': user_info['username'],
                'email': user_info.get('email', ''),
                'first_name': user_info.get('first_name', ''),
                'last_name': user_info.get('last_name', ''),
                'role': user_info.get('role', 'supervisor')
            }
            instance_data['updated_at'] = datetime.now().isoformat()
            
            # Update statistics
            if 'statistics' not in instance_data:
                instance_data['statistics'] = {}
            instance_data['statistics']['completion_percentage'] = round(completion_percentage, 1)
            instance_data['statistics']['completed_items'] = completed_items
            instance_data['statistics']['skipped_items'] = skipped_items
            instance_data['statistics']['failed_items'] = failed_items
            
            # Save instance
            if save_instance(instance_data):
                log.info(f"Completed checklist instance {instance_id} with status {final_status}")
                
                return {
                    'instance': UnifiedChecklistService._format_instance_response(instance_data),
                    'ops_event': {
                        'event_type': 'CHECKLIST_COMPLETED',
                        'entity_type': 'CHECKLIST_INSTANCE',
                        'entity_id': str(instance_id),
                        'payload': {
                            'instance_id': str(instance_id),
                            'completed_by': str(user_id),
                            'status': final_status,
                            'completion_percentage': completion_percentage,
                            'has_exceptions': has_exceptions,
                            'completed_items': completed_items,
                            'total_items': total_items,
                            'skipped_items': skipped_items,
                            'failed_items': failed_items
                        }
                    }
                }
            else:
                raise Exception("Failed to save completed checklist")
                
        except Exception as e:
            log.error(f"Failed to complete checklist instance: {e}")
            raise ValueError(f"Failed to complete checklist: {e}")
    
    @staticmethod
    async def get_todays_checklists(user_id: Optional[UUID] = None, shift: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get today's checklists using file storage"""
        try:
            instances = get_today_instances(user_id=user_id, shift=shift)
            
            # Format instances to match expected response format
            formatted_instances = []
            for instance in instances:
                formatted_instance = UnifiedChecklistService._format_instance_response(instance)
                formatted_instances.append(formatted_instance)
            
            return formatted_instances
            
        except Exception as e:
            log.error(f"Failed to get today's checklists: {e}")
            return []
    
    @staticmethod
    async def join_checklist(instance_id: UUID, user_id: UUID, user_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Join a checklist instance using file storage"""
        try:
            success = join_instance(instance_id, user_id, user_info)
            
            if success:
                log.info(f"User {user_id} joined checklist instance {instance_id}")
                
                return {
                    'instance': {
                        'id': instance_id,
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
            else:
                raise Exception("Failed to join checklist")
                
        except Exception as e:
            log.error(f"Failed to join checklist: {e}")
            raise ValueError(f"Failed to join checklist: {e}")
    
    @staticmethod
    def _format_instance_response(instance_data: Dict[str, Any]) -> Dict[str, Any]:
        """Format instance data to match expected response format"""
        try:
            # Ensure ID is a string for frontend compatibility
            if 'id' in instance_data:
                instance_data['id'] = str(instance_data['id'])
            
            # Add missing template object if needed
            if 'template' not in instance_data:
                # Load template to create template object
                template = UnifiedChecklistService._load_template_from_file(instance_data['shift'])
                instance_data['template'] = {
                    "id": uuid4(),
                    "name": template["name"],
                    "description": f"{template.get('name', '')} - {instance_data['shift']} shift",
                    "shift": template["shift"],
                    "is_active": True,
                    "version": template.get("version", 1),
                    "created_by": instance_data.get('created_by'),
                    "created_at": instance_data.get('created_at', datetime.now().isoformat()),
                    "items": [
                        {
                            "id": item["id"],
                            "template_id": str(uuid4()),
                            "created_at": datetime.now(),
                            "title": item["title"],
                            "description": item.get("description"),
                            "item_type": item["item_type"],
                            "is_required": item["is_required"],
                            "scheduled_time": item.get("scheduled_time"),
                            "notify_before_minutes": item.get("notify_before_minutes"),
                            "severity": item.get("severity", 1),
                            "sort_order": item.get("sort_order", 0)
                        }
                        for item in template.get("items", [])
                    ]
                }
            
            # Convert created_by UUID to UserInfo object
            if instance_data.get('created_by'):
                created_by_uuid = instance_data['created_by'] if isinstance(instance_data['created_by'], UUID) else UUID(instance_data['created_by'])
                user_info = UserService.create_user_info(user_id=created_by_uuid)
                instance_data['created_by'] = user_info
            
            # Convert participants to UserInfo objects
            if 'participants' in instance_data:
                updated_participants = []
                for participant in instance_data['participants']:
                    if isinstance(participant, dict) and 'user_id' in participant:
                        # Use saved participant data if available
                        if participant.get('username'):
                            # Use the actual saved data from auth
                            user_info = {
                                'id': participant['user_id'],
                                'username': participant.get('username', 'unknown'),
                                'email': participant.get('email', ''),
                                'first_name': participant.get('first_name', ''),
                                'last_name': participant.get('last_name', ''),
                                'role': participant.get('role', 'user'),
                                'display_name': participant.get('first_name', participant.get('username', 'Unknown'))
                            }
                            updated_participants.append(user_info)
                        else:
                            # Fallback to UserService lookup for old data
                            participant_uuid = participant['user_id'] if isinstance(participant['user_id'], UUID) else UUID(participant['user_id'])
                            user_info = UserService.create_user_info(user_id=participant_uuid)
                            updated_participants.append(user_info)
                instance_data['participants'] = updated_participants
            
            # Ensure items have all required fields
            if 'items' in instance_data:
                for item in instance_data['items']:
                    # Ensure item ID is a string for frontend compatibility
                    if 'id' in item:
                        item['id'] = str(item['id'])
                    
                    # Transform completed_by to full UserInfo if it exists but is incomplete
                    if item.get('completed_by'):
                        completed_by = item['completed_by']
                        if isinstance(completed_by, dict):
                            # If it only has id/username, fetch full user info
                            if 'email' not in completed_by or 'first_name' not in completed_by:
                                try:
                                    user_id_uuid = UUID(completed_by.get('id', '')) if completed_by.get('id') else None
                                    if user_id_uuid:
                                        user_info = UserService.create_user_info(user_id=user_id_uuid)
                                        item['completed_by'] = user_info
                                    else:
                                        item['completed_by'] = None
                                except:
                                    item['completed_by'] = None
                        else:
                            item['completed_by'] = None
                    else:
                        item['completed_by'] = None
                    
                    # Transform activities to have all required fields
                    if 'activities' in item and item['activities']:
                        transformed_activities = []
                        for activity in item['activities']:
                            if isinstance(activity, dict):
                                # Ensure activity has all required fields
                                transformed_activity = {
                                    'id': activity.get('id', str(uuid4())),
                                    'instance_item_id': str(item['id']),
                                    'action': activity.get('action', 'UPDATED'),
                                    'comment': activity.get('comment') or activity.get('notes') or '',
                                    'created_at': activity.get('created_at') or activity.get('timestamp') or datetime.now().isoformat(),
                                    'metadata': activity.get('metadata', {})
                                }
                                
                                # Transform user to full UserInfo (as 'actor' for frontend compatibility)
                                user_data = activity.get('user') or activity.get('actor')
                                actor_obj = None
                                if isinstance(user_data, dict):
                                    if 'email' not in user_data or 'first_name' not in user_data:
                                        try:
                                            user_id_uuid = UUID(user_data.get('id', '')) if user_data.get('id') else None
                                            if user_id_uuid:
                                                user_info = UserService.create_user_info(user_id=user_id_uuid)
                                                actor_obj = {
                                                    'id': user_info['id'],
                                                    'username': user_info['username'],
                                                    'email': user_info.get('email', ''),
                                                    'first_name': user_info.get('first_name', ''),
                                                    'last_name': user_info.get('last_name', ''),
                                                    'role': user_info.get('role', 'operator')
                                                }
                                            else:
                                                system_user = UserService.create_user_info()
                                                actor_obj = {
                                                    'id': system_user['id'],
                                                    'username': system_user['username'],
                                                    'email': system_user.get('email', ''),
                                                    'first_name': system_user.get('first_name', ''),
                                                    'last_name': system_user.get('last_name', ''),
                                                    'role': system_user.get('role', 'system')
                                                }
                                        except:
                                            system_user = UserService.create_user_info()
                                            actor_obj = {
                                                'id': system_user['id'],
                                                'username': system_user['username'],
                                                'email': system_user.get('email', ''),
                                                'first_name': system_user.get('first_name', ''),
                                                'last_name': system_user.get('last_name', ''),
                                                'role': system_user.get('role', 'system')
                                            }
                                    else:
                                        # Already has all fields
                                        actor_obj = {
                                            'id': user_data.get('id', ''),
                                            'username': user_data.get('username', ''),
                                            'email': user_data.get('email', ''),
                                            'first_name': user_data.get('first_name', ''),
                                            'last_name': user_data.get('last_name', ''),
                                            'role': user_data.get('role', 'operator')
                                        }
                                else:
                                    system_user = UserService.create_user_info()
                                    actor_obj = {
                                        'id': system_user['id'],
                                        'username': system_user['username'],
                                        'email': system_user.get('email', ''),
                                        'first_name': system_user.get('first_name', ''),
                                        'last_name': system_user.get('last_name', ''),
                                        'role': system_user.get('role', 'system')
                                    }
                                
                                # Set both 'user' (for schema) and 'actor' (for frontend)
                                transformed_activity['user'] = actor_obj
                                transformed_activity['actor'] = {
                                    'id': actor_obj['id'],
                                    'username': actor_obj['username'],
                                    'email': actor_obj['email']
                                }
                                # Add 'timestamp' for frontend compatibility (matches ItemActivity type)
                                timestamp_value = activity.get('created_at') or activity.get('timestamp') or datetime.now().isoformat()
                                transformed_activity['timestamp'] = timestamp_value
                                
                                transformed_activities.append(transformed_activity)
                        item['activities'] = transformed_activities
                    else:
                        item['activities'] = []
                    
                    # Add missing fields
                    if 'completed_at' not in item:
                        item['completed_at'] = None
                    if 'skipped_reason' not in item:
                        item['skipped_reason'] = None
                    if 'failure_reason' not in item:
                        item['failure_reason'] = None
                    if 'notes' not in item:
                        item['notes'] = None
                    if 'created_at' not in item:
                        item['created_at'] = instance_data.get('created_at', datetime.now().isoformat())
                    if 'updated_at' not in item:
                        item['updated_at'] = instance_data.get('updated_at', datetime.now().isoformat())
                    if 'attachments' not in item:
                        item['attachments'] = []
            
            # Add closed_by and closed_at if status is COMPLETED but fields are missing
            if instance_data.get('status') == 'COMPLETED':
                if not instance_data.get('closed_at'):
                    instance_data['closed_at'] = instance_data.get('updated_at') or instance_data.get('created_at')
            else:
                instance_data['closed_at'] = None
                instance_data['closed_by'] = None
            
            return instance_data
            
        except Exception as e:
            log.error(f"Error formatting instance response: {e}")
            return instance_data
    
    @staticmethod
    def _load_template_from_file(shift: str) -> Dict[str, Any]:
        """Load template from file with fallback"""
        try:
            # Try to load from JSON template first
            template_path = Path(__file__).parent / "templates" / shift.upper() / "1.json"
            
            if template_path.exists():
                with open(template_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            else:
                # Create fallback template
                return {
                    "id": str(uuid4()),
                    "name": f"{shift.title()} Shift Template",
                    "description": f"Default template for {shift} shift",
                    "shift": shift.upper(),
                    "version": 1,
                    "items": [
                        {
                            "id": "item_1",
                            "title": "System Check",
                            "description": "Perform system health check",
                            "item_type": "ROUTINE",
                            "is_required": True,
                            "scheduled_time": None,
                            "severity": 1,
                            "sort_order": 1
                        }
                    ]
                }
        except Exception as e:
            log.error(f"Failed to load template for {shift}: {e}")
            # Return minimal fallback template
            return {
                "id": str(uuid4()),
                "name": f"{shift.title()} Shift Template",
                "description": f"Default template for {shift} shift",
                "shift": shift.upper(),
                "version": 1,
                "items": []
            }

# Maintain backward compatibility
ChecklistService = UnifiedChecklistService
