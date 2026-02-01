# app/checklists/dashboard_router.py
"""
Dashboard router for database stats visualization
Provides endpoints for weekly growth and capacity prediction data
"""

from fastapi import APIRouter, HTTPException
from typing import Dict, Any
from datetime import datetime

from app.checklists.dashboard_service import DashboardService
from app.checklists.dashboard_storage import get_all_dashboard_stats

# Simple fallback logger
class SimpleLogger:
    def __init__(self, name):
        self.name = name
    def debug(self, msg): print(f"DEBUG: {msg}")
    def info(self, msg): print(f"INFO: {msg}")
    def warning(self, msg): print(f"WARNING: {msg}")
    def error(self, msg): print(f"ERROR: {msg}")

log = SimpleLogger("dashboard-router")

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/stats")
async def get_dashboard_stats() -> Dict[str, Any]:
    """
    Get complete dashboard statistics including weekly growth and predictions
    Falls back to stored data if external endpoints unavailable
    """
    try:
        summary = DashboardService.get_dashboard_summary()
        return {
            "success": True,
            "data": summary,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        log.error(f"Error getting dashboard stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/weekly")
async def get_weekly_stats() -> Dict[str, Any]:
    """
    Get weekly database growth statistics
    """
    try:
        data = DashboardService.get_weekly_stats()
        return {
            "success": True,
            "data": data,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        log.error(f"Error getting weekly stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/prediction")
async def get_prediction_stats() -> Dict[str, Any]:
    """
    Get database capacity prediction statistics
    """
    try:
        data = DashboardService.get_prediction_stats()
        return {
            "success": True,
            "data": data,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        log.error(f"Error getting prediction stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync")
async def sync_dashboard_data() -> Dict[str, Any]:
    """
    Manually trigger sync with external data sources
    """
    try:
        result = DashboardService.sync_dashboard_data()
        return {
            "success": True,
            "message": "Dashboard data sync completed",
            "details": result
        }
    except Exception as e:
        log.error(f"Error syncing dashboard data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def get_dashboard_health() -> Dict[str, Any]:
    """
    Get health status of dashboard data sources
    """
    try:
        stats = get_all_dashboard_stats()
        return {
            "success": True,
            "sources": stats["sources_available"],
            "last_updated": stats["last_updated"]
        }
    except Exception as e:
        log.error(f"Error getting dashboard health: {e}")
        raise HTTPException(status_code=500, detail=str(e))
