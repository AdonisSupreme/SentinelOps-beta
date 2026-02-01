# app/checklists/file_service.py
"""
File-based checklist service that uses templates and instances from local files
Completely eliminates database dependency and stack depth issues
"""

import json
from datetime import datetime, date, time, timedelta
from typing import Dict, List, Optional, Any
from uuid import UUID, uuid4
from pathlib import Path

from app.checklists.instance_storage import (
    save_instance, load_instance, update_instance, 
    update_item_status, add_participant, list_instances
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

log = SimpleLogger("file-service")

class FileChecklistService:
    """File-based checklist service - no database required"""
    
    @staticmethod
    def create_checklist_instance(
        checklist_date: date,
        shift: str,
        template_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None
    ) -> Dict[str, Any]:
        """Create a new checklist instance using file templates"""
        
        # Load template from file
        template = FileChecklistService._load_template_from_file(shift)
        
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
                    'id': item['id'],  # Keep as string
                    'template_id': str(uuid4()),  # Add required template_id
                    'created_at': datetime.now().isoformat()  # Add required created_at
                },
                'item': {  # Add the 'item' property for frontend compatibility
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
                'completed_by': None,  # Add required field
                'completed_at': None,  # Add required field
                'skipped_reason': None,  # Add required field
                'failure_reason': None,  # Add required field
                'notes': None,  # Add required field
                'activities': []  # Add activities array for action tracking
            })
        
        # Create instance data
        instance_data = {
            'id': str(instance_id),
            'template_id': str(template_id) if template_id else None,
            'template_version': template['version'],
            'template_name': template['name'],
            'checklist_date': checklist_date.isoformat(),
            'shift': shift,
            'shift_start': shift_start.isoformat(),
            'shift_end': shift_end.isoformat(),
            'status': 'OPEN',
            'created_by': str(user_id) if user_id else None,
            'closed_by': None,  # Add required field
            'closed_at': None,  # Add required field
            'created_at': datetime.now().isoformat(),
            'items': instance_items,
            'participants': [],
            'statistics': {
                'total_items': len(template['items']),
                'completed_items': 0,
                'in_progress_items': 0,
                'completion_percentage': 0.0
            }
        }
        
        # Save instance to file
        if save_instance(instance_data):
            log.info(f"Created checklist instance {instance_id} for {shift} shift")
            
            return {
                'instance': {
                    'id': instance_id,
                    'template_id': template_id,
                    'template_version': template['version'],
                    'template_name': template['name'],
                    'checklist_date': checklist_date,
                    'shift': shift,
                    'shift_start': shift_start,
                    'shift_end': shift_end,
                    'status': 'OPEN',
                    'created_by': user_id,
                    'created_at': datetime.now(),
                    'total_items': len(template['items'])
                },
                'ops_event': {
                    'event_type': 'CHECKLIST_CREATED',
                    'entity_type': 'CHECKLIST_INSTANCE',
                    'entity_id': instance_id,
                    'payload': {
                        'shift': shift,
                        'date': checklist_date.isoformat(),
                        'created_by': str(user_id) if user_id else None,
                        'template_id': str(template_id) if template_id else None,
                        'template_version': template['version'],
                        'total_items': len(template['items'])
                    }
                }
            }
        else:
            raise Exception("Failed to save checklist instance")
    
    @staticmethod
    def get_instance_by_id(instance_id: UUID) -> Dict[str, Any]:
        """Get checklist instance by ID from file storage"""
        try:
            instance_data = load_instance(instance_id)
            
            if not instance_data:
                raise ValueError(f"Checklist instance {instance_id} not found")
            
            # Add missing template object FIRST
            if 'template' not in instance_data:
                # Load template to create template object
                template = FileChecklistService._load_template_from_file(instance_data['shift'])
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
                            "id": item["id"],  # Keep as string
                            "template_id": str(uuid4()),  # Generate UUID for template_id
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
            
            # Now get template items for reference (template is guaranteed to exist)
            template_items = {}
            if 'template' in instance_data and 'items' in instance_data['template']:
                template_items = {item['id']: item for item in instance_data['template']['items']}
                
            for item in instance_data['items']:
                # Add missing fields to template_item
                if 'template_item' in item:
                    template_item = item['template_item']
                    if 'template_id' not in template_item:
                        template_item['template_id'] = str(uuid4())
                    if 'created_at' not in template_item:
                        template_item['created_at'] = instance_data.get('created_at', datetime.now().isoformat())
                
                # IMPORTANT: Add the 'item' property with template item data
                if 'item' not in item:
                    # Use template_item as the primary source for item data
                    if 'template_item' in item and item['template_item']:
                        item['item'] = {
                            "id": item['template_item'].get('id', item.get('id', str(uuid4()))),
                            "title": item['template_item'].get('title', item.get('template_item_key', 'Untitled Item')),
                            "description": item['template_item'].get('description', ''),
                            "item_type": item['template_item'].get('item_type', 'ROUTINE'),
                            "is_required": item['template_item'].get('is_required', False),
                            "scheduled_time": item['template_item'].get('scheduled_time'),
                            "notify_before_minutes": item['template_item'].get('notify_before_minutes'),
                            "severity": item['template_item'].get('severity', 3),
                            "sort_order": item['template_item'].get('sort_order', 0)
                        }
                    elif item.get('template_item_key') and item.get('template_item_key') in template_items:
                        # Use template items cache if available
                        item['item'] = template_items[item['template_item_key']]
                    else:
                        # Create fallback item data - this should rarely happen
                        item['item'] = {
                            "id": item.get('id', str(uuid4())),
                            "title": item.get('template_item_key', 'Untitled Item').replace('_', ' ').title(),
                            "description": '',
                            "item_type": 'ROUTINE',
                            "is_required": False,
                            "scheduled_time": None,
                            "severity": 3,
                            "sort_order": 0
                        }
                
                # Add missing fields to item
                if 'completed_by' not in item:
                    item['completed_by'] = None
                if 'completed_at' not in item:
                    item['completed_at'] = None
                if 'skipped_reason' not in item:
                    item['skipped_reason'] = None
                if 'failure_reason' not in item:
                    item['failure_reason'] = None
                if 'notes' not in item:
                    item['notes'] = None
                if 'activities' not in item:
                    item['activities'] = []
                if 'created_at' not in item:
                    item['created_at'] = instance_data.get('created_at', datetime.now().isoformat())
                if 'updated_at' not in item:
                    item['updated_at'] = instance_data.get('updated_at', datetime.now().isoformat())
                if 'attachments' not in item:
                    item['attachments'] = []  # Add missing attachments field
            
            # Convert created_by UUID to UserInfo object (keep ID as string)
            if instance_data.get('created_by'):
                created_by_uuid = instance_data['created_by'] if isinstance(instance_data['created_by'], UUID) else UUID(instance_data['created_by'])
                user_info = UserService.create_user_info(user_id=created_by_uuid)
                instance_data['created_by'] = user_info
            
            # Convert participants to UserInfo objects (keep IDs as strings)
            if 'participants' in instance_data:
                updated_participants = []
                for participant in instance_data['participants']:
                    if isinstance(participant, dict) and 'user_id' in participant:
                        participant_uuid = participant['user_id'] if isinstance(participant['user_id'], UUID) else UUID(participant['user_id'])
                        user_info = UserService.create_user_info(user_id=participant_uuid)
                        updated_participants.append(user_info)
                instance_data['participants'] = updated_participants
            
            # Keep IDs as strings for JSON serialization
            # instance_data['id'] = UUID(instance_data['id'])  # Keep as string
            # if instance_data.get('template_id'):
            #     instance_data['template_id'] = UUID(instance_data['template_id'])  # Keep as string
            
            # Don't convert item IDs to UUID objects to maintain string format
            for item in instance_data['items']:
                # item['id'] = UUID(item['id'])  # Keep as string
                # item['instance_id'] = UUID(item['instance_id'])  # Keep as string
                pass
            
            return instance_data
            
        except Exception as e:
            log.error(f"Error getting instance by ID: {e}")
            raise ValueError(f"Failed to get instance: {e}")
    
    @staticmethod
    def update_item_status(
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
        """Update checklist item status"""
        
        # Get current instance to find the item
        instance_data = load_instance(instance_id)
        if not instance_data:
            raise ValueError(f"Checklist instance {instance_id} not found")
        
        # Find the item and get current status
        current_item = None
        for item in instance_data.get('items', []):
            if UUID(item['id']) == item_id:
                current_item = item
                break
        
        if not current_item:
            raise ValueError(f"Item {item_id} not found in instance {instance_id}")
        
        current_status = current_item.get('status')
        
        # Update the item status
        if update_item_status(instance_id, str(item_id), status, user_id, comment or reason, action_type, metadata, notes):
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
    
    @staticmethod
    def join_checklist(instance_id: UUID, user_id: UUID) -> Dict[str, Any]:
        """Join a checklist instance"""
        
        if add_participant(instance_id, user_id):
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
    
    @staticmethod
    def get_todays_checklists(shift: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get today's checklists"""
        today = date.today()
        instances = list_instances(shift=shift, checklist_date=today)
        
        # Debug log to see what we're getting
        log.info(f"get_todays_checklists - loaded {len(instances)} instances")
        if instances:
            log.info(f"get_todays_checklists - first instance ID: {instances[0].get('id')}")
            log.info(f"get_todays_checklists - first instance ID type: {type(instances[0].get('id'))}")
        
        # Ensure all required fields are present for validation
        for instance in instances:
            # Add missing fields to instance
            if 'closed_by' not in instance:
                instance['closed_by'] = None
            if 'closed_at' not in instance:
                instance['closed_at'] = None
            
            # Add missing template object
            if 'template' not in instance:
                # Load template to create template object
                template = FileChecklistService._load_template_from_file(instance['shift'])
                instance['template'] = {
                    "id": uuid4(),
                    "name": template["name"],
                    "description": f"{template.get('name', '')} - {instance['shift']} shift",
                    "shift": template["shift"],
                    "is_active": True,
                    "version": template.get("version", 1),
                    "created_by": instance.get('created_by'),
                    "created_at": instance.get('created_at', datetime.now().isoformat()),
                    "items": [
                        {
                            "id": item["id"],  # Keep as string
                            "template_id": str(uuid4()),  # Generate UUID for template_id
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
            
            # Convert created_by UUID to UserInfo object (keep ID as string)
            if instance.get('created_by'):
                created_by_uuid = instance['created_by'] if isinstance(instance['created_by'], UUID) else UUID(instance['created_by'])
                user_info = UserService.create_user_info(user_id=created_by_uuid)
                instance['created_by'] = user_info
            
            # Add missing fields to items
            if 'items' in instance:
                # Get template items for reference
                template_items = {}
                try:
                    template = FileChecklistService._load_template_from_file(instance['shift'])
                    template_items = {item['id']: item for item in template.get('items', [])}
                except Exception as e:
                    log.warning(f"Failed to load template for {instance['shift']}: {e}")
                
                for item in instance['items']:
                    # Add missing fields to template_item
                    if 'template_item' in item:
                        template_item = item['template_item']
                        if 'template_id' not in template_item:
                            template_item['template_id'] = str(uuid4())
                        if 'created_at' not in template_item:
                            template_item['created_at'] = instance.get('created_at', datetime.now().isoformat())
                    
                    # IMPORTANT: Add the 'item' property with template item data
                    if 'item' not in item:
                        # Use template_item as the primary source for item data
                        if 'template_item' in item and item['template_item']:
                            item['item'] = {
                                "id": item['template_item'].get('id', item.get('id', str(uuid4()))),
                                "title": item['template_item'].get('title', item.get('template_item_key', 'Untitled Item')),
                                "description": item['template_item'].get('description', ''),
                                "item_type": item['template_item'].get('item_type', 'ROUTINE'),
                                "is_required": item['template_item'].get('is_required', False),
                                "scheduled_time": item['template_item'].get('scheduled_time'),
                                "notify_before_minutes": item['template_item'].get('notify_before_minutes'),
                                "severity": item['template_item'].get('severity', 3),
                                "sort_order": item['template_item'].get('sort_order', 0)
                            }
                        elif item.get('template_item_key') and item.get('template_item_key') in template_items:
                            # Use template items cache if available
                            item['item'] = template_items[item['template_item_key']]
                        else:
                            # Create fallback item data - this should rarely happen
                            item['item'] = {
                                "id": item.get('id', str(uuid4())),
                                "title": item.get('template_item_key', 'Untitled Item').replace('_', ' ').title(),
                                "description": '',
                                "item_type": 'ROUTINE',
                                "is_required": False,
                                "scheduled_time": None,
                                "severity": 3,
                                "sort_order": 0
                            }
                    
                    # Add missing fields to item
                    if 'completed_by' not in item:
                        item['completed_by'] = None
                    if 'completed_at' not in item:
                        item['completed_at'] = None
                    if 'skipped_reason' not in item:
                        item['skipped_reason'] = None
                    if 'failure_reason' not in item:
                        item['failure_reason'] = None
                    if 'notes' not in item:
                        item['notes'] = None
                    if 'activities' not in item:
                        item['activities'] = []
            
            # Convert participants to UserInfo objects (keep IDs as strings)
            if 'participants' in instance:
                updated_participants = []
                for participant in instance['participants']:
                    if isinstance(participant, dict) and 'user_id' in participant:
                        participant_uuid = participant['user_id'] if isinstance(participant['user_id'], UUID) else UUID(participant['user_id'])
                        user_info = UserService.create_user_info(user_id=participant_uuid)
                        updated_participants.append(user_info)
                instance['participants'] = updated_participants
            
            # Keep IDs as strings for JSON serialization
            # instance['id'] = UUID(instance['id'])  # Keep as string
            # if instance.get('template_id'):
            #     instance['template_id'] = UUID(instance['template_id'])  # Keep as string
        
        return instances
    
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
                # Fallback to hardcoded template
                return FileChecklistService._get_fallback_template(shift)
        
        except Exception as e:
            log.warning(f"Failed to load template for {shift}: {e}")
            return FileChecklistService._get_fallback_template(shift)
    
    @staticmethod
    def _get_fallback_template(shift: str) -> Dict[str, Any]:
        """Get fallback template for any shift"""
        return {
            'version': 1,
            'name': f'{shift.title()} Shift – Core Banking & Digital Operations',
            'items': [
                {
                    'id': 'uptime_check',
                    'title': 'Share Systems Uptime Status',
                    'description': 'Check and report system uptime status',
                    'item_type': 'ROUTINE',
                    'is_required': True,
                    'scheduled_time': None,
                    'severity': 5,
                    'sort_order': 100
                },
                {
                    'id': 'services_check',
                    'title': 'Check all services are functioning',
                    'description': 'Confirm operational status',
                    'item_type': 'ROUTINE',
                    'is_required': True,
                    'scheduled_time': None,
                    'severity': 5,
                    'sort_order': 200
                },
                {
                    'id': 'handover_review',
                    'title': 'Review handover notes',
                    'description': 'Escalate unresolved issues',
                    'item_type': 'ROUTINE',
                    'is_required': True,
                    'scheduled_time': None,
                    'severity': 4,
                    'sort_order': 300
                }
            ]
        }
