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
from uuid import UUID
import threading

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

def update_item_status(instance_id: UUID, item_id: str, status: str, user_id: Optional[UUID] = None, comment: Optional[str] = None) -> bool:
    """Update status of a specific item in an instance"""
    try:
        instance_data = load_instance(instance_id)
        if not instance_data:
            return False
        
        # Find and update the item
        for item in instance_data.get('items', []):
            if item.get('id') == item_id or item.get('template_item_key') == item_id:
                item['status'] = status
                item['updated_at'] = datetime.now().isoformat()
                if user_id:
                    item['updated_by'] = str(user_id)
                if comment:
                    item['comment'] = comment
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

def add_participant(instance_id: UUID, user_id: UUID) -> bool:
    """Add a participant to the checklist instance"""
    try:
        instance_data = load_instance(instance_id)
        if not instance_data:
            return False
        
        participants = instance_data.get('participants', [])
        user_id_str = str(user_id)
        
        # Check if user is already a participant
        if not any(p.get('user_id') == user_id_str for p in participants):
            participants.append({
                'user_id': user_id_str,
                'joined_at': datetime.now().isoformat()
            })
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
