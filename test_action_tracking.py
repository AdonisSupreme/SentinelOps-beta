#!/usr/bin/env python3
"""
Test the actual action tracking functionality
"""
import sys
import json
from pathlib import Path
from uuid import UUID, uuid4
from datetime import datetime

# Add the app directory to the Python path
sys.path.insert(0, str(Path(__file__).parent / "app"))

from checklists.instance_storage import update_item_status, load_instance

def test_action_tracking():
    print("Testing action tracking functionality...")
    
    # Find an existing instance to test with
    instances_dir = Path("app/checklists/instances")
    instance_files = list(instances_dir.glob("*.json"))
    
    if not instance_files:
        print("No instance files found for testing")
        return False
    
    # Use the first instance file for testing
    instance_file = instance_files[0]
    instance_id = UUID(instance_file.stem)
    
    print(f"Using instance: {instance_id}")
    
    # Load the current instance
    instance_data = load_instance(instance_id)
    if not instance_data:
        print("Failed to load instance")
        return False
    
    # Find a PENDING item to update
    pending_item = None
    for item in instance_data.get('items', []):
        if item.get('status') == 'PENDING':
            pending_item = item
            break
    
    if not pending_item:
        print("No PENDING items found to test with")
        return False
    
    item_id = pending_item['id']
    print(f"Testing with item: {item_id} (current status: {pending_item.get('status')})")
    
    # Test the enhanced update_item_status function
    try:
        success = update_item_status(
            instance_id=instance_id,
            item_id=item_id,
            status='IN_PROGRESS',
            user_id=uuid4(),  # Test user
            comment='Test action tracking',
            action_type='STARTED',
            metadata={'test': True, 'duration_ms': 1500},
            notes='Test notes for activity tracking'
        )
        
        if success:
            print("Item status updated successfully!")
            
            # Load the updated instance to verify
            updated_instance = load_instance(instance_id)
            if updated_instance:
                # Find the updated item
                updated_item = None
                for item in updated_instance.get('items', []):
                    if item['id'] == item_id:
                        updated_item = item
                        break
                
                if updated_item:
                    print(f"New status: {updated_item.get('status')}")
                    print(f"Notes: {updated_item.get('notes')}")
                    print(f"Activities count: {len(updated_item.get('activities', []))}")
                    
                    # Show the latest activity
                    activities = updated_item.get('activities', [])
                    if activities:
                        latest_activity = activities[-1]
                        print(f"Latest activity: {latest_activity.get('action')} by {latest_activity.get('actor', {}).get('username')}")
                        print(f"Activity notes: {latest_activity.get('notes')}")
                        print(f"Activity metadata: {latest_activity.get('metadata')}")
                    
                    return True
                else:
                    print("Updated item not found")
                    return False
            else:
                print("Failed to load updated instance")
                return False
        else:
            print("Failed to update item status")
            return False
            
    except Exception as e:
        print(f"Error during update: {e}")
        return False

if __name__ == "__main__":
    success = test_action_tracking()
    if success:
        print("\n[SUCCESS] Action tracking test completed successfully!")
    else:
        print("\n[FAILED] Action tracking test failed!")
        sys.exit(1)
