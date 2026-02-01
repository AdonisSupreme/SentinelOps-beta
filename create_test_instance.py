#!/usr/bin/env python3
import json
from datetime import date, datetime
from uuid import uuid4

# Create a test instance for today
today = date.today()
instance_data = {
    "id": str(uuid4()),
    "template_id": None,
    "template_version": 1,
    "template_name": "Test Checklist for Today",
    "checklist_date": today.isoformat(),
    "shift": "MORNING",
    "shift_start": f"{today.isoformat()}T06:00:00",
    "shift_end": f"{today.isoformat()}T14:00:00",
    "status": "OPEN",
    "created_by": "785cfda9-38c7-4b8d-844a-5c8c7672a12b",
    "created_at": datetime.now().isoformat(),
    "updated_at": datetime.now().isoformat(),
    "participants": [
        {
            "user_id": "785cfda9-38c7-4b8d-844a-5c8c7672a12b",
            "joined_at": datetime.now().isoformat()
        }
    ],
    "items": [
        {
            "id": str(uuid4()),
            "instance_id": str(uuid4()),
            "template_item_key": "test_item_1",
            "status": "PENDING",
            "created_at": datetime.now().isoformat(),
            "template_item": {
                "title": "Test Item 1",
                "description": "This is a test item",
                "is_required": True,
                "estimated_duration_minutes": 15
            },
            "activities": []
        },
        {
            "id": str(uuid4()),
            "instance_id": str(uuid4()),
            "template_item_key": "test_item_2",
            "status": "PENDING",
            "created_at": datetime.now().isoformat(),
            "template_item": {
                "title": "Test Item 2",
                "description": "This is another test item",
                "is_required": False,
                "estimated_duration_minutes": 10
            },
            "activities": []
        }
    ],
    "statistics": {
        "total_items": 2,
        "completed_items": 0,
        "in_progress_items": 0,
        "completion_percentage": 0.0
    }
}

# Save the instance
import os
from pathlib import Path

instances_dir = Path(__file__).parent / "app" / "checklists" / "instances"
instances_dir.mkdir(exist_ok=True)

file_path = instances_dir / f"{instance_data['id']}.json"
with open(file_path, 'w', encoding='utf-8') as f:
    json.dump(instance_data, f, indent=2, default=str)

print(f"Created test instance: {file_path}")
print(f"Instance ID: {instance_data['id']}")
print(f"Date: {instance_data['checklist_date']}")
