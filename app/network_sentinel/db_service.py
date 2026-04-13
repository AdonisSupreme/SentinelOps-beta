from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from app.core.logging import get_logger
from app.db.database import get_async_connection

log = get_logger("network-sentinel-db")


def _jsonb_param(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value)


@dataclass(frozen=True)
class NetworkService:
    id: UUID
    name: str
    address: str
    port: int | None
    enabled: bool
    check_icmp: bool
    check_tcp: bool
    timeout_ms: int
    interval_seconds: int


class NetworkSentinelDB:
    @staticmethod
    async def list_current_shift_participant_contacts(
        reference_time: datetime | None = None,
    ) -> list[dict[str, Any]]:
        now_utc = reference_time or datetime.now(timezone.utc)
        async with get_async_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT
                    cp.user_id AS id,
                    u.email,
                    COALESCE(
                        NULLIF(BTRIM(CONCAT_WS(' ', u.first_name, u.last_name)), ''),
                        NULLIF(BTRIM(u.username), ''),
                        NULLIF(BTRIM(u.email), ''),
                        'SentinelOps operator'
                    ) AS recipient_name
                FROM checklist_participants cp
                JOIN checklist_instances ci ON ci.id = cp.instance_id
                JOIN users u ON u.id = cp.user_id
                WHERE ci.shift_start <= $1
                  AND ci.shift_end > $1
                  AND COALESCE(ci.status::text, 'OPEN') IN (
                      'OPEN',
                      'IN_PROGRESS',
                      'PENDING_REVIEW'
                  )
                """,
                now_utc,
            )
            return [
                {
                    "id": row["id"],
                    "email": row["email"],
                    "recipient_name": row["recipient_name"],
                }
                for row in rows
            ]

    @staticmethod
    async def list_enabled_services() -> list[NetworkService]:
        async with get_async_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT id, name, address, port, enabled, check_icmp, check_tcp, timeout_ms, interval_seconds
                FROM network_services
                WHERE enabled = true AND deleted_at IS NULL
                ORDER BY created_at ASC
                """
            )
            services: list[NetworkService] = []
            for r in rows:
                services.append(
                    NetworkService(
                        id=r["id"],
                        name=r["name"],
                        address=r["address"],
                        port=r["port"],
                        enabled=r["enabled"],
                        check_icmp=r["check_icmp"],
                        check_tcp=r["check_tcp"],
                        timeout_ms=r["timeout_ms"],
                        interval_seconds=r["interval_seconds"],
                    )
                )
            return services

    @staticmethod
    async def upsert_status(
        *,
        service_id: UUID,
        checked_at: datetime,
        icmp_up: bool | None,
        icmp_bytes: int | None,
        icmp_latency_ms: int | None,
        icmp_ttl: int | None,
        tcp_up: bool | None,
        tcp_latency_ms: int | None,
        overall_status: str,
        reason: str | None,
        consecutive_failures: int,
        state_changed_at: datetime | None,
    ) -> None:
        async with get_async_connection() as conn:
            await conn.execute(
                """
                INSERT INTO network_service_status (
                    service_id,
                    last_checked_at,
                    icmp_up, icmp_bytes, icmp_latency_ms, icmp_ttl,
                    tcp_up, tcp_latency_ms,
                    overall_status, reason,
                    consecutive_failures,
                    last_state_change_at,
                    updated_at
                ) VALUES (
                    $1,
                    $2,
                    $3, $4, $5, $6,
                    $7, $8,
                    $9, $10,
                    $11,
                    $12,
                    now()
                )
                ON CONFLICT (service_id) DO UPDATE SET
                    last_checked_at = EXCLUDED.last_checked_at,
                    icmp_up = EXCLUDED.icmp_up,
                    icmp_bytes = EXCLUDED.icmp_bytes,
                    icmp_latency_ms = EXCLUDED.icmp_latency_ms,
                    icmp_ttl = EXCLUDED.icmp_ttl,
                    tcp_up = EXCLUDED.tcp_up,
                    tcp_latency_ms = EXCLUDED.tcp_latency_ms,
                    overall_status = EXCLUDED.overall_status,
                    reason = EXCLUDED.reason,
                    consecutive_failures = EXCLUDED.consecutive_failures,
                    last_state_change_at = COALESCE(EXCLUDED.last_state_change_at, network_service_status.last_state_change_at),
                    updated_at = now()
                """,
                service_id,
                checked_at,
                icmp_up,
                icmp_bytes,
                icmp_latency_ms,
                icmp_ttl,
                tcp_up,
                tcp_latency_ms,
                overall_status,
                reason,
                consecutive_failures,
                state_changed_at,
            )

    @staticmethod
    async def start_outage_if_needed(
        *,
        service_id: UUID,
        started_at: datetime,
        cause: str = "UNKNOWN",
        details: dict[str, Any] | None = None,
    ) -> bool:
        async with get_async_connection() as conn:
            # Partial unique indexes cannot be used with ON CONFLICT directly.
            # We enforce "single open outage" via a guard insert.
            row = await conn.fetchrow(
                """
                INSERT INTO network_service_outages (service_id, started_at, cause, details)
                SELECT $1, $2, $3::network_outage_cause, $4::jsonb
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM network_service_outages
                    WHERE service_id = $1 AND ended_at IS NULL
                )
                RETURNING id
                """,
                service_id,
                started_at,
                cause,
                _jsonb_param(details),
            )
            return bool(row)

    @staticmethod
    async def end_outage_if_open(
        *,
        service_id: UUID,
        ended_at: datetime,
        cause: str = "UNKNOWN",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """
        End the active outage if one exists and return the closed outage details.
        """
        async with get_async_connection() as conn:
            row = await conn.fetchrow(
                """
                UPDATE network_service_outages
                SET
                    ended_at = $2,
                    duration_seconds = GREATEST(0, EXTRACT(EPOCH FROM ($2 - started_at))::int),
                    cause = $3::network_outage_cause,
                    details = COALESCE($4::jsonb, details)
                WHERE service_id = $1 AND ended_at IS NULL
                RETURNING id, started_at, ended_at, duration_seconds, cause::text AS cause, details
                """,
                service_id,
                ended_at,
                cause,
                _jsonb_param(details),
            )
            if not row:
                return None
            payload = dict(row)
            if payload.get("details") and not isinstance(payload["details"], dict):
                try:
                    payload["details"] = json.loads(payload["details"])
                except Exception:
                    pass
            return payload

    @staticmethod
    async def get_current_status(service_id: UUID) -> dict | None:
        async with get_async_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT overall_status, consecutive_failures, last_state_change_at
                FROM network_service_status
                WHERE service_id = $1
                """,
                service_id,
            )
            if not row:
                return None
            return dict(row)

    @staticmethod
    async def get_open_outage(service_id: UUID) -> dict[str, Any] | None:
        async with get_async_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, started_at, cause::text AS cause, details
                FROM network_service_outages
                WHERE service_id = $1
                  AND ended_at IS NULL
                ORDER BY started_at DESC
                LIMIT 1
                """,
                service_id,
            )
            if not row:
                return None
            payload = dict(row)
            if payload.get("details") and not isinstance(payload["details"], dict):
                try:
                    payload["details"] = json.loads(payload["details"])
                except Exception:
                    pass
            return payload

    @staticmethod
    async def get_latest_alert_event_at(
        service_id: UUID,
        outage_started_at: datetime | None = None,
    ) -> datetime | None:
        async with get_async_connection() as conn:
            return await conn.fetchval(
                """
                SELECT MAX(created_at)
                FROM network_service_events
                WHERE service_id = $1
                  AND event_type = ANY($2::text[])
                  AND ($3::timestamptz IS NULL OR created_at >= $3)
                """,
                service_id,
                ["OUTAGE_ALERTED", "OUTAGE_REMINDER"],
                outage_started_at,
            )

    @staticmethod
    async def list_current_shift_participant_user_ids(
        reference_time: datetime | None = None,
    ) -> list[UUID]:
        contacts = await NetworkSentinelDB.list_current_shift_participant_contacts(reference_time)
        return [row["id"] for row in contacts]

    @staticmethod
    async def record_sample(
        *,
        service_id: UUID,
        sampled_at: datetime,
        overall_status: str,
        icmp_up: bool | None,
        icmp_bytes: int | None,
        icmp_latency_ms: int | None,
        icmp_ttl: int | None,
        tcp_up: bool | None,
        tcp_latency_ms: int | None,
        reason: str | None,
        consecutive_failures: int,
    ) -> None:
        async with get_async_connection() as conn:
            await conn.execute(
                """
                INSERT INTO network_service_samples (
                    service_id,
                    sampled_at,
                    overall_status,
                    icmp_up,
                    icmp_bytes,
                    icmp_latency_ms,
                    icmp_ttl,
                    tcp_up,
                    tcp_latency_ms,
                    reason,
                    consecutive_failures
                ) VALUES (
                    $1, $2, $3::network_overall_status, $4, $5, $6, $7, $8, $9, $10, $11
                )
                """,
                service_id,
                sampled_at,
                overall_status,
                icmp_up,
                icmp_bytes,
                icmp_latency_ms,
                icmp_ttl,
                tcp_up,
                tcp_latency_ms,
                reason,
                consecutive_failures,
            )

    @staticmethod
    async def record_event(
        *,
        category: str,
        event_type: str,
        severity: str,
        title: str,
        summary: str | None = None,
        service_id: UUID | None = None,
        service_name: str | None = None,
        service_address: str | None = None,
        service_port: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        async with get_async_connection() as conn:
            await conn.execute(
                """
                INSERT INTO network_service_events (
                    service_id,
                    service_name,
                    service_address,
                    service_port,
                    category,
                    event_type,
                    severity,
                    title,
                    summary,
                    details
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb
                )
                """,
                service_id,
                service_name,
                service_address,
                service_port,
                category,
                event_type,
                severity,
                title,
                summary,
                _jsonb_param(details),
            )

    @staticmethod
    async def purge_old_history(
        *,
        sample_retention_days: int,
        event_retention_days: int,
        outage_retention_days: int,
    ) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        sample_cutoff = now - timedelta(days=max(1, sample_retention_days))
        event_cutoff = now - timedelta(days=max(1, event_retention_days))
        outage_cutoff = now - timedelta(days=max(1, outage_retention_days))

        async with get_async_connection() as conn:
            sample_deleted = await conn.fetchval(
                """
                WITH purged AS (
                    DELETE FROM network_service_samples
                    WHERE sampled_at < $1
                    RETURNING 1
                )
                SELECT COUNT(*) FROM purged
                """,
                sample_cutoff,
            )
            event_deleted = await conn.fetchval(
                """
                WITH purged AS (
                    DELETE FROM network_service_events
                    WHERE created_at < $1
                    RETURNING 1
                )
                SELECT COUNT(*) FROM purged
                """,
                event_cutoff,
            )
            outage_deleted = await conn.fetchval(
                """
                WITH purged AS (
                    DELETE FROM network_service_outages
                    WHERE ended_at IS NOT NULL
                      AND COALESCE(ended_at, started_at) < $1
                    RETURNING 1
                )
                SELECT COUNT(*) FROM purged
                """,
                outage_cutoff,
            )

        return {
            "samples_deleted": int(sample_deleted or 0),
            "events_deleted": int(event_deleted or 0),
            "outages_deleted": int(outage_deleted or 0),
        }

    @staticmethod
    def utc_now() -> datetime:
        return datetime.now(timezone.utc)

