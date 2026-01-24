# app/checklists/template_loader.py
"""
Checklist Template File Loader

Definitions live in files. State lives in PostgreSQL.
No runtime joins on definitions. Files are immutable, instances are not.
"""

import os
import json
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import time
from functools import lru_cache
from uuid import uuid4

from pydantic import BaseModel, validator, Field
from app.checklists.schemas import ChecklistItemType, ShiftType
from app.core.logging import get_logger

log = get_logger("template-loader")

# Try to import yaml, but don't fail if it's not available
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    log.warning("PyYAML not available. Only JSON templates will be supported.")

# Directory where templates are stored
TEMPLATES_DIR = Path(__file__).parent / "templates"

class TemplateItemFile(BaseModel):
    """File-based template item model"""
    id: str = Field(..., description="File-scoped stable ID")
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None
    item_type: ChecklistItemType
    is_required: bool = True
    scheduled_time: Optional[time] = None
    notify_before_minutes: Optional[int] = Field(None, ge=0, le=1440)
    severity: int = Field(default=1, ge=1, le=5)
    sort_order: int = Field(default=0, ge=0)
    
    @validator('scheduled_time', pre=True)
    def parse_time(cls, v):
        if isinstance(v, str):
            try:
                return time.fromisoformat(v)
            except ValueError:
                raise ValueError(f"Invalid time format: {v}. Expected HH:MM format")
        return v
    
    @validator('id')
    def validate_id(cls, v):
        if not v or not isinstance(v, str):
            raise ValueError("Item ID must be a non-empty string")
        return v

class ChecklistTemplateFile(BaseModel):
    """File-based template model"""
    version: int = Field(..., ge=1)
    shift: ShiftType
    name: str = Field(..., min_length=1, max_length=200)
    items: List[TemplateItemFile] = []
    
    @validator('items')
    def validate_items(cls, items):
        if not items:
            raise ValueError("Template must have at least one item")
        
        # Check for duplicate IDs
        ids = [item.id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate item IDs found in template")
        
        # Check sort_order sequence
        sort_orders = [item.sort_order for item in items]
        if len(sort_orders) != len(set(sort_orders)):
            raise ValueError("Duplicate sort_order values found")
        
        return items

class TemplateLoader:
    """File-based template loader with caching"""
    
    @staticmethod
    def get_template_path(shift: ShiftType, version: int) -> Path:
        """Get the file path for a template"""
        shift_dir = TEMPLATES_DIR / shift.value
        if not shift_dir.exists():
            raise ValueError(f"Template directory not found: {shift_dir}")
        
        # Try YAML first, then JSON
        yaml_path = shift_dir / f"{version}.yaml"
        json_path = shift_dir / f"{version}.json"
        
        if yaml_path.exists() and YAML_AVAILABLE:
            template_path = yaml_path
        elif json_path.exists():
            template_path = json_path
        elif yaml_path.exists() and not YAML_AVAILABLE:
            raise ValueError(f"YAML template found but PyYAML not installed. Install PyYAML or use JSON format. File: {yaml_path}")
        else:
            raise ValueError(f"Template file not found: {shift.value}/v{version}")
        
        return template_path
    
    @staticmethod
    @lru_cache(maxsize=128)
    def load_template(shift: ShiftType, version: int = 1) -> ChecklistTemplateFile:
        """Load template from file with caching"""
        try:
            template_path = TemplateLoader.get_template_path(shift, version)
            
            with open(template_path, 'r', encoding='utf-8') as f:
                if template_path.suffix.lower() in ['.yaml', '.yml'] and YAML_AVAILABLE:
                    data = yaml.safe_load(f)
                else:
                    data = json.load(f)
            
            # Validate and parse the template
            template = ChecklistTemplateFile(**data)
            
            log.info(f"Loaded template: {shift.value} v{version} with {len(template.items)} items")
            return template
            
        except FileNotFoundError:
            raise ValueError(f"Template file not found: {shift.value} v{version}")
        except Exception as e:
            # Handle yaml errors gracefully when yaml is not available
            if 'yaml' in str(type(e)).lower() or 'YAML' in str(e):
                if not YAML_AVAILABLE:
                    raise ValueError(f"YAML template found but PyYAML not installed. Install PyYAML: pip install PyYAML")
                else:
                    raise ValueError(f"Invalid YAML in template {shift.value} v{version}: {e}")
            elif 'JSON' in str(e):
                raise ValueError(f"Invalid JSON in template {shift.value} v{version}: {e}")
            else:
                raise ValueError(f"Error loading template {shift.value} v{version}: {e}")
    
    @staticmethod
    def get_latest_version(shift: ShiftType) -> int:
        """Get the latest version number for a shift"""
        shift_dir = TEMPLATES_DIR / shift.value
        if not shift_dir.exists():
            raise ValueError(f"Template directory not found: {shift_dir}")
        
        versions = []
        for file_path in shift_dir.glob("*.yaml"):
            try:
                version = int(file_path.stem)
                versions.append(version)
            except ValueError:
                continue
        
        for file_path in shift_dir.glob("*.json"):
            try:
                version = int(file_path.stem)
                versions.append(version)
            except ValueError:
                continue
        
        if not versions:
            raise ValueError(f"No template versions found for shift: {shift.value}")
        
        return max(versions)
    
    @staticmethod
    def list_templates() -> Dict[ShiftType, List[int]]:
        """List all available templates by shift and version"""
        templates = {}
        
        if not TEMPLATES_DIR.exists():
            return templates
        
        for shift_dir in TEMPLATES_DIR.iterdir():
            if not shift_dir.is_dir():
                continue
            
            try:
                shift = ShiftType(shift_dir.name)
                versions = []
                
                for file_path in shift_dir.glob("*.yaml"):
                    try:
                        version = int(file_path.stem)
                        versions.append(version)
                    except ValueError:
                        continue
                
                for file_path in shift_dir.glob("*.json"):
                    try:
                        version = int(file_path.stem)
                        versions.append(version)
                    except ValueError:
                        continue
                
                if versions:
                    templates[shift] = sorted(versions)
                    
            except ValueError:
                # Invalid shift name, skip
                continue
        
        return templates
    
    @staticmethod
    def clear_cache():
        """Clear the template cache"""
        TemplateLoader.load_template.cache_clear()
        log.info("Template cache cleared")

# Convenience functions
def load_template(shift: ShiftType, version: int = 1) -> ChecklistTemplateFile:
    """Load template from file"""
    return TemplateLoader.load_template(shift, version)

def get_latest_template(shift: ShiftType) -> ChecklistTemplateFile:
    """Load the latest version of a template"""
    latest_version = TemplateLoader.get_latest_version(shift)
    return TemplateLoader.load_template(shift, latest_version)
