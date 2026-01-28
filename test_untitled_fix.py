#!/usr/bin/env python3
"""
Test script to verify the untitled item fix
"""
import sys
import json
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.checklists.file_service import FileChecklistService
from uuid import UUID

def test_instance_data():
    """Test that instances have proper item structure"""
    print("🔍 Testing instance data structure...")
    
    # Get today's checklists
    try:
        instances = FileChecklistService.get_todays_checklists()
        print(f"Found {len(instances)} instances for today")
        
        for instance in instances:
            print(f"\n📋 Instance: {instance['id']}")
            print(f"   Shift: {instance['shift']}")
            print(f"   Items count: {len(instance.get('items', []))}")
            
            # Check first few items
            for i, item in enumerate(instance.get('items', [])[:3]):
                print(f"   Item {i+1}:")
                print(f"     ID: {item.get('id')}")
                print(f"     Template Item Key: {item.get('template_item_key')}")
                print(f"     Has 'item' property: {'item' in item}")
                
                if 'item' in item:
                    item_data = item['item']
                    print(f"     Item title: {item_data.get('title', 'MISSING')}")
                    print(f"     Item description: {item_data.get('description', 'MISSING')[:50]}...")
                else:
                    print(f"     ❌ MISSING 'item' property!")
                
                if 'template_item' in item:
                    template_item = item['template_item']
                    print(f"     Template title: {template_item.get('title', 'MISSING')}")
        
        # Test specific instance by ID if we have one
        if instances:
            instance_id = instances[0]['id']
            print(f"\n🔍 Testing specific instance by ID: {instance_id}")
            
            try:
                instance = FileChecklistService.get_instance_by_id(UUID(instance_id))
                print(f"   Instance loaded successfully")
                print(f"   Items count: {len(instance.get('items', []))}")
                
                # Check first item
                if instance.get('items'):
                    first_item = instance['items'][0]
                    print(f"   First item:")
                    print(f"     Has 'item' property: {'item' in first_item}")
                    if 'item' in first_item:
                        print(f"     Title: {first_item['item'].get('title', 'MISSING')}")
                    else:
                        print(f"     ❌ MISSING 'item' property!")
                        
            except Exception as e:
                print(f"   ❌ Error loading instance: {e}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_instance_data()
