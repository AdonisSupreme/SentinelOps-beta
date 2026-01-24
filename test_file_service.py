from app.checklists.file_service import FileChecklistService
from datetime import date
from uuid import uuid4

print('Testing File-Based Checklist Service...')

# Test creating an instance
result = FileChecklistService.create_checklist_instance(
    checklist_date=date.today(),
    shift='MORNING',
    user_id=uuid4()
)

print(f'SUCCESS: Created instance: {result["instance"]["id"]}')
print(f'Template: {result["instance"]["template_name"]}')
print(f'Total items: {result["instance"]["total_items"]}')

# Test getting the instance
instance = FileChecklistService.get_instance_by_id(result['instance']['id'])
print(f'SUCCESS: Retrieved instance: {instance["id"]}')
print(f'Items count: {len(instance["items"])}')

# Test updating item status
item_id = instance['items'][0]['id']
update_result = FileChecklistService.update_item_status(
    instance_id=result['instance']['id'],
    item_id=item_id,
    status='COMPLETED',
    user_id=uuid4()
)
print(f'SUCCESS: Updated item status: {update_result["instance"]["status"]}')

# Test joining checklist
join_result = FileChecklistService.join_checklist(
    instance_id=result['instance']['id'],
    user_id=uuid4()
)
print(f'SUCCESS: User joined checklist: {join_result["instance"]["user_id"]}')

print('SUCCESS: File-based service working correctly!')
