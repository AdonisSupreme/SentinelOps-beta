#!/usr/bin/env python3
"""
Script to fix completed_by user mapping in existing instance files
Changes system user to ashumba for the specific user ID
"""

import json
import os
from pathlib import Path
from typing import Dict, Any

def fix_instance_file(file_path: Path) -> bool:
    """Fix completed_by user mapping in a single instance file"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        modified = False
        target_user_id = "785cfda9-38c7-4b8d-844a-5c8c7672a12b"
        
        # Fix items completed by the target user ID
        for item in data.get('items', []):
            if (item.get('completed_by') and 
                item.get('completed_by', {}).get('id') == 'system-user-id' and
                item.get('updated_by') == target_user_id):
                
                # Change to ashumba
                item['completed_by'] = {
                    'id': '785cfda9-38c7-4b8d-844a-5c8c7672a12b',
                    'username': 'ashumba',
                    'email': 'ashumba@sentinel.ops',
                    'first_name': 'Ashumba',
                    'last_name': 'Operator',
                    'role': 'senior_operator',
                    'display_name': 'Ashumba'
                }
                modified = True
                print(f"Fixed item: {item.get('template_item_key', 'Unknown')} in {file_path.name}")
        
        if modified:
            # Save the updated file
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)
            print(f"Updated: {file_path.name}")
            return True
        
        return False
        
    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        return False

def main():
    """Main function to fix all instance files"""
    instances_dir = Path(__file__).parent / "instances"
    
    if not instances_dir.exists():
        print(f"Instances directory not found: {instances_dir}")
        return
    
    print("Fixing completed_by user mapping in instance files...")
    
    fixed_count = 0
    for file_path in instances_dir.glob("*.json"):
        if fix_instance_file(file_path):
            fixed_count += 1
    
    print(f"\nCompleted! Fixed {fixed_count} instance files.")

if __name__ == "__main__":
    main()
