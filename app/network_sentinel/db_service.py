from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
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
    ) -> None:
        async with get_async_connection() as conn:
            # Partial unique indexes cannot be used with ON CONFLICT directly.
            # We enforce "single open outage" via a guard insert.
            await conn.execute(
                """
                INSERT INTO network_service_outages (service_id, started_at, cause, details)
                SELECT $1, $2, $3::network_outage_cause, $4::jsonb
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM network_service_outages
                    WHERE service_id = $1 AND ended_at IS NULL
                )
                """,
                service_id,
                started_at,
                cause,
                _jsonb_param(details),
            )

    @staticmethod
    async def end_outage_if_open(
        *,
        service_id: UUID,
        ended_at: datetime,
        cause: str = "UNKNOWN",
        details: dict[str, Any] | None = None,
    ) -> int | None:
        """
        End the active outage if one exists.
        Returns duration_seconds if an outage was closed, else None.
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
                RETURNING duration_seconds
                """,
                service_id,
                ended_at,
                cause,
                _jsonb_param(details),
            )
            if not row:
                return None
            return int(row["duration_seconds"]) if row["duration_seconds"] is not None else None

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
    def utc_now() -> datetime:
        return datetime.now(timezone.utc)

