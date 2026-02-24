#!/usr/bin/env python3
"""
Test script to manually trigger notification creation and verify it works
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.db.database import get_connection
from app.core.logging import get_logger
from app.notifications.db_service import NotificationDBService
from uuid import uuid4

log = get_logger("test-notifications")

def test_direct_notification_creation():
    """Test creating notifications directly"""
    print("🧪 Testing direct notification creation...")
    
    try:
        # First, let's see what roles exist
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name FROM roles WHERE name IN ('admin', 'manager')")
                roles = cur.fetchall()
                print(f"Found roles: {roles}")
                
                if not roles:
                    print("❌ No admin/manager roles found!")
                    return False
        
        # Try to create a notification for each role
        for role_id, role_name in roles:
            try:
                print(f"📬 Creating notification for {role_name} role...")
                
                notification = NotificationDBService.create_notification(
                    title=f"Test Notification for {role_name}",
                    message=f"This is a test notification created for {role_name} role",
                    role_id=role_id,
                    related_entity="test",
                    related_id=uuid4()
                )
                
                print(f"✅ Created notification: {notification['id']}")
                
            except Exception as e:
                print(f"❌ Failed to create notification for {role_name}: {e}")
                return False
        
        # Check total notifications count
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM notifications")
                count = cur.fetchone()[0]
                print(f"📊 Total notifications in database: {count}")
                
                if count > 0:
                    cur.execute("SELECT id, title, message, created_at FROM notifications ORDER BY created_at DESC LIMIT 5")
                    recent = cur.fetchall()
                    print("Recent notifications:")
                    for n in recent:
                        print(f"  - {n[0]}: {n[1]} ({n[3]})")
        
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

def test_notification_helpers():
    """Test the helper methods that should be called from checklist updates"""
    print("\n🧪 Testing notification helper methods...")
    
    try:
        # Test the skipped notification method
        print("📋 Testing create_item_skipped_notification...")
        result = NotificationDBService.create_item_skipped_notification(
            item_id=uuid4(),
            item_title="Test Item",
            instance_id=uuid4(),
            checklist_date="2026-02-21",
            shift="MORNING",
            skipped_reason="Test skip reason"
        )
        print(f"✅ Skipped notification result: {len(result)} notifications created")
        
        # Test the failed notification method
        print("🚨 Testing create_item_failed_notification...")
        result = NotificationDBService.create_item_failed_notification(
            item_id=uuid4(),
            item_title="Test Failed Item",
            instance_id=uuid4(),
            checklist_date="2026-02-21",
            shift="MORNING",
            failure_reason="Test failure reason"
        )
        print(f"✅ Failed notification result: {len(result)} notifications created")
        
        # Test the completed notification method
        print("✅ Testing create_checklist_completed_notification...")
        result = NotificationDBService.create_checklist_completed_notification(
            instance_id=uuid4(),
            checklist_date="2026-02-21",
            shift="MORNING",
            completion_rate=85.5,
            completed_by_username="testuser"
        )
        print(f"✅ Completed notification result: {len(result)} notifications created")
        
        return True
        
    except Exception as e:
        print(f"❌ Helper method test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def simulate_checklist_item_update():
    """Simulate what happens when a checklist item is updated"""
    print("\n🧪 Simulating checklist item update...")
    
    try:
        # This simulates the exact code path in db_service.py
        item_id = uuid4()
        item_title = "Test Checklist Item"
        instance_id = uuid4()
        checklist_date = "2026-02-21"
        shift = "MORNING"
        reason = "Test skip reason"
        
        print(f"🔄 Simulating item skip for item: {item_title}")
        
        # This is the exact call from db_service.py line 1346-1353
        notifications = NotificationDBService.create_item_skipped_notification(
            item_id=item_id,
            item_title=item_title,
            instance_id=instance_id,
            checklist_date=checklist_date,
            shift=shift,
            skipped_reason=reason
        )
        
        print(f"✅ Simulation successful: {len(notifications)} notifications created")
        
        # Verify the notifications were actually created
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, title, message, related_entity, related_id 
                    FROM notifications 
                    WHERE related_id = %s 
                    ORDER BY created_at DESC
                """, (str(item_id),))
                
                created_notifications = cur.fetchall()
                print(f"📊 Found {len(created_notifications)} notifications for this item:")
                for notif in created_notifications:
                    print(f"  - {notif[0]}: {notif[1]}")
        
        return len(created_notifications) > 0
        
    except Exception as e:
        print(f"❌ Simulation failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("🚀 SentinelOps Notification Test Suite")
    print("=" * 50)
    
    success = True
    
    # Test 1: Direct notification creation
    if not test_direct_notification_creation():
        success = False
    
    # Test 2: Helper methods
    if not test_notification_helpers():
        success = False
    
    # Test 3: Simulate checklist update
    if not simulate_checklist_item_update():
        success = False
    
    print("\n" + "=" * 50)
    if success:
        print("✅ All tests passed! Notification system should be working.")
    else:
        print("❌ Some tests failed. Notification system has issues.")
    
    print("🏁 Test complete")
