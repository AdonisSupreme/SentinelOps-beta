# app/checklists/dashboard_storage.py
"""
File-based dashboard stats storage
Stores dashboard statistics (weekly growth and predictions) as JSON files
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
import threading

# Simple fallback logger to avoid dependency issues
class SimpleLogger:
    def __init__(self, name):
        self.name = name
    def debug(self, msg): print(f"DEBUG: {msg}")
    def info(self, msg): print(f"INFO: {msg}")
    def warning(self, msg): print(f"WARNING: {msg}")
    def error(self, msg): print(f"ERROR: {msg}")

log = SimpleLogger("dashboard-storage")

# Directory where dashboard stats are stored
DASHBOARD_DIR = Path(__file__).parent / "dashboard_data"

# Thread-safe lock for file operations
_file_lock = threading.Lock()

def ensure_dashboard_dir():
    """Ensure the dashboard directory exists"""
    DASHBOARD_DIR.mkdir(exist_ok=True)

def get_stats_file_path(filename: str) -> Path:
    """Get the file path for a stats file"""
    ensure_dashboard_dir()
    return DASHBOARD_DIR / filename

def save_weekly_stats(data: Dict[str, Any]) -> bool:
    """Save weekly growth stats to file"""
    try:
        with _file_lock:
            file_path = get_stats_file_path("weekly_stats.json")
            
            # Add metadata
            data['updated_at'] = datetime.now().isoformat()
            data['version'] = data.get('version', 1) + 1
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)
            
            log.info(f"Saved weekly stats to {file_path}")
            return True
    except Exception as e:
        log.error(f"Failed to save weekly stats: {e}")
        return False

def load_weekly_stats() -> Optional[Dict[str, Any]]:
    """Load weekly growth stats from file"""
    try:
        file_path = get_stats_file_path("weekly_stats.json")
        
        if not file_path.exists():
            log.warning(f"Weekly stats file not found: {file_path}")
            return None
        
        with _file_lock:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        
        log.debug(f"Loaded weekly stats from {file_path}")
        return data
    except Exception as e:
        log.error(f"Failed to load weekly stats: {e}")
        return None

def save_prediction_stats(data: Dict[str, Any]) -> bool:
    """Save prediction stats to file"""
    try:
        with _file_lock:
            file_path = get_stats_file_path("prediction_stats.json")
            
            # Add metadata
            data['updated_at'] = datetime.now().isoformat()
            data['version'] = data.get('version', 1) + 1
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)
            
            log.info(f"Saved prediction stats to {file_path}")
            return True
    except Exception as e:
        log.error(f"Failed to save prediction stats: {e}")
        return False

def load_prediction_stats() -> Optional[Dict[str, Any]]:
    """Load prediction stats from file"""
    try:
        file_path = get_stats_file_path("prediction_stats.json")
        
        if not file_path.exists():
            log.warning(f"Prediction stats file not found: {file_path}")
            return None
        
        with _file_lock:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        
        log.debug(f"Loaded prediction stats from {file_path}")
        return data
    except Exception as e:
        log.error(f"Failed to load prediction stats: {e}")
        return None

def get_all_dashboard_stats() -> Dict[str, Any]:
    """Get all dashboard stats (weekly and prediction)"""
    weekly = load_weekly_stats()
    prediction = load_prediction_stats()
    
    return {
        "weekly": weekly,
        "prediction": prediction,
        "last_updated": datetime.now().isoformat(),
        "sources_available": {
            "weekly": weekly is not None,
            "prediction": prediction is not None
        }
    }
