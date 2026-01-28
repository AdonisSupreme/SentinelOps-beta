#!/usr/bin/env python3
"""
Script to add missing notes field to existing instance files
"""

import json
import os
from pathlib import Path
from typing import Dict, Any

def add_notes_field(file_path: Path) -> bool:
    """Add notes field to all items in a single instance file"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        modified = False
        
        # Add notes field to all items
        for item in data.get('items', []):
            if 'notes' not in item:
                item['notes'] = None
                modified = True
        
        if modified:
            # Save the updated file
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)
            print(f"Added notes field to: {file_path.name}")
            return True
        
        return False
        
    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        return False

def main():
    """Main function to add notes field to all instance files"""
    instances_dir = Path(__file__).parent / "instances"
    
    if not instances_dir.exists():
        print(f"Instances directory not found: {instances_dir}")
        return
    
    print("Adding notes field to instance files...")
    
    fixed_count = 0
    for file_path in instances_dir.glob("*.json"):
        if add_notes_field(file_path):
            fixed_count += 1
    
    print(f"\nCompleted! Added notes field to {fixed_count} instance files.")

if __name__ == "__main__":
    main()
