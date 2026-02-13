# app/checklists/instance_storage.py
"""
File-based checklist instance storage
Stores checklist instances as JSON files on the local filesystem
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime, date
from uuid import UUID, uuid4
import threading

from app.checklists.user_service import UserService
from app.checklists.email_service import EmailService

# Simple fallback logger to avoid dependency issues
class SimpleLogger:
    def __init__(self, name):
        self.name = name
    def debug(self, msg): print(f"DEBUG: {msg}")
    def info(self, msg): print(f"INFO: {msg}")
    def warning(self, msg): print(f"WARNING: {msg}")
    def error(self, msg): print(f"ERROR: {msg}")

log = SimpleLogger("instance-storage")

# Directory where checklist instances are stored
INSTANCES_DIR = Path(__file__).parent / "instances"

# Thread-safe lock for file operations
_file_lock = threading.Lock()

def ensure_instances_dir():
    """Ensure the instances directory exists"""
    INSTANCES_DIR.mkdir(exist_ok=True)

def get_instance_file_path(instance_id: UUID) -> Path:
    """Get the file path for a checklist instance"""
    ensure_instances_dir()
    return INSTANCES_DIR / f"{instance_id}.json"

def save_instance(instance_data: Dict[str, Any]) -> bool:
    """Save checklist instance to file"""
    try:
        with _file_lock:
            instance_id = instance_data['id']
            file_path = get_instance_file_path(instance_id)
            
            # Add metadata
            instance_data['updated_at'] = datetime.now().isoformat()
            instance_data['version'] = instance_data.get('version', 1)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(instance_data, f, indent=2, default=str)
            
            log.debug(f"Saved instance {instance_id} to {file_path}")
            return True
    except Exception as e:
        log.error(f"Failed to save instance {instance_data.get('id')}: {e}")
        return False

def load_instance(instance_id: UUID) -> Optional[Dict[str, Any]]:
    """Load checklist instance from file"""
    try:
        file_path = get_instance_file_path(instance_id)
        
        if not file_path.exists():
            log.warning(f"Instance file not found: {file_path}")
            return None
        
        with _file_lock:
            with open(file_path, 'r', encoding='utf-8') as f:
                instance_data = json.load(f)
        
        log.debug(f"Loaded instance {instance_id} from {file_path}")
        return instance_data
    except Exception as e:
        log.error(f"Failed to load instance {instance_id}: {e}")
        return None

def update_instance(instance_id: UUID, updates: Dict[str, Any]) -> bool:
    """Update checklist instance with partial data"""
    try:
        instance_data = load_instance(instance_id)
        if not instance_data:
            return False
        
        # Apply updates
        instance_data.update(updates)
        instance_data['updated_at'] = datetime.now().isoformat()
        instance_data['version'] = instance_data.get('version', 1) + 1
        
        return save_instance(instance_data)
    except Exception as e:
        log.error(f"Failed to update instance {instance_id}: {e}")
        return False

def update_item_status(instance_id: UUID, item_id: str, status: str, user_id: Optional[UUID] = None, comment: Optional[str] = None, action_type: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, notes: Optional[str] = None, reason: Optional[str] = None) -> bool:
    """Update status of a specific item in an instance with enhanced status transition support"""
    try:
        instance_data = load_instance(instance_id)
        if not instance_data:
            return False
        
        # Find and update the item
        for item in instance_data.get('items', []):
            if item.get('id') == item_id or item.get('template_item_key') == item_id:
                # Store previous status for activity tracking
                previous_status = item.get('status')
                
                # Calculate duration if we have previous status change timestamp
                duration_ms = None
                if metadata and 'duration_ms' in metadata:
                    duration_ms = metadata['duration_ms']
                elif item.get('updated_at'):
                    try:
                        prev_time = datetime.fromisoformat(item['updated_at'].replace('Z', '+00:00'))
                        current_time = datetime.now()
                        duration_ms = int((current_time - prev_time).total_seconds() * 1000)
                    except Exception:
                        duration_ms = None
                
                # Update basic fields
                item['status'] = status
                item['updated_at'] = datetime.now().isoformat()
                if user_id:
                    item['updated_by'] = str(user_id)
                
                # Set notes field (this is the main notes field)
                if notes:
                    item['notes'] = notes
                elif comment:
                    item['notes'] = comment
                elif reason:
                    item['notes'] = reason
                
                # Initialize activities array if it doesn't exist
                if 'activities' not in item:
                    item['activities'] = []
                
                # Create activity entry matching frontend ItemActivity interface
                activity_entry = {
                    'id': str(uuid4()),
                    'action': action_type or _determine_action_type(status, previous_status),
                    'actor': _create_actor_info(user_id) if user_id else _get_default_actor(),
                    'timestamp': datetime.now().isoformat(),
                    'notes': notes or comment or reason,
                    'instance_item_id': str(item_id),  # Required field
                    'user': _create_actor_info(user_id) if user_id else _get_default_actor(),  # Required field
                    'comment': notes or comment or reason,  # Required field
                    'created_at': datetime.now().isoformat(),  # Required field
                    'metadata': {
                        'previous_status': previous_status,
                        'new_status': status,
                        'reason': reason or comment or notes,
                        'duration_ms': duration_ms
                    }
                }
                
                # Add additional metadata if provided
                if metadata:
                    activity_entry['metadata'].update(metadata)
                
                # Add the activity to the item's activity log
                item['activities'].append(activity_entry)
                
                # Set completed_by and completed_at when status is COMPLETED
                if status == 'COMPLETED':
                    if user_id:
                        user_info = UserService.create_user_info(user_id=user_id)
                        item['completed_by'] = {
                            'id': str(user_info['id']),
                            'username': user_info['username'],
                            'email': user_info.get('email', ''),
                            'first_name': user_info.get('first_name', ''),
                            'last_name': user_info.get('last_name', ''),
                            'role': user_info.get('role', 'user')
                        }
                    else:
                        # Use system user as fallback instead of hardcoded ashumba
                        system_user = UserService.identify_user()
                        item['completed_by'] = {
                            'id': str(system_user['id']),
                            'username': system_user['username'],
                            'email': system_user.get('email', ''),
                            'first_name': system_user.get('first_name', ''),
                            'last_name': system_user.get('last_name', ''),
                            'role': system_user.get('role', 'system')
                        }
                    item['completed_at'] = datetime.now().isoformat()
                else:
                    # Clear completion data when status is not COMPLETED
                    item['completed_by'] = None
                    item['completed_at'] = None
                
                # Set other status-specific fields
                if status == 'SKIPPED' and (comment or notes or reason):
                    item['skipped_reason'] = comment or notes or reason
                    # Send escalation email for skipped item
                    EmailService.send_escalation_email(
                        item_title=item.get('template_item', {}).get('title', 'Unknown Item'),
                        action_type='SKIPPED',
                        reason=comment or notes or reason or 'No reason provided',
                        checklist_date=instance_data.get('checklist_date', 'Unknown'),
                        shift=instance_data.get('shift', 'Unknown'),
                        operator_name=_create_actor_info(user_id).get('username', 'Unknown') if user_id else 'System',
                        instance_id=str(instance_id)
                    )
                elif status == 'FAILED' and (comment or notes or reason):
                    item['failure_reason'] = comment or notes or reason
                    # Send escalation email for failed item
                    EmailService.send_escalation_email(
                        item_title=item.get('template_item', {}).get('title', 'Unknown Item'),
                        action_type='FAILED',
                        reason=comment or notes or reason or 'No description provided',
                        checklist_date=instance_data.get('checklist_date', 'Unknown'),
                        shift=instance_data.get('shift', 'Unknown'),
                        operator_name=_create_actor_info(user_id).get('username', 'Unknown') if user_id else 'System',
                        instance_id=str(instance_id)
                    )
                else:
                    # Clear reason fields when not applicable
                    if status != 'SKIPPED':
                        item['skipped_reason'] = None
                    if status != 'FAILED':
                        item['failure_reason'] = None
                
                break
        else:
            log.warning(f"Item {item_id} not found in instance {instance_id}")
            return False
        
        # Update instance statistics
        _update_instance_statistics(instance_data)
        
        return save_instance(instance_data)
    except Exception as e:
        log.error(f"Failed to update item status in instance {instance_id}: {e}")
        return False

def add_participant(instance_id: UUID, user_id: UUID, user_info: Optional[Dict[str, Any]] = None) -> bool:
    """Add a participant to the checklist instance with full user info"""
    try:
        instance_data = load_instance(instance_id)
        if not instance_data:
            return False
        
        participants = instance_data.get('participants', [])
        user_id_str = str(user_id)
        
        # Check if user is already a participant
        if not any(p.get('user_id') == user_id_str for p in participants):
            participant_data = {
                'user_id': user_id_str,
                'joined_at': datetime.now().isoformat()
            }
            
            # Add user info if provided (username, role from auth)
            if user_info:
                participant_data['username'] = user_info.get('username', 'unknown')
                participant_data['role'] = user_info.get('role', 'user')
                participant_data['email'] = user_info.get('email', '')
                participant_data['first_name'] = user_info.get('first_name', '')
                participant_data['last_name'] = user_info.get('last_name', '')
            print(f"USER INFO: {user_info}")
            participants.append(participant_data)
            instance_data['participants'] = participants
            
            return save_instance(instance_data)
        
        return True  # Already a participant
    except Exception as e:
        log.error(f"Failed to add participant to instance {instance_id}: {e}")
        return False

def list_instances(shift: Optional[str] = None, checklist_date: Optional[date] = None) -> List[Dict[str, Any]]:
    """List all checklist instances, optionally filtered"""
    try:
        ensure_instances_dir()
        instances = []
        
        for file_path in INSTANCES_DIR.glob("*.json"):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    instance_data = json.load(f)
                
                # Apply filters
                if shift and instance_data.get('shift') != shift:
                    continue
                if checklist_date and instance_data.get('checklist_date') != checklist_date.isoformat():
                    continue
                
                instances.append(instance_data)
            except Exception as e:
                log.warning(f"Failed to load instance from {file_path}: {e}")
                continue
        
        return instances
    except Exception as e:
        log.error(f"Failed to list instances: {e}")
        return []

def get_today_instances(user_id: Optional[UUID] = None, shift: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get today's checklist instances for a user"""
    try:
        today = date.today()
        instances = list_instances(shift=shift, checklist_date=today)
        
        # Filter by user if specified (check if user is a participant)
        if user_id:
            user_id_str = str(user_id)
            filtered_instances = []
            for instance in instances:
                participants = instance.get('participants', [])
                if any(p.get('user_id') == user_id_str for p in participants):
                    filtered_instances.append(instance)
            instances = filtered_instances
        
        return instances
    except Exception as e:
        log.error(f"Failed to get today's instances: {e}")
        return []


def join_instance(instance_id: UUID, user_id: UUID, user_info: Optional[Dict[str, Any]] = None) -> bool:
    """Add a user as a participant to a checklist instance"""
    return add_participant(instance_id, user_id, user_info)


def delete_instance(instance_id: UUID) -> bool:
    """Delete a checklist instance"""
    try:
        file_path = get_instance_file_path(instance_id)
        
        if file_path.exists():
            with _file_lock:
                file_path.unlink()
            log.debug(f"Deleted instance {instance_id}")
            return True
        
        return False
    except Exception as e:
        log.error(f"Failed to delete instance {instance_id}: {e}")
        return False

def _update_instance_statistics(instance_data: Dict[str, Any]):
    """Update instance statistics based on item statuses"""
    items = instance_data.get('items', [])
    
    total_items = len(items)
    completed_items = sum(1 for item in items if item.get('status') == 'COMPLETED')
    in_progress_items = sum(1 for item in items if item.get('status') == 'IN_PROGRESS')
    
    completion_percentage = (completed_items / total_items * 100) if total_items > 0 else 0
    
    # Determine overall status
    if completion_percentage == 100:
        instance_status = 'COMPLETED'
    elif in_progress_items > 0:
        instance_status = 'IN_PROGRESS'
    else:
        instance_status = 'OPEN'
    
    instance_data['statistics'] = {
        'total_items': total_items,
        'completed_items': completed_items,
        'in_progress_items': in_progress_items,
        'completion_percentage': round(completion_percentage, 1)
    }
    
    instance_data['status'] = instance_status


def _determine_action_type(status: str, previous_status: str) -> str:
    """Determine the action type based on status change"""
    if status == 'IN_PROGRESS' and previous_status == 'PENDING':
        return 'STARTED'
    elif status == 'COMPLETED':
        return 'COMPLETED'
    elif status == 'SKIPPED':
        return 'SKIPPED'
    elif status == 'FAILED':
        return 'FAILED'
    else:
        return 'UPDATED'


def _create_actor_info(user_id: UUID) -> Dict[str, Any]:
    """Create actor information for activity tracking matching frontend interface"""
    user_info = UserService.create_user_info(user_id=user_id)
    return {
        'id': str(user_info['id']),
        'username': user_info['username'],
        'email': user_info.get('email', ''),
        'first_name': user_info.get('first_name', ''),
        'last_name': user_info.get('last_name', ''),
        'role': user_info.get('role', 'operator')
    }


def _get_default_actor() -> Dict[str, Any]:
    """Get default actor information when no user_id is provided"""
    # Use system user as fallback instead of hardcoded ashumba
    system_user = UserService.identify_user()
    return {
        'id': str(system_user['id']),
        'username': system_user['username'],
        'email': system_user.get('email', ''),
        'first_name': system_user.get('first_name', ''),
        'last_name': system_user.get('last_name', ''),
        'role': system_user.get('role', 'system')
    }
