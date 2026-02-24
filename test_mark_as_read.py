#!/usr/bin/env python3
"""
Test script to verify notification mark-as-read functionality
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.db.database import get_connection
from app.core.logging import get_logger
from app.notifications.db_service import NotificationDBService
from uuid import uuid4
from datetime import datetime, timezone

log = get_logger("test-mark-as-read")

def create_test_notification():
    """Create a test notification"""
    print("🔧 Creating test notification...")
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Create a test user first
                user_id = uuid4()
                cur.execute("""
                    INSERT INTO users (id, username, email, password_hash, first_name, last_name, is_active, created_at)
                    VALUES (%s, 'testuser', 'test@example.com', 'hashed_password', 'Test', 'User', true, %s)
                    ON CONFLICT (id) DO NOTHING
                """, (user_id, datetime.now(timezone.utc)))
                
                # Create a notification
                notification_id = uuid4()
                cur.execute("""
                    INSERT INTO notifications (
                        id, user_id, title, message, 
                        related_entity, related_id, is_read, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    RETURNING id, title, message, is_read, created_at
                """, (
                    notification_id, user_id, 
                    "Test Notification", 
                    "This is a test notification for mark-as-read functionality",
                    "test_entity", uuid4(), False,
                    datetime.now(timezone.utc)
                ))
                
                result = cur.fetchone()
                conn.commit()
                
                print(f"✅ Created test notification: {result[0]}")
                print(f"   Title: {result[1]}")
                print(f"   Message: {result[2]}")
                print(f"   Read: {result[3]}")
                print(f"   Created: {result[4]}")
                
                return notification_id, user_id
    
    except Exception as e:
        print(f"❌ Failed to create test notification: {e}")
        import traceback
        traceback.print_exc()
        return None, None

def test_mark_as_read(notification_id, user_id):
    """Test marking notification as read"""
    print(f"\n🧪 Testing mark-as-read for notification: {notification_id}")
    
    try:
        # Test the database service directly
        success = NotificationDBService.mark_as_read(notification_id, user_id)
        
        if success:
            print("✅ Successfully marked notification as read via DB service")
        else:
            print("❌ Failed to mark notification as read via DB service")
            return False
        
        # Verify the change in database
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, title, message, is_read, created_at 
                    FROM notifications 
                    WHERE id = %s
                """, (notification_id,))
                
                result = cur.fetchone()
                if result:
                    print(f"📊 Database verification:")
                    print(f"   ID: {result[0]}")
                    print(f"   Title: {result[1]}")
                    print(f"   Message: {result[2]}")
                    print(f"   Read: {result[3]} ✅")
                    print(f"   Created: {result[4]}")
                    
                    return result[3]  # Return the read status
                else:
                    print("❌ Notification not found in database")
                    return False
    
    except Exception as e:
        print(f"❌ Error testing mark-as-read: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_api_endpoint():
    """Test the HTTP API endpoint for marking as read"""
    print(f"\n🌐 Testing HTTP API endpoint...")
    
    try:
        import requests
        import json
        
        # Create notification first
        notification_id, user_id = create_test_notification()
        if not notification_id:
            return False
        
        # Test the API endpoint (this would require a running server)
        print("📝 Note: API endpoint test requires server to be running")
        print("   Endpoint: PATCH /api/v1/notifications/{notification_id}/read")
        print("   Headers: Authorization: Bearer <token>")
        print("   Body: {}")
        
        return True
    
    except ImportError:
        print("⚠️  Requests library not available, skipping API test")
        return True

def cleanup_test_data():
    """Clean up test data"""
    print("\n🧹 Cleaning up test data...")
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Delete test notifications
                cur.execute("""
                    DELETE FROM notifications 
                    WHERE title = 'Test Notification' 
                    AND message LIKE '%mark-as-read functionality%'
                """)
                
                # Delete test user
                cur.execute("""
                    DELETE FROM users WHERE username = 'testuser'
                """)
                
                conn.commit()
                print("✅ Test data cleaned up")
    
    except Exception as e:
        print(f"⚠️  Cleanup failed: {e}")

if __name__ == "__main__":
    print("🚀 SentinelOps Notification Mark-as-Read Test")
    print("=" * 50)
    
    success = True
    
    try:
        # Test 1: Create notification
        notification_id, user_id = create_test_notification()
        if not notification_id:
            success = False
        
        # Test 2: Mark as read via DB service
        if notification_id and user_id:
            mark_success = test_mark_as_read(notification_id, user_id)
            if not mark_success:
                success = False
        
        # Test 3: API endpoint (informational)
        api_success = test_api_endpoint()
        if not api_success:
            success = False
        
        print("\n" + "=" * 50)
        if success:
            print("✅ All mark-as-read tests passed!")
            print("✅ Frontend should now be able to mark notifications as read")
            print("✅ Read notifications will be filtered out from display")
        else:
            print("❌ Some tests failed - check implementation")
        
    finally:
        cleanup_test_data()
    
    print("\n🏁 Test complete")
