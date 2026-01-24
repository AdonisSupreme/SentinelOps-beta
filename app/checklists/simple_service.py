# app/checklists/simple_service.py
"""
Minimal checklist service that works without external dependencies
This is a temporary fallback until proper dependencies are installed
"""

import json
from datetime import datetime, date, time, timedelta
from typing import Dict, List, Optional, Any
from uuid import UUID, uuid4

class SimpleChecklistService:
    """Minimal checklist service that doesn't require external dependencies"""
    
    # Hardcoded fallback template
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
    
    @staticmethod
    def create_checklist_instance(
        checklist_date: date,
        shift: str,
        template_id: Optional[UUID] = None,
        user_id: UUID = None
    ) -> Dict:
        """Create a new checklist instance (minimal version)"""
        
        # Calculate shift times
        shift_times = {
            'MORNING': {'start': time(6, 0), 'end': time(14, 0)},
            'AFTERNOON': {'start': time(14, 0), 'end': time(22, 0)},
            'NIGHT': {'start': time(22, 0), 'end': time(6, 0)}
        }
        
        shift_config = shift_times.get(shift, shift_times['MORNING'])
        shift_start = datetime.combine(checklist_date, shift_config['start'])
        shift_end = datetime.combine(
            checklist_date + timedelta(days=1) if shift == 'NIGHT' and shift_config['end'] < shift_config['start'] else checklist_date,
            shift_config['end']
        )
        
        # Generate instance ID
        instance_id = uuid4()
        
        # Use fallback template
        template = SimpleChecklistService.FALLBACK_TEMPLATE
        
        # Create instance items (in memory for now)
        instance_items = []
        for item in template['items']:
            instance_items.append({
                'id': uuid4(),
                'instance_id': instance_id,
                'template_item_key': item['id'],
                'status': 'PENDING',
                'template_item': item
            })
        
        # Return minimal response (no database operations)
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
            },
            'items': instance_items  # Include items for testing
        }
    
    @staticmethod
    def get_instance_by_id(instance_id: UUID) -> Dict:
        """Get checklist instance by ID (minimal version)"""
        # For now, return a mock instance
        template = SimpleChecklistService.FALLBACK_TEMPLATE
        
        return {
            'id': instance_id,
            'template': {
                'id': None,
                'name': template['name'],
                'version': template['version']
            },
            'checklist_date': date.today(),
            'shift': 'MORNING',
            'status': 'OPEN',
            'created_by': None,
            'created_at': datetime.now(),
            'items': [
                {
                    'id': uuid4(),
                    'template_item_key': item['id'],
                    'status': 'PENDING',
                    'template_item': item
                }
                for item in template['items']
            ],
            'participants': [],
            'statistics': {
                'total_items': len(template['items']),
                'completed_items': 0,
                'completion_percentage': 0.0,
                'time_remaining_minutes': 480  # 8 hours
            }
        }

# Test the service
if __name__ == "__main__":
    print("Testing Simple Checklist Service...")
    
    # Test creating an instance
    result = SimpleChecklistService.create_checklist_instance(
        checklist_date=date.today(),
        shift='MORNING',
        user_id=uuid4()
    )
    
    print(f"✅ Created instance: {result['instance']['id']}")
    print(f"📋 Template: {result['instance']['template_name']}")
    print(f"📊 Total items: {result['instance']['total_items']}")
    
    # Test getting an instance
    instance = SimpleChecklistService.get_instance_by_id(result['instance']['id'])
    print(f"✅ Retrieved instance: {instance['id']}")
    print(f"📊 Items count: {len(instance['items'])}")
    
    print("🎉 Simple service working correctly!")
