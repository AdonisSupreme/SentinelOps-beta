# app/db/database.py

import psycopg
import asyncpg
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Generator

from app.core.logging import get_logger
from app.core.config import settings

log = get_logger("database")

# =====================================================
# SYNC DATABASE (LEGACY / EXISTING CODE)
# =====================================================

def get_connection() -> Generator[psycopg.Connection, None, None]:
    """
    Legacy synchronous database connection.

    DO NOT MODIFY:
    - Used by existing code
    - psycopg-based
    """
    log.info("🔌 Opening sync database connection")
    conn = psycopg.connect(settings.DATABASE_URL)
    # Ensure UTC timezone for all operations
    with conn.cursor() as cur:
        cur.execute("SET timezone = 'UTC'")
        conn.commit()
    return conn

# =====================================================
# ASYNC DATABASE (CHECKLISTS / NEW SYSTEMS)
# =====================================================

_async_pool: asyncpg.Pool | None = None


async def init_db() -> None:
    """
    Initialize async database connection pool.
    Safe to call multiple times (idempotent).
    """
    global _async_pool

    if _async_pool is not None:
        return

    try:
        _async_pool = await asyncpg.create_pool(
            dsn=settings.DATABASE_URL,
            min_size=1,
            max_size=10,
            command_timeout=80,
        )

        # Sanity check
        async with _async_pool.acquire() as conn:
            await conn.execute("SELECT 1")

        log.info("✅ Async database connection pool initialized")

    except Exception as e:
        log.error(f"❌ Async database initialization failed: {e}")
        raise


@asynccontextmanager
async def get_async_connection() -> AsyncGenerator[asyncpg.Connection, None]:
    """
    Async database connection (pool-backed).

    Used by:
    - Checklists
    - Notifications
    - Gamification
    """
    global _async_pool

    if _async_pool is None:
        await init_db()

    assert _async_pool is not None

    conn = await _async_pool.acquire()
    try:
        yield conn
    finally:
        await _async_pool.release(conn)


# =====================================================
# HEALTH CHECK (ASYNC)
# =====================================================

async def health_check() -> dict:
    """
    Database health check for monitoring & probes.
    """
    try:
        async with get_async_connection() as conn:
            await conn.fetchval("SELECT 1")

            stats = await conn.fetchrow(
                """
                SELECT 
                    (SELECT COUNT(*) FROM checklist_instances) AS total_instances,
                    (SELECT COUNT(*) FROM checklist_instances WHERE status != 'COMPLETED') AS active_instances,
                    (SELECT COUNT(*) FROM users WHERE is_active = true) AS active_users
                """
            )

            return {
                "status": "healthy",
                "connection": "ok",
                "stats": {
                    "total_instances": stats["total_instances"],
                    "active_instances": stats["active_instances"],
                    "active_users": stats["active_users"],
                },
            }

    except Exception as e:
        log.error(f"❌ Database health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
        }

