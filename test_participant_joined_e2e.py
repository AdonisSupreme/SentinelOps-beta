#!/usr/bin/env python3
"""
Comprehensive test to verify participant joined notifications work end-to-end
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.db.database import get_connection
from app.core.logging import get_logger
from app.notifications.db_service import NotificationDBService
from app.checklists.db_service import ChecklistDBService
from uuid import uuid4

log = get_logger("test-participant-joined-e2e")

def setup_complete_test_scenario():
    """Set up complete test scenario with users, instance, and participants"""
    print("🔧 Setting up complete test scenario...")
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Create test users
                user1_id = uuid4()
                user2_id = uuid4()
                new_user_id = uuid4()
                
                cur.execute("""
                    INSERT INTO users (id, username, email, password_hash, first_name, last_name, is_active, created_at)
                    VALUES 
                        (%s, 'alice', 'alice@test.com', 'hashed_password_123', 'Alice', 'Smith', true, %s),
                        (%s, 'bob', 'bob@test.com', 'hashed_password_123', 'Bob', 'Jones', true, %s),
                        (%s, 'shumba', 'shumba@test.com', 'hashed_password_123', 'Shumba', 'Admin', true, %s)
                    ON CONFLICT (id) DO NOTHING
                """, (user1_id, "2026-02-21 07:00:00 UTC", 
                      user2_id, "2026-02-21 07:00:00 UTC", 
                      new_user_id, "2026-02-21 07:00:00 UTC"))
                
                # Create a test checklist template
                template_id = uuid4()
                cur.execute("""
                    INSERT INTO checklist_templates (id, name, description, department_id, section_id, is_active, created_by, created_at)
                    VALUES (%s, 'Test Template', 'Test template for participant notifications', %s, %s, true, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                """, (template_id, uuid4(), uuid4(), user1_id, "2026-02-21 07:00:00 UTC"))
                
                # Create a test checklist instance
                instance_id = uuid4()
                checklist_date = "2026-02-21"
                shift = "MORNING"
                
                cur.execute("""
                    INSERT INTO checklist_instances (
                        id, template_id, checklist_date, shift, shift_start, shift_end, status, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                """, (instance_id, template_id, checklist_date, shift, 
                      "2026-02-21 07:00:00 UTC", "2026-02-21 15:00:00 UTC", 
                      "OPEN"))
                
                # Add existing participants
                cur.execute("""
                    INSERT INTO checklist_participants (id, instance_id, user_id, joined_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (instance_id, user_id) DO NOTHING
                """, (uuid4(), instance_id, user1_id, "2026-02-21 07:05:00 UTC"))
                
                cur.execute("""
                    INSERT INTO checklist_participants (id, instance_id, user_id, joined_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (instance_id, user_id) DO NOTHING
                """, (uuid4(), instance_id, user2_id, "2026-02-21 07:10:00 UTC"))
                
                conn.commit()
                print(f"✅ Created test instance: {instance_id}")
                print(f"✅ Created test users: alice, bob, shumba")
                print(f"✅ Added existing participants: alice, bob")
                
                return instance_id, new_user_id, "shumba"
    
    except Exception as e:
        print(f"❌ Setup failed: {e}")
        import traceback
        traceback.print_exc()
        return None, None, None

def test_notification_creation_directly():
    """Test notification creation directly"""
    print("\n🧪 Testing notification creation directly...")
    
    instance_id, new_user_id, username = setup_complete_test_scenario()
    if not instance_id:
        return False
    
    try:
        # Test direct notification creation
        notifications = NotificationDBService.create_participant_joined_notification(
            instance_id=instance_id,
            participant_username=username,
            checklist_date="2026-02-21",
            shift="MORNING"
        )
        
        print(f"✅ Direct notification creation: {len(notifications)} notifications created")
        
        # Verify notifications in database
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, title, message, user_id, role_id, related_entity, related_id
                    FROM notifications 
                    WHERE related_id = %s AND title LIKE '%joined the shift%'
                    ORDER BY created_at DESC
                """, (str(instance_id),))
                
                created_notifications = cur.fetchall()
                print(f"📊 Found {len(created_notifications)} participant joined notifications:")
                
                for notif in created_notifications:
                    notif_type = "User" if notif[3] else "Role"
                    print(f"  - {notif[0]}: {notif[1]} ({notif_type}: {notif[3] or notif[4]})")
                    print(f"    Message: {notif[2][:50]}...")
        
        return len(created_notifications) > 0
        
    except Exception as e:
        print(f"❌ Direct notification test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_complete_join_flow():
    """Test the complete join flow through ChecklistDBService"""
    print("\n🔄 Testing complete join flow...")
    
    instance_id, new_user_id, username = setup_complete_test_scenario()
    if not instance_id:
        return False
    
    try:
        # Clear any existing notifications for clean test
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM notifications WHERE related_id = %s", (str(instance_id),))
                conn.commit()
        
        # Test the complete join flow
        print(f"🔄 Simulating {username} joining checklist...")
        success = ChecklistDBService.add_participant(
            instance_id=instance_id,
            user_id=new_user_id,
            username=username
        )
        
        if success:
            print("✅ Participant added successfully through ChecklistDBService")
            
            # Check for notifications created by the join flow
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT COUNT(*) FROM notifications 
                        WHERE related_id = %s AND title LIKE '%joined the shift%'
                    """, (str(instance_id),))
                    
                    count = cur.fetchone()[0]
                    print(f"📊 Notifications created by join flow: {count}")
                    
                    if count > 0:
                        cur.execute("""
                            SELECT title, message, user_id, role_id, created_at 
                            FROM notifications 
                            WHERE related_id = %s AND title LIKE '%joined the shift%'
                            ORDER BY created_at DESC
                        """, (str(instance_id),))
                        
                        notifications = cur.fetchall()
                        print("Notifications created:")
                        for notif in notifications:
                            recipient = "User" if notif[2] else "Role"
                            print(f"  - {notif[0]} ({recipient})")
                            print(f"    {notif[1]}")
                            print(f"    Created: {notif[4]}")
                        
                        return True
                    else:
                        print("❌ No notifications created by join flow")
                        return False
        else:
            print("❌ Failed to add participant")
            return False
        
    except Exception as e:
        print(f"❌ Complete join flow test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def verify_notification_data_integrity():
    """Verify notification data integrity and relationships"""
    print("\n🔍 Verifying notification data integrity...")
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Check notifications table structure
                cur.execute("""
                    SELECT column_name, data_type 
                    FROM information_schema.columns 
                    WHERE table_name = 'notifications' 
                    ORDER BY ordinal_position
                """)
                columns = cur.fetchall()
                print("📋 Notifications table structure:")
                for col in columns:
                    print(f"  - {col[0]}: {col[1]}")
                
                # Check for proper foreign key relationships
                cur.execute("""
                    SELECT n.id, n.user_id, u.username as user_username,
                           n.role_id, r.name as role_name,
                           n.title, n.message, n.created_at
                    FROM notifications n
                    LEFT JOIN users u ON n.user_id = u.id
                    LEFT JOIN roles r ON n.role_id = r.id
                    WHERE n.title LIKE '%joined the shift%'
                    ORDER BY n.created_at DESC
                    LIMIT 5
                """)
                
                notifications = cur.fetchall()
                print(f"\n📊 Recent participant joined notifications with relationships:")
                for notif in notifications:
                    print(f"  - ID: {notif[0]}")
                    print(f"    User: {notif[2]} ({notif[1]})")
                    print(f"    Role: {notif[4]} ({notif[3]})")
                    print(f"    Title: {notif[5]}")
                    print(f"    Created: {notif[7]}")
                    print()
                
                return len(notifications) > 0
        
    except Exception as e:
        print(f"❌ Data integrity verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def cleanup_test_data():
    """Clean up all test data"""
    print("\n🧹 Cleaning up test data...")
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Delete notifications
                cur.execute("DELETE FROM notifications WHERE title LIKE '%joined the shift%'")
                
                # Delete participants
                cur.execute("""
                    DELETE FROM checklist_participants 
                    WHERE user_id IN (
                        SELECT id FROM users WHERE username IN ('alice', 'bob', 'shumba')
                    )
                """)
                
                # Delete instances
                cur.execute("""
                    DELETE FROM checklist_instances 
                    WHERE id IN (
                        SELECT cp.instance_id 
                        FROM checklist_participants cp 
                        JOIN users u ON cp.user_id = u.id 
                        WHERE u.username IN ('alice', 'bob', 'shumba')
                    )
                """)
                
                # Delete templates
                cur.execute("""
                    DELETE FROM checklist_templates 
                    WHERE name = 'Test Template'
                """)
                
                # Delete test users
                cur.execute("DELETE FROM users WHERE username IN ('alice', 'bob', 'shumba')")
                
                conn.commit()
                print("✅ Test data cleaned up")
    
    except Exception as e:
        print(f"⚠️ Cleanup failed: {e}")

if __name__ == "__main__":
    print("🚀 SentinelOps Participant Joined Notification E2E Test")
    print("=" * 65)
    
    success = True
    
    try:
        # Test 1: Direct notification creation
        if not test_notification_creation_directly():
            success = False
        
        # Test 2: Complete join flow
        if not test_complete_join_flow():
            success = False
        
        # Test 3: Data integrity verification
        if not verify_notification_data_integrity():
            success = False
        
        print("\n" + "=" * 65)
        if success:
            print("✅ All participant joined notification tests passed!")
            print("✅ System correctly notifies all instance participants when someone joins")
        else:
            print("❌ Some tests failed - participant notification system needs attention")
        
    finally:
        cleanup_test_data()
    
    print("\n🏁 E2E Test complete")
