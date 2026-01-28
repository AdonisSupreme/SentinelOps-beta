#!/usr/bin/env python3
"""
Script to fix missing completed_by and completed_at fields for completed items
"""

import json
import os
from pathlib import Path
from typing import Dict, Any
from datetime import datetime

def fix_completed_items(file_path: Path) -> bool:
    """Fix completed items missing completed_by and completed_at fields"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        modified = False
        
        # Fix completed items
        for item in data.get('items', []):
            if item.get('status') == 'COMPLETED':
                # Add completed_by if missing
                if 'completed_by' not in item or item['completed_by'] is None:
                    # Check if there's an updated_by field to determine the user
                    if item.get('updated_by') == '785cfda9-38c7-4b8d-844a-5c8c7672a12b':
                        # This is ashumba's user ID
                        item['completed_by'] = {
                            'id': '785cfda9-38c7-4b8d-844a-5c8c7672a12b',
                            'username': 'ashumba',
                            'email': 'ashumba@sentinel.ops',
                            'first_name': 'Ashumba',
                            'last_name': 'Operator',
                            'role': 'senior_operator',
                            'display_name': 'Ashumba'
                        }
                    else:
                        # Default to system user
                        item['completed_by'] = {
                            'id': 'system-user-id',
                            'username': 'system',
                            'email': 'system@sentinel.ops',
                            'first_name': 'System',
                            'last_name': 'User',
                            'role': 'system',
                            'display_name': 'System'
                        }
                    modified = True
                
                # Add completed_at if missing
                if 'completed_at' not in item or item['completed_at'] is None:
                    # Use updated_at as fallback, or current time
                    if item.get('updated_at'):
                        item['completed_at'] = item['updated_at']
                    else:
                        item['completed_at'] = datetime.now().isoformat()
                    modified = True
        
        if modified:
            # Save the updated file
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)
            print(f"Fixed completed items in: {file_path.name}")
            return True
        
        return False
        
    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        return False

def main():
    """Main function to fix completed items in all instance files"""
    instances_dir = Path(__file__).parent / "instances"
    
    if not instances_dir.exists():
        print(f"Instances directory not found: {instances_dir}")
        return
    
    print("Fixing completed items missing completed_by and completed_at fields...")
    
    fixed_count = 0
    for file_path in instances_dir.glob("*.json"):
        if fix_completed_items(file_path):
            fixed_count += 1
    
    print(f"\nCompleted! Fixed {fixed_count} instance files.")

if __name__ == "__main__":
    main()
