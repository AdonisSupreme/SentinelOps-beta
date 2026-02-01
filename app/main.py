from pathlib import Path
import sys

# -------------------------------------------------------------------
# Ensure project root is on sys.path so `python app/main.py` works
# -------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# -------------------------------------------------------------------
# Core imports
# -------------------------------------------------------------------
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.logging import get_logger
from app.core.config import settings

# Routers
from app.auth.router import router as auth_router
from app.checklists.router import router as checklists_router  # Use original router for existing endpoints
from app.checklists.file_router_minimal import router as checklists_file_router  # Use minimal file router for file-based endpoints
from app.checklists.dashboard_router import router as dashboard_router  # Dashboard stats router
from app.notifications.router import router as notifications_router
from app.gamification.router import router as gamification_router

# DB lifecycle
from app.db.database import init_db, health_check

log = get_logger("main")

# -------------------------------------------------------------------
# FastAPI app
# -------------------------------------------------------------------
app = FastAPI(
    title="SentinelOps API",
    version="0.2.0",
    description="Central Operations Platform with Gamified Checklist System",
    openapi_url="/openapi/v1.json",
)

# -------------------------------------------------------------------
# CORS (safe defaults, env-driven)
# -------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------------------
# Routers
# -------------------------------------------------------------------
app.include_router(auth_router)
app.include_router(checklists_router, prefix="/api/v1")
app.include_router(checklists_file_router, prefix="/api/v1")  # File-based endpoints with same prefix
app.include_router(dashboard_router, prefix="/api/v1")  # Dashboard endpoints
app.include_router(notifications_router, prefix="/api/v1")
app.include_router(gamification_router, prefix="/api/v1")

# -------------------------------------------------------------------
# Startup lifecycle
# -------------------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    """
    Application startup:
    - Initialize database
    - Ensure baseline checklist templates
    - Sync dashboard data from external sources
    """
    log.info("🚀 SentinelOps API starting...")

    await init_db()
    log.info("✅ Database initialized")

    # Lazy import avoids circular dependencies at startup
    from app.checklists.service import ensure_default_templates
    await ensure_default_templates()
    log.info("✅ Default checklist templates ensured")
    
    # Sync dashboard data on startup
    from app.checklists.dashboard_service import DashboardService
    sync_result = DashboardService.sync_dashboard_data()
    log.info(f"✅ Dashboard data sync completed - Weekly: {sync_result['weekly_source']}, Prediction: {sync_result['prediction_source']}")

# -------------------------------------------------------------------
# Health & metadata
# -------------------------------------------------------------------
@app.get("/health")
async def health():
    """System health check endpoint"""
    db_health = await health_check()

    return {
        "status": "healthy",
        "version": app.version,
        "database": db_health,
        "services": {
            "auth": "operational",
            "checklists": "operational",
            "notifications": "operational",
            "gamification": "operational",
        },
    }

@app.get("/")
async def root():
    """Root API metadata"""
    return {
        "name": "SentinelOps Central Operations Platform",
        "version": app.version,
        "description": app.description,
        "openapi": "/openapi/v1.json",
        "endpoints": {
            "auth": "/auth",
            "checklists": "/api/v1/checklists",
            "dashboard": "/api/v1/dashboard",
            "notifications": "/api/v1/notifications",
            "gamification": "/api/v1/gamification",
            "health": "/health",
            "docs": "/docs",
        },
    }

# -------------------------------------------------------------------
# Local execution support
# -------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL,
    )
