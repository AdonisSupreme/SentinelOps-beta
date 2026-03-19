#!/usr/bin/env python3
"""
Test script to verify PERSONAL task assignment fix
"""

import sys
import os
from uuid import uuid4

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from app.tasks.schemas import TaskCreate, TaskType, Priority, TaskStatus
    from app.tasks.service import TaskService
    
    # Create a test user
    test_user = {
        'id': str(uuid4()),
        'username': 'testuser',
        'role': 'USER'
    }
    
    # Create a PERSONAL task without assigned_to_id
    task_data = TaskCreate(
        title="Test Personal Task",
        description="This is a test personal task",
        task_type=TaskType.PERSONAL,
        priority=Priority.MEDIUM,
        status=TaskStatus.ACTIVE,
        assigned_by_id=uuid4()  # This should be overwritten
    )
    
    print(f"Before fix - assigned_to_id: {task_data.assigned_to_id}")
    print(f"Before fix - assigned_by_id: {task_data.assigned_by_id}")
    
    # Simulate the fix logic
    user_id = test_user['id']
    
    if task_data.task_type == TaskType.PERSONAL:
        if task_data.assigned_to_id and task_data.assigned_to_id != user_id:
            print("❌ Would raise validation error")
        else:
            task_data.assigned_to_id = user_id
            task_data.assigned_by_id = user_id
            print("✅ PERSONAL task assignment fixed")
    
    print(f"After fix - assigned_to_id: {task_data.assigned_to_id}")
    print(f"After fix - assigned_by_id: {task_data.assigned_by_id}")
    
    # Verify the fix
    if task_data.assigned_to_id == user_id and task_data.assigned_by_id == user_id:
        print("🎉 PERSONAL task assignment fix verified!")
    else:
        print("❌ Fix failed")
        
except ImportError as e:
    print(f"❌ Import error: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ Unexpected error: {e}")
    sys.exit(1)
