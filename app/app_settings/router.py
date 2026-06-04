from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user
from app.core.authorization import is_admin
from app.core.config import settings
from app.db.database import get_connection


router = APIRouter(prefix="/app-settings", tags=["Application Settings"])

TIMEZONE_KEY = "application_timezone"
DEFAULT_TIMEZONE = settings.APPLICATION_TIMEZONE or "Africa/Harare"
RECOMMENDED_TIMEZONES = [
    "Africa/Harare",
    "Africa/Johannesburg",
    "Africa/Lusaka",
    "Africa/Maputo",
    "UTC",
]


class TimezoneUpdate(BaseModel):
    timezone: str = Field(..., min_length=2, max_length=80)


def _validate_timezone(value: str) -> str:
    candidate = value.strip()
    try:
        ZoneInfo(candidate)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=400, detail=f"Unsupported IANA timezone: {candidate}") from exc
    return candidate


def _read_timezone() -> dict[str, object]:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT setting_value, updated_by, updated_at
                    FROM app_settings
                    WHERE setting_key = %s
                    """,
                    (TIMEZONE_KEY,),
                )
                row = cur.fetchone()
    except Exception:
        row = None

    configured_timezone = DEFAULT_TIMEZONE
    updated_by = "environment"
    updated_at = None
    if row:
        payload = row[0] or {}
        if isinstance(payload, dict):
            configured_timezone = str(payload.get("timezone") or DEFAULT_TIMEZONE)
        updated_by = row[1] or "database"
        updated_at = row[2]

    configured_timezone = _validate_timezone(configured_timezone)
    now_utc = datetime.now(timezone.utc)
    return {
        "timezone": configured_timezone,
        "default_timezone": DEFAULT_TIMEZONE,
        "recommended_timezones": RECOMMENDED_TIMEZONES,
        "updated_by": updated_by,
        "updated_at": updated_at.isoformat() if updated_at else None,
        "server_utc": now_utc.isoformat(),
        "server_local": now_utc.astimezone(ZoneInfo(configured_timezone)).isoformat(),
    }


@router.get("/timezone")
async def get_application_timezone(current_user: dict = Depends(get_current_user)) -> dict[str, object]:
    return _read_timezone()


@router.put("/timezone")
async def update_application_timezone(
    payload: TimezoneUpdate,
    current_user: dict = Depends(get_current_user),
) -> dict[str, object]:
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Only administrators can update the SentinelOps application timezone.")

    timezone_name = _validate_timezone(payload.timezone)
    actor = current_user.get("username") or current_user.get("email") or current_user.get("id") or "admin"
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES (%s, %s::jsonb, %s, %s, now())
                ON CONFLICT (setting_key) DO UPDATE SET
                    setting_value = EXCLUDED.setting_value,
                    description = EXCLUDED.description,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = now()
                """,
                (
                    TIMEZONE_KEY,
                    json.dumps({"timezone": timezone_name}),
                    "IANA timezone used for operator-facing SentinelOps date/time display.",
                    str(actor),
                ),
            )
            conn.commit()

    return _read_timezone()
