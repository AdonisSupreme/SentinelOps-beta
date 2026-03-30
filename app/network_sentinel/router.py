from __future__ import annotations

import asyncio
import csv
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from app.auth.dependencies import get_current_user, get_current_user_websocket
from app.core.authorization import is_admin, is_manager_or_admin
from app.core.logging import get_logger
from app.db.database import get_async_connection
from app.network_sentinel.checks import check_tcp, parse_ping, ping_once
from app.network_sentinel.engine import _derive_overall_status
from app.network_sentinel.history_logs import default_log_dir
from app.network_sentinel.schemas import (
    NetworkServiceCreate,
    NetworkServiceListItem,
    NetworkServiceUpdate,
    OutageItem,
    ServiceCheckNowResponse,
)

log = get_logger("network-sentinel-router")
router = APIRouter(prefix="/network-sentinel", tags=["Network Sentinel"])

_TS_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+\|\s+(?P<body>.+)$")
_UP_RE = re.compile(
    r"^UP \| bytes=(?P<bytes>[^|]+) \| icmp_latency=(?P<icmp_latency>[^|]+) \| TTL=(?P<ttl>[^|]+) \| tcp_latency=(?P<tcp_latency>.+)$"
)
_REC_RE = re.compile(r"^RECOVERED \| Outage lasted (?P<duration>[\d.]+)s$")


def _normalize_outage_details(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"raw": value}
        return _normalize_outage_details(parsed)
    if isinstance(value, (list, tuple)):
        return {"items": list(value)}
    return {"value": value}


def _ensure_manage(user: dict) -> None:
    if not is_manager_or_admin(user):
        raise HTTPException(status_code=403, detail="Manage access required (manager/admin)")


def _ensure_admin(user: dict) -> None:
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")


@router.get("/health")
async def network_engine_health(request: Request, current_user: dict = Depends(get_current_user)):
    engine = getattr(request.app.state, "network_sentinel_engine", None)
    if engine is None:
        return {"online": False, "reason": "engine_not_initialized"}
    return engine.get_health()


@router.websocket("/ws")
async def network_ws(websocket: WebSocket):
    token = websocket.query_params.get("token")
    try:
        user = await get_current_user_websocket(token or "")
    except HTTPException:
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await websocket.accept()
    engine = getattr(websocket.app.state, "network_sentinel_engine", None)
    if engine is None:
        await websocket.send_text(json.dumps({"type": "ENGINE_OFFLINE", "data": {"online": False}}))
        await websocket.close()
        return

    queue = engine.subscribe()
    min_interval = max(0.2, float(websocket.query_params.get("min_interval_seconds", "1.0")))
    last_sent = 0.0

    await websocket.send_text(
        json.dumps(
            {
                "type": "CONNECTION_ESTABLISHED",
                "data": {
                    "user_id": user.get("id"),
                    "engine_health": engine.get_health(),
                },
            }
        )
    )
    try:
        while True:
            message = await queue.get()
            now = asyncio.get_running_loop().time()
            if now - last_sent < min_interval:
                continue
            last_sent = now
            await websocket.send_text(json.dumps(message))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        engine.unsubscribe(queue)


@router.post("/services")
async def create_service(payload: NetworkServiceCreate, current_user: dict = Depends(get_current_user)):
    _ensure_manage(current_user)
    async with get_async_connection() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO network_services (
                name, address, port, enabled, check_icmp, check_tcp, timeout_ms, interval_seconds,
                environment, group_name, owner_team, tags, color, icon, ui_color, ui_icon, notes, description, created_by
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$13,$14,$15,$15,$16
            )
            RETURNING id, created_at
            """,
            payload.name,
            payload.address,
            payload.port,
            payload.enabled,
            payload.check_icmp,
            payload.check_tcp,
            payload.timeout_ms,
            payload.interval_seconds,
            payload.environment,
            payload.group_name,
            payload.owner_team,
            payload.tags,
            payload.color,
            payload.icon,
            payload.notes,
            UUID(current_user["id"]),
        )
    return {"id": str(row["id"]), "created_at": row["created_at"], "message": "Service created"}


@router.patch("/services/{service_id}")
async def update_service(service_id: UUID, payload: NetworkServiceUpdate, current_user: dict = Depends(get_current_user)):
    _ensure_manage(current_user)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided")

    set_clauses: list[str] = []
    values: list[Any] = []
    idx = 1
    for key, val in updates.items():
        if key in {"color", "icon"}:
            set_clauses.append(f"{key} = ${idx}")
            values.append(val)
            idx += 1
            twin = "ui_color" if key == "color" else "ui_icon"
            set_clauses.append(f"{twin} = ${idx}")
            values.append(val)
            idx += 1
            continue
        set_clauses.append(f"{key} = ${idx}")
        values.append(val)
        idx += 1

    values.append(service_id)
    query = f"UPDATE network_services SET {', '.join(set_clauses)} WHERE id = ${idx} AND deleted_at IS NULL RETURNING id, updated_at"

    async with get_async_connection() as conn:
        row = await conn.fetchrow(query, *values)
    if not row:
        raise HTTPException(status_code=404, detail="Service not found")
    return {"id": str(row["id"]), "updated_at": row["updated_at"], "message": "Service updated"}


@router.delete("/services/{service_id}")
async def delete_service(service_id: UUID, current_user: dict = Depends(get_current_user)):
    _ensure_admin(current_user)
    async with get_async_connection() as conn:
        row = await conn.fetchrow(
            "UPDATE network_services SET deleted_at = now(), enabled = false WHERE id = $1 AND deleted_at IS NULL RETURNING id",
            service_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Service not found")
    return {"id": str(service_id), "message": "Service deleted"}


@router.post("/services/{service_id}/enable")
async def set_service_enabled(
    service_id: UUID,
    enabled: bool = Query(...),
    current_user: dict = Depends(get_current_user),
):
    _ensure_manage(current_user)
    async with get_async_connection() as conn:
        row = await conn.fetchrow(
            "UPDATE network_services SET enabled = $2 WHERE id = $1 AND deleted_at IS NULL RETURNING id, enabled, updated_at",
            service_id,
            enabled,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Service not found")
    return {"id": str(row["id"]), "enabled": row["enabled"], "updated_at": row["updated_at"]}


@router.get("/services")
async def list_services(
    group_name: str | None = Query(default=None),
    environment: str | None = Query(default=None),
    status: str | None = Query(default=None, pattern="^(UNKNOWN|UP|DEGRADED|DOWN)$"),
    tag: str | None = Query(default=None),
    search: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: dict = Depends(get_current_user),
) -> list[NetworkServiceListItem]:
    where = ["s.deleted_at IS NULL"]
    params: list[Any] = []
    i = 1
    if group_name:
        where.append(f"s.group_name = ${i}")
        params.append(group_name)
        i += 1
    if environment:
        where.append(f"s.environment = ${i}")
        params.append(environment)
        i += 1
    if status:
        where.append(f"st.overall_status::text = ${i}")
        params.append(status)
        i += 1
    if tag:
        where.append(f"${i} = ANY(COALESCE(s.tags, ARRAY[]::text[]))")
        params.append(tag)
        i += 1
    if search:
        where.append(f"(s.name ILIKE ${i} OR s.address ILIKE ${i} OR COALESCE(s.notes,'') ILIKE ${i})")
        params.append(f"%{search}%")
        i += 1
    if enabled is not None:
        where.append(f"s.enabled = ${i}")
        params.append(enabled)
        i += 1

    params.extend([limit, offset])
    async with get_async_connection() as conn:
        rows = await conn.fetch(
            f"""
            SELECT
                s.id, s.name, s.address, s.port, s.enabled, s.check_icmp, s.check_tcp, s.timeout_ms, s.interval_seconds,
                s.environment, s.group_name, s.owner_team, COALESCE(s.tags, ARRAY[]::text[]) AS tags,
                COALESCE(s.color, s.ui_color) AS color, COALESCE(s.icon, s.ui_icon) AS icon, COALESCE(s.notes, s.description) AS notes,
                s.created_at, s.updated_at,
                st.last_checked_at, st.icmp_up, st.icmp_bytes, st.icmp_latency_ms, st.icmp_ttl, st.tcp_up, st.tcp_latency_ms,
                st.overall_status::text AS overall_status, st.reason, st.consecutive_failures, st.last_state_change_at
            FROM network_services s
            LEFT JOIN network_service_status st ON st.service_id = s.id
            WHERE {" AND ".join(where)}
            ORDER BY s.created_at ASC
            LIMIT ${i} OFFSET ${i+1}
            """,
            *params,
        )
    output: list[NetworkServiceListItem] = []
    for r in rows:
        output.append(
            NetworkServiceListItem(
                id=r["id"],
                name=r["name"],
                address=r["address"],
                port=r["port"],
                enabled=r["enabled"],
                check_icmp=r["check_icmp"],
                check_tcp=r["check_tcp"],
                timeout_ms=r["timeout_ms"],
                interval_seconds=r["interval_seconds"],
                environment=r["environment"],
                group_name=r["group_name"],
                owner_team=r["owner_team"],
                tags=list(r["tags"] or []),
                color=r["color"],
                icon=r["icon"],
                notes=r["notes"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                status={
                    "last_checked_at": r["last_checked_at"].isoformat() if r["last_checked_at"] else None,
                    "icmp_up": r["icmp_up"],
                    "icmp_bytes": r["icmp_bytes"],
                    "icmp_latency_ms": r["icmp_latency_ms"],
                    "icmp_ttl": r["icmp_ttl"],
                    "tcp_up": r["tcp_up"],
                    "tcp_latency_ms": r["tcp_latency_ms"],
                    "overall_status": r["overall_status"],
                    "reason": r["reason"],
                    "consecutive_failures": r["consecutive_failures"],
                    "last_state_change_at": r["last_state_change_at"].isoformat() if r["last_state_change_at"] else None,
                }
                if r["overall_status"]
                else None,
            )
        )
    return output


@router.post("/services/{service_id}/check-now", response_model=ServiceCheckNowResponse)
async def check_now(service_id: UUID, current_user: dict = Depends(get_current_user)):
    _ensure_manage(current_user)
    async with get_async_connection() as conn:
        r = await conn.fetchrow(
            """
            SELECT id, name, address, port, check_icmp, check_tcp, timeout_ms
            FROM network_services
            WHERE id = $1 AND deleted_at IS NULL
            """,
            service_id,
        )
    if not r:
        raise HTTPException(status_code=404, detail="Service not found")

    icmp = None
    tcp = None
    if r["check_icmp"]:
        ping_output = await asyncio.to_thread(ping_once, r["address"], int(r["timeout_ms"]))
        icmp = parse_ping(ping_output)
    if r["check_tcp"] and r["port"] is not None:
        tcp = await check_tcp(r["address"], int(r["port"]), int(r["timeout_ms"]))

    class _TempSvc:
        check_icmp = bool(r["check_icmp"])
        check_tcp = bool(r["check_tcp"])

    overall_status, reason, _ = _derive_overall_status(_TempSvc(), icmp.up if icmp else None, tcp.up if tcp else None)
    checked_at = datetime.now(timezone.utc)
    return ServiceCheckNowResponse(
        service_id=service_id,
        checked_at=checked_at,
        overall_status=overall_status,  # type: ignore[arg-type]
        reason=reason,
        icmp={
            "up": icmp.up,
            "bytes": icmp.bytes_val,
            "latency_ms": icmp.latency_ms,
            "ttl": icmp.ttl,
        }
        if icmp
        else None,
        tcp={
            "up": tcp.up,
            "latency_ms": tcp.latency_ms,
        }
        if tcp
        else None,
    )


def _parse_log_line(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    m = _TS_RE.match(line)
    if not m:
        return None
    body = m.group("body")
    entry: dict[str, Any] = {"timestamp": m.group("ts"), "raw": line}
    up = _UP_RE.match(body)
    if up:
        entry["kind"] = "UP"
        entry["bytes"] = up.group("bytes").strip()
        entry["icmp_latency"] = up.group("icmp_latency").strip()
        entry["ttl"] = up.group("ttl").strip()
        entry["tcp_latency"] = up.group("tcp_latency").strip()
        return entry
    if body == "DOWN":
        entry["kind"] = "DOWN"
        return entry
    if body == "DEGRADED":
        entry["kind"] = "DEGRADED"
        return entry
    if body == "OUTAGE DETECTED":
        entry["kind"] = "OUTAGE_DETECTED"
        return entry
    rec = _REC_RE.match(body)
    if rec:
        entry["kind"] = "RECOVERED"
        entry["duration_seconds"] = float(rec.group("duration"))
        return entry
    entry["kind"] = "OTHER"
    return entry


def _read_service_logs(service_id: UUID, start: datetime | None, end: datetime | None) -> list[dict[str, Any]]:
    root = default_log_dir(Path(__file__).resolve().parents[2])
    files = sorted(root.glob(f"network_log_{service_id}_*.txt"))
    out: list[dict[str, Any]] = []
    start_utc = start.astimezone(timezone.utc) if start and start.tzinfo else (start.replace(tzinfo=timezone.utc) if start else None)
    end_utc = end.astimezone(timezone.utc) if end and end.tzinfo else (end.replace(tzinfo=timezone.utc) if end else None)
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                for ln in fh:
                    parsed = _parse_log_line(ln)
                    if not parsed:
                        continue
                    ts = datetime.strptime(parsed["timestamp"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    if start_utc and ts < start_utc:
                        continue
                    if end_utc and ts > end_utc:
                        continue
                    parsed["timestamp"] = ts.isoformat()
                    parsed["file"] = f.name
                    out.append(parsed)
        except Exception:
            continue
    return out


@router.get("/history/{service_id}")
async def service_history(
    service_id: UUID,
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    limit: int = Query(default=5000, ge=1, le=20000),
    format: str = Query(default="json", pattern="^(json|csv)$"),
    current_user: dict = Depends(get_current_user),
):
    rows = _read_service_logs(service_id, start_at, end_at)
    if len(rows) > limit:
        rows = rows[-limit:]
    if format == "json":
        return {"service_id": str(service_id), "count": len(rows), "rows": rows}

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["timestamp", "kind", "bytes", "icmp_latency", "ttl", "tcp_latency", "duration_seconds", "file", "raw"])
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=history_{service_id}.csv"})


@router.get("/outages", response_model=list[OutageItem])
async def list_outages(
    service_id: UUID | None = Query(default=None),
    active_only: bool = Query(default=False),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    where = ["1=1"]
    params: list[Any] = []
    i = 1
    if service_id:
        where.append(f"service_id = ${i}")
        params.append(service_id)
        i += 1
    if active_only:
        where.append("ended_at IS NULL")
    if start_at:
        where.append(f"started_at >= ${i}")
        params.append(start_at)
        i += 1
    if end_at:
        where.append(f"COALESCE(ended_at, now()) <= ${i}")
        params.append(end_at)
        i += 1
    params.extend([limit, offset])
    async with get_async_connection() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, service_id, started_at, ended_at, duration_seconds, cause::text AS cause, details
            FROM network_service_outages
            WHERE {" AND ".join(where)}
            ORDER BY started_at DESC
            LIMIT ${i} OFFSET ${i+1}
            """,
            *params,
        )
    return [
        OutageItem(
            id=r["id"],
            service_id=r["service_id"],
            started_at=r["started_at"],
            ended_at=r["ended_at"],
            duration_seconds=r["duration_seconds"],
            cause=r["cause"],
            details=_normalize_outage_details(r["details"]),
        )
        for r in rows
    ]

