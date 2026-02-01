# app/checklists/dashboard_service.py
"""
Dashboard service that fetches data from external endpoints
and stores it locally for offline/backup access
"""

import requests
from typing import Dict, Any, Optional
from datetime import datetime

from app.checklists.dashboard_storage import (
    save_weekly_stats,
    save_prediction_stats,
    load_weekly_stats,
    load_prediction_stats
)

# Simple fallback logger
class SimpleLogger:
    def __init__(self, name):
        self.name = name
    def debug(self, msg): print(f"DEBUG: {msg}")
    def info(self, msg): print(f"INFO: {msg}")
    def warning(self, msg): print(f"WARNING: {msg}")
    def error(self, msg): print(f"ERROR: {msg}")

log = SimpleLogger("dashboard-service")

# External API endpoints
WEEKLY_ENDPOINT = "http://192.168.1.167:3030/api/dashboard/weekly"
PREDICTION_ENDPOINT = "http://192.168.1.167:3030/api/dashboard/prediction"

# Default/fallback data when endpoints are unavailable
DEFAULT_WEEKLY_DATA = {
    "growth": [0.0, 5.0, 7.0, 13.0, -6.0, 13.0, 14.0],
    "total": [1960.0, 1965.0, 1972.0, 1985.0, 1979.0, 1992.0, 2006.0],
    "labels": ["2026-01-25", "2026-01-26", "2026-01-27", "2026-01-28", "2026-01-29", "2026-01-30", "2026-01-31"]
}

DEFAULT_PREDICTION_DATA = {
    "daysRemaining": 176,
    "dailyGrowthRateGb": 5.8,
    "currentUsedGb": 2006.0,
    "totalCapacityGb": 3031.0,
    "predictedFullDate": "2026-07-27",
    "status": "HEALTHY"
}


class DashboardService:
    """Service for fetching and managing dashboard statistics"""
    
    @staticmethod
    def fetch_weekly_stats() -> Optional[Dict[str, Any]]:
        """
        Fetch weekly stats from external endpoint
        Returns None if endpoint is unavailable
        """
        try:
            response = requests.get(WEEKLY_ENDPOINT, timeout=10)
            response.raise_for_status()
            data = response.json()
            log.info("Successfully fetched weekly stats from external endpoint")
            return data
        except requests.exceptions.RequestException as e:
            log.warning(f"Could not fetch weekly stats: {e}")
            return None
        except Exception as e:
            log.error(f"Unexpected error fetching weekly stats: {e}")
            return None
    
    @staticmethod
    def fetch_prediction_stats() -> Optional[Dict[str, Any]]:
        """
        Fetch prediction stats from external endpoint
        Returns None if endpoint is unavailable
        """
        try:
            response = requests.get(PREDICTION_ENDPOINT, timeout=10)
            response.raise_for_status()
            data = response.json()
            log.info("Successfully fetched prediction stats from external endpoint")
            return data
        except requests.exceptions.RequestException as e:
            log.warning(f"Could not fetch prediction stats: {e}")
            return None
        except Exception as e:
            log.error(f"Unexpected error fetching prediction stats: {e}")
            return None
    
    @staticmethod
    def sync_dashboard_data() -> Dict[str, Any]:
        """
        Sync dashboard data from external endpoints
        Falls back to stored data if endpoints unavailable
        Falls back to defaults if no stored data exists
        """
        result = {
            "weekly_synced": False,
            "prediction_synced": False,
            "weekly_source": None,
            "prediction_source": None,
            "timestamp": datetime.now().isoformat()
        }
        
        # Try to fetch weekly stats
        weekly_data = DashboardService.fetch_weekly_stats()
        if weekly_data:
            save_weekly_stats(weekly_data)
            result["weekly_synced"] = True
            result["weekly_source"] = "external"
        else:
            # Check if we have stored data
            stored_weekly = load_weekly_stats()
            if stored_weekly:
                result["weekly_source"] = "local_cache"
            else:
                # Use defaults and save them
                save_weekly_stats(DEFAULT_WEEKLY_DATA.copy())
                result["weekly_source"] = "default"
        
        # Try to fetch prediction stats
        prediction_data = DashboardService.fetch_prediction_stats()
        if prediction_data:
            save_prediction_stats(prediction_data)
            result["prediction_synced"] = True
            result["prediction_source"] = "external"
        else:
            # Check if we have stored data
            stored_prediction = load_prediction_stats()
            if stored_prediction:
                result["prediction_source"] = "local_cache"
            else:
                # Use defaults and save them
                save_prediction_stats(DEFAULT_PREDICTION_DATA.copy())
                result["prediction_source"] = "default"
        
        return result
    
    @staticmethod
    def get_weekly_stats() -> Dict[str, Any]:
        """Get weekly stats (from cache or defaults)"""
        data = load_weekly_stats()
        if data:
            return data
        return DEFAULT_WEEKLY_DATA.copy()
    
    @staticmethod
    def get_prediction_stats() -> Dict[str, Any]:
        """Get prediction stats (from cache or defaults)"""
        data = load_prediction_stats()
        if data:
            return data
        return DEFAULT_PREDICTION_DATA.copy()
    
    @staticmethod
    def get_dashboard_summary() -> Dict[str, Any]:
        """Get complete dashboard summary"""
        weekly = DashboardService.get_weekly_stats()
        prediction = DashboardService.get_prediction_stats()
        
        # Calculate derived metrics
        current_total = prediction.get("currentUsedGb", 0)
        total_capacity = prediction.get("totalCapacityGb", 1)
        usage_percentage = (current_total / total_capacity * 100) if total_capacity > 0 else 0
        
        return {
            "weekly_growth": weekly,
            "prediction": prediction,
            "derived_metrics": {
                "usage_percentage": round(usage_percentage, 1),
                "remaining_capacity_gb": round(total_capacity - current_total, 1),
                "average_daily_growth": round(sum(weekly.get("growth", [])) / len(weekly.get("growth", [1])), 1) if weekly.get("growth") else 0
            },
            "timestamp": datetime.now().isoformat()
        }
