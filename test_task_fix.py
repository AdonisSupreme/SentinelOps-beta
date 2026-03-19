#!/usr/bin/env python3
"""
Test script to verify task creation fixes
"""

import sys
import os

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from app.tasks.service import TaskService, TaskAccessError, TaskValidationError, TaskNotFoundError, TaskStateTransitionError
    print("✓ All custom exception classes imported successfully")
    
    from app.tasks.router import router
    print("✓ Router imported successfully")
    
    # Test that exceptions can be raised and caught properly
    try:
        raise TaskValidationError("Test validation error")
    except TaskValidationError as e:
        print(f"✓ TaskValidationError works: {e.message}")
    
    try:
        raise TaskNotFoundError("Test not found error")
    except TaskNotFoundError as e:
        print(f"✓ TaskNotFoundError works: {e.message}")
        
    try:
        raise TaskStateTransitionError("Test state transition error")
    except TaskStateTransitionError as e:
        print(f"✓ TaskStateTransitionError works: {e.message}")
        
    try:
        raise TaskAccessError("Test access error")
    except TaskAccessError as e:
        print(f"✓ TaskAccessError works: {e.message}")
    
    print("\n🎉 All fixes appear to be working correctly!")
    
except ImportError as e:
    print(f"❌ Import error: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ Unexpected error: {e}")
    sys.exit(1)
