#!/usr/bin/env python3
"""
Test script to verify participant joined notifications work correctly
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.db.database import get_connection
from app.core.logging import get_logger
from app.notifications.db_service import NotificationDBService
from app.checklists.db_service import ChecklistDBService
from uuid import uuid4

log = get_logger("test-participant-joined")

def setup_test_data():
    """Set up test checklist instance and participants"""
    print("🔧 Setting up test data...")
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Create a test checklist instance
                instance_id = uuid4()
                template_id = uuid4()  # Add template_id
                checklist_date = "2026-02-21"
                shift = "MORNING"
                
                cur.execute("""
                    INSERT INTO checklist_instances (
                        id, template_id, checklist_date, shift, shift_start, shift_end, status, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                """, (instance_id, template_id, checklist_date, shift, 
                      "2026-02-21 07:00:00 UTC", "2026-02-21 15:00:00 UTC", 
                      "OPEN", "2026-02-21 07:00:00 UTC"))
                
                # Create test users
                user1_id = uuid4()
                user2_id = uuid4()
                new_user_id = uuid4()
                
                cur.execute("""
                    INSERT INTO users (id, username, email, first_name, last_name, is_active, created_at)
                    VALUES 
                        (%s, 'alice', 'alice@test.com', 'Alice', 'Smith', true, %s),
                        (%s, 'bob', 'bob@test.com', 'Bob', 'Jones', true, %s),
                        (%s, 'shumba', 'shumba@test.com', 'Shumba', 'Admin', true, %s)
                    ON CONFLICT (id) DO NOTHING
                """, (user1_id, "2026-02-21 07:00:00 UTC", 
                      user2_id, "2026-02-21 07:00:00 UTC", 
                      new_user_id, "2026-02-21 07:00:00 UTC"))
                
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
        return None, None, None

def test_participant_joined_notification():
    """Test the participant joined notification functionality"""
    print("\n🧪 Testing participant joined notifications...")
    
    instance_id, new_user_id, username = setup_test_data()
    if not instance_id:
        return False
    
    try:
        # Test the notification method directly
        print("📋 Testing direct notification creation...")
        notifications = NotificationDBService.create_participant_joined_notification(
            instance_id=instance_id,
            participant_username=username,
            checklist_date="2026-02-21",
            shift="MORNING"
        )
        
        print(f"✅ Created {len(notifications)} notifications")
        
        # Verify notifications were created
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, title, message, user_id, role_id, related_entity, related_id
                    FROM notifications 
                    WHERE related_id = %s 
                    ORDER BY created_at DESC
                """, (str(instance_id),))
                
                created_notifications = cur.fetchall()
                print(f"📊 Found {len(created_notifications)} notifications in database:")
                
                for notif in created_notifications:
                    notif_type = "User" if notif[3] else "Role"
                    print(f"  - {notif[0]}: {notif[1]} ({notif_type}: {notif[3] or notif[4]})")
        
        # Test the full participant join flow
        print("\n🔄 Testing full participant join flow...")
        success = ChecklistDBService.add_participant(
            instance_id=instance_id,
            user_id=new_user_id,
            username=username
        )
        
        if success:
            print("✅ Participant added successfully")
            
            # Check for new notifications
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT COUNT(*) FROM notifications 
                        WHERE related_id = %s AND title LIKE '%joined the shift%'
                    """, (str(instance_id),))
                    
                    count = cur.fetchone()[0]
                    print(f"📊 Total participant joined notifications: {count}")
                    
                    if count > 0:
                        cur.execute("""
                            SELECT title, message, created_at 
                            FROM notifications 
                            WHERE related_id = %s AND title LIKE '%joined the shift%'
                            ORDER BY created_at DESC
                        """, (str(instance_id),))
                        
                        recent = cur.fetchall()
                        print("Recent participant joined notifications:")
                        for notif in recent:
                            print(f"  - {notif[0]}")
                            print(f"    {notif[1]}")
                            print(f"    {notif[2]}")
                    else:
                        print("❌ No participant joined notifications found")
                        return False
        else:
            print("❌ Failed to add participant")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def cleanup_test_data(instance_id):
    """Clean up test data"""
    print("\n🧹 Cleaning up test data...")
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Delete notifications
                cur.execute("DELETE FROM notifications WHERE related_id = %s", (str(instance_id),))
                
                # Delete participants
                cur.execute("DELETE FROM checklist_participants WHERE instance_id = %s", (str(instance_id),))
                
                # Delete instance
                cur.execute("DELETE FROM checklist_instances WHERE id = %s", (str(instance_id),))
                
                # Delete test users
                cur.execute("DELETE FROM users WHERE username IN ('alice', 'bob', 'shumba')")
                
                conn.commit()
                print("✅ Test data cleaned up")
    
    except Exception as e:
        print(f"⚠️ Cleanup failed: {e}")

if __name__ == "__main__":
    print("🚀 SentinelOps Participant Joined Notification Test")
    print("=" * 60)
    
    instance_id = None
    try:
        if test_participant_joined_notification():
            print("\n✅ All participant joined notification tests passed!")
        else:
            print("\n❌ Some tests failed")
    
    finally:
        if instance_id:
            cleanup_test_data(instance_id)
    
    print("\n🏁 Test complete")
