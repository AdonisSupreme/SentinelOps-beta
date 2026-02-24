#!/usr/bin/env python3
"""
Debug script to check why notifications aren't being created
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.db.database import get_connection
from app.core.logging import get_logger

log = get_logger("debug-notifications")

def check_database_state():
    """Check the current state of roles and notifications"""
    print("🔍 Checking database state...")
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Check roles table structure
                print("\n📋 Roles table structure:")
                cur.execute("""
                    SELECT column_name, data_type 
                    FROM information_schema.columns 
                    WHERE table_name = 'roles' 
                    ORDER BY ordinal_position
                """)
                roles_columns = cur.fetchall()
                for col in roles_columns:
                    print(f"  - {col[0]}: {col[1]}")
                
                # Check what roles actually exist
                print("\n👥 Existing roles:")
                cur.execute("SELECT * FROM roles")
                roles = cur.fetchall()
                if not roles:
                    print("  ❌ No roles found!")
                else:
                    for role in roles:
                        print(f"  ✅ {role}")
                
                # Check for admin/manager specifically
                print("\n🔎 Looking for admin/manager roles:")
                cur.execute("""
                    SELECT id, name FROM roles WHERE name IN ('admin', 'manager')
                """)
                admin_manager_roles = cur.fetchall()
                if not admin_manager_roles:
                    print("  ❌ No admin/manager roles found!")
                    print("  🔍 This is why notifications aren't being created!")
                else:
                    print("  ✅ Found admin/manager roles:")
                    for role in admin_manager_roles:
                        print(f"    - {role}")
                
                # Check notifications table
                print("\n📬 Notifications table:")
                cur.execute("SELECT COUNT(*) FROM notifications")
                notification_count = cur.fetchone()[0]
                print(f"  Total notifications: {notification_count}")
                
                if notification_count > 0:
                    cur.execute("SELECT id, title, created_at FROM notifications ORDER BY created_at DESC LIMIT 5")
                    recent_notifications = cur.fetchall()
                    print("  Recent notifications:")
                    for notif in recent_notifications:
                        print(f"    - {notif[0]}: {notif[1]} ({notif[2]})")
                else:
                    print("  ❌ No notifications found")
                
                # Check user_roles junction table
                print("\n🔗 User-Role assignments:")
                cur.execute("""
                    SELECT u.username, r.name 
                    FROM user_roles ur
                    JOIN users u ON ur.user_id = u.id
                    JOIN roles r ON ur.role_id = r.id
                    LIMIT 10
                """)
                user_roles = cur.fetchall()
                if user_roles:
                    print("  User roles:")
                    for ur in user_roles:
                        print(f"    - {ur[0]} → {ur[1]}")
                else:
                    print("  ❌ No user-role assignments found")
                
    except Exception as e:
        print(f"❌ Database error: {e}")
        return False
    
    return True

def test_notification_creation():
    """Test creating a notification directly"""
    print("\n🧪 Testing notification creation...")
    
    try:
        from app.notifications.db_service import NotificationDBService
        from uuid import uuid4
        
        # Try to create a test notification
        test_notification = NotificationDBService.create_notification(
            title="Test Notification",
            message="This is a test notification",
            user_id=None,  # We'll try role-based
            role_id=None,  # This will fail since we need either user_id or role_id
        )
        
        print(f"✅ Test notification created: {test_notification}")
        
    except Exception as e:
        print(f"❌ Test notification failed: {e}")
        
        # Try with role_id if we can find a role
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM roles WHERE name = 'admin' LIMIT 1")
                    admin_role = cur.fetchone()
                    
                    if admin_role:
                        test_notification = NotificationDBService.create_notification(
                            title="Test Admin Notification",
                            message="This is a test admin notification",
                            role_id=admin_role[0]
                        )
                        print(f"✅ Test admin notification created: {test_notification}")
                    else:
                        print("❌ No admin role found to test with")
                        
        except Exception as e2:
            print(f"❌ Admin notification test failed: {e2}")

if __name__ == "__main__":
    print("🚀 SentinelOps Notification Debug Tool")
    print("=" * 50)
    
    if check_database_state():
        test_notification_creation()
    
    print("\n🏁 Debug complete")
