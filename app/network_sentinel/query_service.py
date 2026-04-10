from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.db.database import get_async_connection


def _normalize_details(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"raw": value}
        return _normalize_details(parsed)
    if isinstance(value, (list, tuple)):
        return {"items": list(value)}
    return {"value": value}


def _serialize_status(row: Any) -> dict[str, Any]:
    row = dict(row)
    return {
        "last_checked_at": row["last_checked_at"].isoformat() if row.get("last_checked_at") else None,
        "icmp_up": row.get("icmp_up"),
        "icmp_bytes": row.get("icmp_bytes"),
        "icmp_latency_ms": row.get("icmp_latency_ms"),
        "icmp_ttl": row.get("icmp_ttl"),
        "tcp_up": row.get("tcp_up"),
        "tcp_latency_ms": row.get("tcp_latency_ms"),
        "overall_status": row.get("overall_status") or "UNKNOWN",
        "reason": row.get("reason"),
        "consecutive_failures": int(row.get("consecutive_failures") or 0),
        "last_state_change_at": row["last_state_change_at"].isoformat() if row.get("last_state_change_at") else None,
    }


def _serialize_service(row: Any) -> dict[str, Any]:
    row = dict(row)
    total_samples_24h = int(row.get("total_samples_24h") or 0)
    up_samples_24h = int(row.get("up_samples_24h") or 0)
    degraded_samples_24h = int(row.get("degraded_samples_24h") or 0)
    down_samples_24h = int(row.get("down_samples_24h") or 0)
    uptime_percent_24h = round((up_samples_24h / total_samples_24h) * 100, 1) if total_samples_24h else None

    return {
        "id": str(row["id"]),
        "name": row["name"],
        "address": row["address"],
        "port": row["port"],
        "enabled": row["enabled"],
        "check_icmp": row["check_icmp"],
        "check_tcp": row["check_tcp"],
        "timeout_ms": row["timeout_ms"],
        "interval_seconds": row["interval_seconds"],
        "environment": row["environment"],
        "group_name": row["group_name"],
        "owner_team": row["owner_team"],
        "tags": list(row.get("tags") or []),
        "color": row.get("color"),
        "icon": row.get("icon"),
        "notes": row.get("notes"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        "status": _serialize_status(row),
        "active_outage": {
            "started_at": row["active_outage_started_at"].isoformat(),
            "cause": row.get("active_outage_cause"),
            "duration_seconds": int(row.get("active_outage_duration_seconds") or 0),
        }
        if row.get("active_outage_started_at")
        else None,
        "metrics": {
            "uptime_percent_24h": uptime_percent_24h,
            "total_samples_24h": total_samples_24h,
            "up_samples_24h": up_samples_24h,
            "degraded_samples_24h": degraded_samples_24h,
            "down_samples_24h": down_samples_24h,
            "avg_icmp_latency_ms_24h": round(float(row["avg_icmp_latency_ms_24h"]), 1)
            if row.get("avg_icmp_latency_ms_24h") is not None
            else None,
            "avg_tcp_latency_ms_24h": round(float(row["avg_tcp_latency_ms_24h"]), 1)
            if row.get("avg_tcp_latency_ms_24h") is not None
            else None,
        },
    }


def _serialize_event(row: Any) -> dict[str, Any]:
    row = dict(row)
    return {
        "id": str(row["id"]),
        "service_id": str(row["service_id"]) if row.get("service_id") else None,
        "service_name": row.get("service_name"),
        "service_address": row.get("service_address"),
        "service_port": row.get("service_port"),
        "category": row.get("category"),
        "event_type": row.get("event_type"),
        "severity": row.get("severity"),
        "title": row.get("title"),
        "summary": row.get("summary"),
        "details": _normalize_details(row.get("details")),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


def _serialize_outage(row: Any) -> dict[str, Any]:
    row = dict(row)
    return {
        "id": str(row["id"]),
        "service_id": str(row["service_id"]),
        "service_name": row.get("service_name"),
        "service_address": row.get("service_address"),
        "service_port": row.get("service_port"),
        "started_at": row["started_at"].isoformat() if row.get("started_at") else None,
        "ended_at": row["ended_at"].isoformat() if row.get("ended_at") else None,
        "duration_seconds": row.get("duration_seconds"),
        "cause": row.get("cause"),
        "details": _normalize_details(row.get("details")),
    }


def _serialize_sample(row: Any) -> dict[str, Any]:
    row = dict(row)
    return {
        "timestamp": row["sampled_at"].isoformat() if row.get("sampled_at") else None,
        "overall_status": row.get("overall_status") or "UNKNOWN",
        "icmp_up": row.get("icmp_up"),
        "icmp_bytes": row.get("icmp_bytes"),
        "icmp_latency_ms": row.get("icmp_latency_ms"),
        "icmp_ttl": row.get("icmp_ttl"),
        "tcp_up": row.get("tcp_up"),
        "tcp_latency_ms": row.get("tcp_latency_ms"),
        "reason": row.get("reason"),
        "consecutive_failures": int(row.get("consecutive_failures") or 0),
    }


def _build_overview(services: list[dict[str, Any]], active_outages: list[dict[str, Any]], recent_events: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"UP": 0, "DEGRADED": 0, "DOWN": 0, "UNKNOWN": 0}
    enabled_services = [service for service in services if service["enabled"]]
    for service in services:
        status = ((service.get("status") or {}).get("overall_status")) or "UNKNOWN"
        counts[status] = counts.get(status, 0) + 1

    scoring = {"UP": 100, "DEGRADED": 64, "UNKNOWN": 35, "DOWN": 0}
    if enabled_services:
        fleet_pulse = round(
            sum(scoring.get(((service.get("status") or {}).get("overall_status")) or "UNKNOWN", 35) for service in enabled_services)
            / len(enabled_services)
        )
        average_interval = round(sum(int(service.get("interval_seconds") or 0) for service in enabled_services) / len(enabled_services), 1)
    else:
        fleet_pulse = 0
        average_interval = None

    return {
        "total_services": len(services),
        "enabled_services": len(enabled_services),
        "up_services": counts["UP"],
        "degraded_services": counts["DEGRADED"],
        "down_services": counts["DOWN"],
        "unknown_services": counts["UNKNOWN"],
        "impaired_services": counts["DOWN"] + counts["DEGRADED"],
        "active_incidents": len(active_outages),
        "fleet_pulse": fleet_pulse,
        "average_interval_seconds": average_interval,
        "recent_event_count": len(recent_events),
    }


_SERVICE_SNAPSHOT_SQL = """
WITH sample_window AS (
    SELECT
        service_id,
        COUNT(*) AS total_samples_24h,
        COUNT(*) FILTER (WHERE overall_status = 'UP') AS up_samples_24h,
        COUNT(*) FILTER (WHERE overall_status = 'DEGRADED') AS degraded_samples_24h,
        COUNT(*) FILTER (WHERE overall_status = 'DOWN') AS down_samples_24h,
        AVG(NULLIF(icmp_latency_ms, 0)) AS avg_icmp_latency_ms_24h,
        AVG(NULLIF(tcp_latency_ms, 0)) AS avg_tcp_latency_ms_24h
    FROM network_service_samples
    WHERE sampled_at >= now() - interval '24 hours'
    GROUP BY service_id
),
active_outage AS (
    SELECT DISTINCT ON (service_id)
        service_id,
        started_at AS active_outage_started_at,
        cause::text AS active_outage_cause,
        EXTRACT(EPOCH FROM (now() - started_at))::int AS active_outage_duration_seconds
    FROM network_service_outages
    WHERE ended_at IS NULL
    ORDER BY service_id, started_at DESC
)
SELECT
    s.id,
    s.name,
    s.address,
    s.port,
    s.enabled,
    s.check_icmp,
    s.check_tcp,
    s.timeout_ms,
    s.interval_seconds,
    s.environment,
    s.group_name,
    s.owner_team,
    COALESCE(s.tags, ARRAY[]::text[]) AS tags,
    COALESCE(s.color, s.ui_color) AS color,
    COALESCE(s.icon, s.ui_icon) AS icon,
    COALESCE(s.notes, s.description) AS notes,
    s.created_at,
    s.updated_at,
    st.last_checked_at,
    st.icmp_up,
    st.icmp_bytes,
    st.icmp_latency_ms,
    st.icmp_ttl,
    st.tcp_up,
    st.tcp_latency_ms,
    COALESCE(st.overall_status::text, 'UNKNOWN') AS overall_status,
    st.reason,
    st.consecutive_failures,
    st.last_state_change_at,
    sw.total_samples_24h,
    sw.up_samples_24h,
    sw.degraded_samples_24h,
    sw.down_samples_24h,
    sw.avg_icmp_latency_ms_24h,
    sw.avg_tcp_latency_ms_24h,
    ao.active_outage_started_at,
    ao.active_outage_cause,
    ao.active_outage_duration_seconds
FROM network_services s
LEFT JOIN network_service_status st ON st.service_id = s.id
LEFT JOIN sample_window sw ON sw.service_id = s.id
LEFT JOIN active_outage ao ON ao.service_id = s.id
WHERE s.deleted_at IS NULL
"""


async def fetch_network_command_center() -> dict[str, Any]:
    async with get_async_connection() as conn:
        service_rows = await conn.fetch(
            _SERVICE_SNAPSHOT_SQL
            + """
            ORDER BY
                CASE COALESCE(st.overall_status::text, 'UNKNOWN')
                    WHEN 'DOWN' THEN 1
                    WHEN 'DEGRADED' THEN 2
                    WHEN 'UNKNOWN' THEN 3
                    ELSE 4
                END,
                s.enabled DESC,
                s.name ASC
            """
        )
        active_outage_rows = await conn.fetch(
            """
            SELECT
                o.id,
                o.service_id,
                s.name AS service_name,
                s.address AS service_address,
                s.port AS service_port,
                o.started_at,
                o.ended_at,
                COALESCE(o.duration_seconds, EXTRACT(EPOCH FROM (now() - o.started_at))::int) AS duration_seconds,
                o.cause::text AS cause,
                o.details
            FROM network_service_outages o
            JOIN network_services s ON s.id = o.service_id
            WHERE o.ended_at IS NULL
              AND s.deleted_at IS NULL
            ORDER BY o.started_at ASC
            LIMIT 12
            """
        )
        recent_event_rows = await conn.fetch(
            """
            SELECT
                e.id,
                e.service_id,
                COALESCE(s.name, e.service_name) AS service_name,
                COALESCE(s.address, e.service_address) AS service_address,
                COALESCE(s.port, e.service_port) AS service_port,
                e.category,
                e.event_type,
                e.severity,
                e.title,
                e.summary,
                e.details,
                e.created_at
            FROM network_service_events e
            LEFT JOIN network_services s ON s.id = e.service_id
            ORDER BY e.created_at DESC
            LIMIT 16
            """
        )

    services = [_serialize_service(row) for row in service_rows]
    active_outages = [_serialize_outage(row) for row in active_outage_rows]
    recent_events = [_serialize_event(row) for row in recent_event_rows]
    return {
        "overview": _build_overview(services, active_outages, recent_events),
        "retention": {
            "raw_history_days": settings.NETWORK_SENTINEL_RAW_RETENTION_DAYS,
            "sample_history_days": settings.NETWORK_SENTINEL_SAMPLE_RETENTION_DAYS,
            "event_history_days": settings.NETWORK_SENTINEL_EVENT_RETENTION_DAYS,
            "outage_history_days": settings.NETWORK_SENTINEL_OUTAGE_RETENTION_DAYS,
            "sample_interval_seconds": settings.NETWORK_SENTINEL_SAMPLE_INTERVAL_SECONDS,
        },
        "services": services,
        "active_outages": active_outages,
        "recent_events": recent_events,
    }


async def fetch_service_investigation(
    service_id: UUID,
    *,
    horizon_hours: int,
    sample_limit: int,
    event_limit: int,
    outage_limit: int,
) -> dict[str, Any] | None:
    async with get_async_connection() as conn:
        service_row = await conn.fetchrow(_SERVICE_SNAPSHOT_SQL + " AND s.id = $1", service_id)
        if not service_row:
            return None

        metric_row = await conn.fetchrow(
            """
            SELECT
                COUNT(*) AS total_samples_24h,
                COUNT(*) FILTER (WHERE overall_status = 'UP') AS up_samples_24h,
                COUNT(*) FILTER (WHERE overall_status = 'DEGRADED') AS degraded_samples_24h,
                COUNT(*) FILTER (WHERE overall_status = 'DOWN') AS down_samples_24h,
                AVG(NULLIF(icmp_latency_ms, 0)) AS avg_icmp_latency_ms_24h,
                AVG(NULLIF(tcp_latency_ms, 0)) AS avg_tcp_latency_ms_24h,
                (
                    SELECT COUNT(*)
                    FROM network_service_outages
                    WHERE service_id = $1
                      AND started_at >= now() - ($2::int * interval '1 day')
                ) AS outage_count_diagnostic_window
            FROM network_service_samples
            WHERE service_id = $1
              AND sampled_at >= now() - interval '24 hours'
            """,
            service_id,
            settings.NETWORK_SENTINEL_OUTAGE_RETENTION_DAYS,
        )
        metric_row = dict(metric_row) if metric_row else None
        sample_rows = await conn.fetch(
            """
            SELECT
                sampled_at,
                overall_status::text AS overall_status,
                icmp_up,
                icmp_bytes,
                icmp_latency_ms,
                icmp_ttl,
                tcp_up,
                tcp_latency_ms,
                reason,
                consecutive_failures
            FROM network_service_samples
            WHERE service_id = $1
              AND sampled_at >= now() - ($2::int * interval '1 hour')
            ORDER BY sampled_at DESC
            LIMIT $3
            """,
            service_id,
            horizon_hours,
            sample_limit,
        )
        event_rows = await conn.fetch(
            """
            SELECT
                id,
                service_id,
                COALESCE(service_name, $2) AS service_name,
                service_address,
                service_port,
                category,
                event_type,
                severity,
                title,
                summary,
                details,
                created_at
            FROM network_service_events
            WHERE service_id = $1
            ORDER BY created_at DESC
            LIMIT $3
            """,
            service_id,
            service_row["name"],
            event_limit,
        )
        outage_rows = await conn.fetch(
            """
            SELECT
                id,
                service_id,
                $2::text AS service_name,
                $3::text AS service_address,
                $4::integer AS service_port,
                started_at,
                ended_at,
                duration_seconds,
                cause::text AS cause,
                details
            FROM network_service_outages
            WHERE service_id = $1
            ORDER BY started_at DESC
            LIMIT $5
            """,
            service_id,
            service_row["name"],
            service_row["address"],
            service_row["port"],
            outage_limit,
        )

    total_samples_24h = int(metric_row["total_samples_24h"] or 0) if metric_row else 0
    up_samples_24h = int(metric_row["up_samples_24h"] or 0) if metric_row else 0
    degraded_samples_24h = int(metric_row["degraded_samples_24h"] or 0) if metric_row else 0
    down_samples_24h = int(metric_row["down_samples_24h"] or 0) if metric_row else 0
    availability_24h = round((up_samples_24h / total_samples_24h) * 100, 1) if total_samples_24h else None

    samples = list(reversed([_serialize_sample(row) for row in sample_rows]))
    events = [_serialize_event(row) for row in event_rows]
    outages = [_serialize_outage(row) for row in outage_rows]
    return {
        "service": _serialize_service(service_row),
        "metrics": {
            "availability_percent_24h": availability_24h,
            "total_samples_24h": total_samples_24h,
            "degraded_samples_24h": degraded_samples_24h,
            "down_samples_24h": down_samples_24h,
            "avg_icmp_latency_ms_24h": round(float(metric_row["avg_icmp_latency_ms_24h"]), 1)
            if metric_row and metric_row.get("avg_icmp_latency_ms_24h") is not None
            else None,
            "avg_tcp_latency_ms_24h": round(float(metric_row["avg_tcp_latency_ms_24h"]), 1)
            if metric_row and metric_row.get("avg_tcp_latency_ms_24h") is not None
            else None,
            "outage_count_diagnostic_window": int(metric_row["outage_count_diagnostic_window"] or 0) if metric_row else 0,
        },
        "samples": samples,
        "events": events,
        "outages": outages,
        "retention": {
            "raw_history_days": settings.NETWORK_SENTINEL_RAW_RETENTION_DAYS,
            "sample_history_days": settings.NETWORK_SENTINEL_SAMPLE_RETENTION_DAYS,
            "event_history_days": settings.NETWORK_SENTINEL_EVENT_RETENTION_DAYS,
            "outage_history_days": settings.NETWORK_SENTINEL_OUTAGE_RETENTION_DAYS,
        },
    }
