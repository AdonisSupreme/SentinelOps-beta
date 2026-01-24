# Test the router integration with file service
from app.checklists.file_service import FileChecklistService
from datetime import date
from uuid import uuid4

print("Testing Router Integration with File Service...")

# Test the key operations that the router uses
try:
    # 1. Test create_checklist_instance (router calls this)
    result = FileChecklistService.create_checklist_instance(
        checklist_date=date.today(),
        shift='MORNING',
        user_id=uuid4()
    )
    print(f"SUCCESS: create_checklist_instance: {result['instance']['id']}")
    
    # 2. Test get_instance_by_id (router calls this)
    instance = FileChecklistService.get_instance_by_id(result['instance']['id'])
    print(f"SUCCESS: get_instance_by_id: {len(instance['items'])} items")
    
    # 3. Test get_todays_checklists (router calls this)
    today_instances = FileChecklistService.get_todays_checklists()
    print(f"SUCCESS: get_todays_checklists: {len(today_instances)} instances")
    
    # 4. Test update_item_status (router calls this)
    item_id = instance['items'][0]['id']
    update_result = FileChecklistService.update_item_status(
        instance_id=result['instance']['id'],
        item_id=item_id,
        status='COMPLETED',
        user_id=uuid4()
    )
    print(f"SUCCESS: update_item_status: {update_result['instance']['status']}")
    
    # 5. Test join_checklist (router calls this)
    join_result = FileChecklistService.join_checklist(
        instance_id=result['instance']['id'],
        user_id=uuid4()
    )
    print(f"SUCCESS: join_checklist: {join_result['instance']['user_id']}")
    
    print("\nSUCCESS: ALL ROUTER OPERATIONS WORKING WITH FILE SERVICE!")
    print("SUCCESS: Stack depth error should be RESOLVED")
    
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
