from pathlib import Path
import sys
import asyncio
from zoneinfo import ZoneInfo

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
## File-based checklist router import removed (system now uses DB only)
from app.checklists.dashboard_router import router as dashboard_router  # Dashboard stats router
from app.notifications.router import router as notifications_router
from app.gamification.router import router as gamification_router
from app.users.router import router as users_router
from app.org.router import router as org_router
from app.tasks.router import router as tasks_router  # Task management router
from app.api.pdf_endpoints import router as pdf_router  # PDF generation endpoints
from app.trustlink.router import router as trustlink_router
from app.network_sentinel.router import router as network_sentinel_router

# DB lifecycle
from app.db.database import init_db, health_check
# APScheduler for scheduled Trustlink runs
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
except Exception:
    AsyncIOScheduler = None

log = get_logger("main")

# Network Sentinel (stage 1+2: multi-service monitoring engine)
try:
    from app.network_sentinel.engine import NetworkSentinelEngine
except Exception:
    NetworkSentinelEngine = None

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
## Routers
app.include_router(auth_router)
# Only the database-backed checklist router is active. File-based routers are deprecated/removed.
app.include_router(checklists_router, prefix="/api/v1")
app.include_router(dashboard_router, prefix="/api/v1")  # Dashboard endpoints
app.include_router(notifications_router, prefix="/api/v1")
app.include_router(gamification_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(org_router, prefix="/api/v1")
app.include_router(tasks_router, prefix="/api/v1")  # Task management endpoints
app.include_router(trustlink_router, prefix="/api/v1")
app.include_router(pdf_router)  # PDF generation endpoints
app.include_router(network_sentinel_router, prefix="/api/v1")

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

    # --- Scheduler: Trustlink daily run at 07:00 ---
    if AsyncIOScheduler is not None:
        try:
            from app.trustlink.workflow import TrustlinkWorkflow
            from app.checklists.automation_service import ChecklistAutomationService
            from app.tasks.service import TaskService

            scheduler_timezone = ZoneInfo(settings.TRUSTLINK_SCHEDULE_TIMEZONE)
            scheduler = AsyncIOScheduler(timezone=scheduler_timezone)

            async def _trustlink_job():
                try:
                    loop = asyncio.get_running_loop()
                    # Run blocking workflow in executor to avoid blocking event loop
                    await loop.run_in_executor(None, TrustlinkWorkflow.run_extraction, "scheduled", None, False)
                except Exception as exc:
                    log.error(f"Scheduled Trustlink job failed: {exc}")

            # Schedule daily at 07:00 configured business timezone
            scheduler.add_job(_trustlink_job, "cron", hour=7, minute=0, id="trustlink_daily_run", replace_existing=True)

            async def _checklist_daily_init_job():
                try:
                    await ChecklistAutomationService.initialize_daily_shift_instances()
                except Exception as exc:
                    log.error(f"Scheduled checklist initialization job failed: {exc}")

            async def _checklist_timed_reminder_job():
                try:
                    await ChecklistAutomationService.process_due_timed_reminders()
                except Exception as exc:
                    log.error(f"Scheduled checklist timed reminder job failed: {exc}")

            async def _task_due_reminder_job():
                try:
                    await TaskService.process_due_task_reminders()
                except Exception as exc:
                    log.error(f"Scheduled task reminder job failed: {exc}")

            # Schedule daily at 06:00 configured business timezone
            scheduler.add_job(
                _checklist_daily_init_job,
                "cron",
                hour=6,
                minute=0,
                id="checklist_daily_initialize",
                replace_existing=True,
            )
            scheduler.add_job(
                _checklist_timed_reminder_job,
                "interval",
                seconds=max(30, int(settings.NOTIFICATION_CHECK_INTERVAL)),
                id="checklist_timed_reminders",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
            scheduler.add_job(
                _task_due_reminder_job,
                "interval",
                seconds=max(30, int(settings.NOTIFICATION_CHECK_INTERVAL)),
                id="task_due_reminders",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
            scheduler.start()
            app.state.trustlink_scheduler = scheduler
            log.info(
                f"✅ Schedulers started (checklist 06:00 + trustlink 07:00, timezone={settings.TRUSTLINK_SCHEDULE_TIMEZONE})"
            )

            try:
                catchup_result = await ChecklistAutomationService.initialize_daily_shift_instances()
                log.info("✅ Checklist startup catch-up complete: %s", catchup_result)
            except Exception as exc:
                log.error(f"Checklist startup catch-up failed: {exc}")
        except Exception as e:
            log.error(f"Failed to start Trustlink scheduler: {e}")
    else:
        log.warning("apscheduler not available; Trustlink scheduled jobs disabled")

    # --- Background: Network Sentinel engine ---
    if NetworkSentinelEngine is not None:
        try:
            # Project root is already computed above; reuse it for consistent log placement.
            engine = NetworkSentinelEngine(project_root=PROJECT_ROOT)
            app.state.network_sentinel_engine = engine
            app.state.network_sentinel_task = asyncio.create_task(engine.run_forever())
            log.info("✅ Network Sentinel engine started")
        except Exception as e:
            log.error(f"Failed to start Network Sentinel engine: {e}")
    else:
        log.warning("Network Sentinel engine not available; monitoring disabled")

    # --- Diagnostic: confirm attachment download route registered ---
    try:
        download_route = None
        for r in app.router.routes:
            # route.path is available on Starlette routes; check for our download pattern
            path = getattr(r, 'path', None) or getattr(r, 'name', None)
            if path and 'attachments' in str(path) and 'download' in str(path):
                download_route = str(path)
                break

        if download_route:
            log.info(f"✅ Attachment download route registered: {download_route}")
        else:
            log.warning("⚠️ Attachment download route NOT found at startup. Check router registration and reload behavior.")
    except Exception as e:
        log.error(f"Failed to check download route registration: {e}")

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
            "pdf": "/api/pdf",
            "health": "/health",
            "docs": "/docs",
        },
    }


@app.on_event("shutdown")
async def shutdown_event():
    """Application shutdown: stop scheduler if running."""
    try:
        sched = getattr(app.state, "trustlink_scheduler", None)
        if sched:
            try:
                sched.shutdown(wait=False)
                log.info("✅ Trustlink scheduler shutdown")
            except Exception as e:
                log.error(f"Error shutting down Trustlink scheduler: {e}")
    except Exception as e:
        log.error(f"Error during shutdown_event: {e}")

    # Stop network sentinel cleanly
    try:
        engine = getattr(app.state, "network_sentinel_engine", None)
        task = getattr(app.state, "network_sentinel_task", None)
        if engine:
            await engine.stop()
        if task:
            task.cancel()
        log.info("✅ Network Sentinel engine stopped")
    except Exception as e:
        log.error(f"Error stopping Network Sentinel engine: {e}")

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
