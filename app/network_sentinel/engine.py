from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.network_sentinel.checks import ping_once, parse_ping, check_tcp
from app.network_sentinel.db_service import NetworkSentinelDB, NetworkService
from app.network_sentinel.history_logs import ServiceLogPaths, default_log_dir, append_line

log = get_logger("network-sentinel-engine")


@dataclass
class ServiceRuntimeState:
    consecutive_failures: int = 0
    last_overall_status: str = "UNKNOWN"
    last_state_change_at: datetime | None = None


def _derive_overall_status(service: NetworkService, icmp_up: bool | None, tcp_up: bool | None) -> tuple[str, str | None, str]:
    """
    Returns (overall_status, reason, outage_cause_guess)
    """
    if not service.check_icmp and not service.check_tcp:
        return "UNKNOWN", "No checks enabled", "CONFIG"

    # If both checks enabled
    if service.check_icmp and service.check_tcp:
        if icmp_up is True and tcp_up is True:
            return "UP", None, "UNKNOWN"
        if icmp_up is False and tcp_up is True:
            return "DEGRADED", "ICMP failed but TCP succeeded", "ICMP_BLOCKED"
        if icmp_up is True and tcp_up is False:
            return "DEGRADED", "TCP failed but ICMP succeeded", "APPLICATION"
        if icmp_up is False and tcp_up is False:
            return "DOWN", "ICMP and TCP failed", "NETWORK"
        return "UNKNOWN", "Insufficient data", "UNKNOWN"

    # Only ICMP
    if service.check_icmp and not service.check_tcp:
        if icmp_up is True:
            return "UP", None, "UNKNOWN"
        if icmp_up is False:
            return "DOWN", "ICMP failed", "NETWORK"
        return "UNKNOWN", "ICMP unknown", "UNKNOWN"

    # Only TCP
    if service.check_tcp and not service.check_icmp:
        if tcp_up is True:
            return "UP", None, "UNKNOWN"
        if tcp_up is False:
            return "DOWN", "TCP failed", "APPLICATION"
        return "UNKNOWN", "TCP unknown", "UNKNOWN"

    return "UNKNOWN", "Unhandled check combination", "UNKNOWN"


def _format_log_line_up(ts: str, *, bytes_val: int | None, icmp_latency: int | None, ttl: int | None, tcp_latency: int | None) -> str:
    # Exact format aligned with your script.
    return (
        f"{ts} | UP | "
        f"bytes={bytes_val} | "
        f"icmp_latency={icmp_latency}ms | "
        f"TTL={ttl} | "
        f"tcp_latency={tcp_latency}ms"
    )


class NetworkSentinelEngine:
    """
    Multi-target monitoring engine.

    - Reads enabled services from DB (periodically)
    - Runs checks per service on interval
    - Writes continuous history logs (per-service daily file) with same line formats
    - Updates DB snapshot + opens/closes outage windows
    """

    def __init__(self, project_root: Path):
        self._project_root = project_root
        self._log_paths = ServiceLogPaths(base_dir=default_log_dir(project_root))
        self._stop = asyncio.Event()
        self._service_tasks: dict[str, asyncio.Task] = {}
        self._service_configs: dict[str, tuple[str, int | None, bool, bool, int, int]] = {}
        self._state: dict[str, ServiceRuntimeState] = {}
        self._started_at: datetime | None = None
        self._last_reconcile_at: datetime | None = None
        self._last_error: str | None = None
        self._subscribers: set[asyncio.Queue] = set()

    async def stop(self) -> None:
        self._stop.set()
        for t in list(self._service_tasks.values()):
            t.cancel()
        await asyncio.gather(*self._service_tasks.values(), return_exceptions=True)
        self._service_tasks.clear()
        self._subscribers.clear()

    async def run_forever(self) -> None:
        self._log_paths.ensure()
        self._started_at = datetime.now(timezone.utc)
        log.info("🌐 Network Sentinel engine starting")

        # Bootstrap from DB and then reconcile periodically.
        while not self._stop.is_set():
            try:
                services = await NetworkSentinelDB.list_enabled_services()
                await self._reconcile_services(services)
                self._last_reconcile_at = datetime.now(timezone.utc)
            except Exception as exc:
                self._last_error = f"reconcile_error: {type(exc).__name__}"
                log.error(f"Failed to reconcile network services: {exc}")

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=15)
            except asyncio.TimeoutError:
                pass

        log.info("🛑 Network Sentinel engine stopping")

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def get_health(self) -> dict[str, Any]:
        return {
            "online": not self._stop.is_set() and self._started_at is not None,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_reconcile_at": self._last_reconcile_at.isoformat() if self._last_reconcile_at else None,
            "last_error": self._last_error,
            "active_service_workers": len(self._service_tasks),
        }

    def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        if not self._subscribers:
            return
        message = {
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": payload,
        }
        stale: list[asyncio.Queue] = []
        for q in self._subscribers:
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                # Drop oldest message to keep stream live, then retry once.
                try:
                    _ = q.get_nowait()
                    q.put_nowait(message)
                except Exception:
                    stale.append(q)
            except Exception:
                stale.append(q)
        for q in stale:
            self._subscribers.discard(q)

    async def _reconcile_services(self, services: list[NetworkService]) -> None:
        wanted_ids = {str(s.id) for s in services}
        current_ids = set(self._service_tasks.keys())

        # Stop tasks for removed/disabled services
        for service_id in current_ids - wanted_ids:
            task = self._service_tasks.pop(service_id, None)
            if task:
                task.cancel()
            self._service_configs.pop(service_id, None)
            self._state.pop(service_id, None)

        # Start tasks for new services, or restart tasks when config changes.
        for s in services:
            sid = str(s.id)
            config_sig = (s.address, s.port, s.check_icmp, s.check_tcp, s.timeout_ms, s.interval_seconds)
            if sid not in self._service_tasks:
                self._state[sid] = ServiceRuntimeState()
                self._service_configs[sid] = config_sig
                self._service_tasks[sid] = asyncio.create_task(self._monitor_loop(s), name=f"network-svc-{sid}")
                continue

            prev_sig = self._service_configs.get(sid)
            if prev_sig != config_sig:
                old_task = self._service_tasks.get(sid)
                if old_task:
                    old_task.cancel()
                self._service_configs[sid] = config_sig
                self._service_tasks[sid] = asyncio.create_task(self._monitor_loop(s), name=f"network-svc-{sid}")

    async def _monitor_loop(self, service: NetworkService) -> None:
        sid = str(service.id)
        state = self._state[sid]

        # Warm-load last known DB status (so restarts don't create fake "recovery" events)
        try:
            existing = await NetworkSentinelDB.get_current_status(service.id)
            if existing:
                state.last_overall_status = existing.get("overall_status") or "UNKNOWN"
                state.consecutive_failures = int(existing.get("consecutive_failures") or 0)
                state.last_state_change_at = existing.get("last_state_change_at")
        except Exception:
            pass

        while not self._stop.is_set():
            checked_at = NetworkSentinelDB.utc_now()
            ts = checked_at.strftime("%Y-%m-%d %H:%M:%S")

            icmp = None
            tcp = None

            try:
                if service.check_icmp:
                    # Avoid blocking the asyncio event loop with subprocess.run.
                    icmp_out = await asyncio.to_thread(ping_once, service.address, service.timeout_ms)
                    icmp = parse_ping(icmp_out)
                if service.check_tcp and service.port is not None:
                    tcp = await check_tcp(service.address, int(service.port), service.timeout_ms)

                icmp_up = icmp.up if icmp is not None else None
                tcp_up = tcp.up if tcp is not None else None

                overall_status, reason, outage_cause = _derive_overall_status(service, icmp_up, tcp_up)

                is_down = overall_status == "DOWN"
                if is_down:
                    state.consecutive_failures += 1
                else:
                    state.consecutive_failures = 0

                state_changed_at = None
                if overall_status != state.last_overall_status:
                    state.last_overall_status = overall_status
                    state.last_state_change_at = checked_at
                    state_changed_at = checked_at

                # --- DB snapshot ---
                await NetworkSentinelDB.upsert_status(
                    service_id=service.id,
                    checked_at=checked_at,
                    icmp_up=icmp_up,
                    icmp_bytes=icmp.bytes_val if icmp else None,
                    icmp_latency_ms=icmp.latency_ms if icmp else None,
                    icmp_ttl=icmp.ttl if icmp else None,
                    tcp_up=tcp_up,
                    tcp_latency_ms=tcp.latency_ms if tcp else None,
                    overall_status=overall_status,
                    reason=reason,
                    consecutive_failures=state.consecutive_failures,
                    state_changed_at=state_changed_at,
                )
                self._publish(
                    "SERVICE_STATUS_UPDATED",
                    {
                        "service_id": sid,
                        "name": service.name,
                        "address": service.address,
                        "port": service.port,
                        "last_checked_at": checked_at.isoformat(),
                        "icmp_up": icmp_up,
                        "icmp_bytes": icmp.bytes_val if icmp else None,
                        "icmp_latency_ms": icmp.latency_ms if icmp else None,
                        "icmp_ttl": icmp.ttl if icmp else None,
                        "tcp_up": tcp_up,
                        "tcp_latency_ms": tcp.latency_ms if tcp else None,
                        "overall_status": overall_status,
                        "reason": reason,
                        "consecutive_failures": state.consecutive_failures,
                        "last_state_change_at": state.last_state_change_at.isoformat() if state.last_state_change_at else None,
                    },
                )

                # --- Outage windows (only for true DOWN) ---
                if overall_status == "DOWN":
                    await NetworkSentinelDB.start_outage_if_needed(
                        service_id=service.id,
                        started_at=checked_at,
                        cause=outage_cause,
                        details={"reason": reason, "address": service.address, "port": service.port},
                    )
                else:
                    closed = await NetworkSentinelDB.end_outage_if_open(
                        service_id=service.id,
                        ended_at=checked_at,
                        cause=outage_cause,
                        details={"reason": reason, "address": service.address, "port": service.port},
                    )
                    # closed is duration_seconds if an outage ended
                    if closed is not None:
                        # Mirror legacy "RECOVERED" line format (per-service file)
                        recovery_msg = f"{ts} | RECOVERED | Outage lasted {float(closed):.2f}s"
                        append_line(self._log_paths.file_for(sid, checked_at), recovery_msg)

                # --- Continuous file history (same format as legacy script) ---
                if overall_status == "UP":
                    line = _format_log_line_up(
                        ts,
                        bytes_val=icmp.bytes_val if icmp else None,
                        icmp_latency=icmp.latency_ms if icmp else None,
                        ttl=icmp.ttl if icmp else None,
                        tcp_latency=tcp.latency_ms if tcp else None,
                    )
                else:
                    line = f"{ts} | {overall_status}"

                    # Legacy "OUTAGE DETECTED" line (only when transitioning into DOWN)
                    if overall_status == "DOWN" and state_changed_at is not None:
                        append_line(self._log_paths.file_for(sid, checked_at), f"{ts} | OUTAGE DETECTED")

                append_line(self._log_paths.file_for(sid, checked_at), line)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Do not crash the task; mark as unknown and keep moving.
                try:
                    await NetworkSentinelDB.upsert_status(
                        service_id=service.id,
                        checked_at=checked_at,
                        icmp_up=None,
                        icmp_bytes=None,
                        icmp_latency_ms=None,
                        icmp_ttl=None,
                        tcp_up=None,
                        tcp_latency_ms=None,
                        overall_status="UNKNOWN",
                        reason=f"monitoring_error: {type(exc).__name__}",
                        consecutive_failures=state.consecutive_failures + 1,
                        state_changed_at=None,
                    )
                except Exception:
                    pass
                log.error(f"Monitoring error for {service.name} ({service.address}:{service.port}): {exc}")
                self._last_error = f"monitoring_error: {type(exc).__name__}"

            # Sleep until next check or stop signal
            interval = max(1, int(service.interval_seconds))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

