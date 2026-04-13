from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.email_templates import network_outage_alert_template, network_outage_recovered_template
from app.core.emailer import send_email_fire_and_forget
from app.core.logging import get_logger
from app.network_sentinel.checks import check_tcp, get_ping_runtime_details, parse_ping, ping_once
from app.network_sentinel.db_service import NetworkSentinelDB, NetworkService
from app.network_sentinel.history_logs import ServiceLogPaths, append_line, default_log_dir, prune_old_logs
from app.notifications.db_service import NotificationDBService

log = get_logger("network-sentinel-engine")
CRITICAL_OUTAGE_NOTIFICATION_DELAY = timedelta(seconds=75)
CRITICAL_OUTAGE_REMINDER_INTERVAL = timedelta(minutes=5)


@dataclass
class ServiceRuntimeState:
    consecutive_failures: int = 0
    last_overall_status: str = "UNKNOWN"
    last_state_change_at: datetime | None = None
    last_sampled_at: datetime | None = None
    down_since: datetime | None = None
    last_critical_notification_at: datetime | None = None
    has_observed: bool = False


def _derive_overall_status(service: NetworkService, icmp_up: bool | None, tcp_up: bool | None) -> tuple[str, str | None, str]:
    """
    Returns (overall_status, reason, outage_cause_guess)
    """
    if not service.check_icmp and not service.check_tcp:
        return "UNKNOWN", "No checks enabled", "CONFIG"

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

    if service.check_icmp and not service.check_tcp:
        if icmp_up is True:
            return "UP", None, "UNKNOWN"
        if icmp_up is False:
            return "DOWN", "ICMP failed", "NETWORK"
        return "UNKNOWN", "ICMP unknown", "UNKNOWN"

    if service.check_tcp and not service.check_icmp:
        if tcp_up is True:
            return "UP", None, "UNKNOWN"
        if tcp_up is False:
            return "DOWN", "TCP failed", "APPLICATION"
        return "UNKNOWN", "TCP unknown", "UNKNOWN"

    return "UNKNOWN", "Unhandled check combination", "UNKNOWN"


def _format_log_line_up(ts: str, *, bytes_val: int | None, icmp_latency: int | None, ttl: int | None, tcp_latency: int | None) -> str:
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

    - Reads enabled services from DB
    - Runs checks per service on interval
    - Maintains lightweight latest-status state
    - Persists sampled diagnostics + major events
    - Retains only a short raw log window on disk
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
        self._last_housekeeping_at: datetime | None = None
        self._last_housekeeping_summary: dict[str, int] | None = None
        self._last_error: str | None = None
        self._subscribers: set[asyncio.Queue] = set()

    async def stop(self) -> None:
        self._stop.set()
        for task in list(self._service_tasks.values()):
            task.cancel()
        await asyncio.gather(*self._service_tasks.values(), return_exceptions=True)
        self._service_tasks.clear()
        self._subscribers.clear()

    async def run_forever(self) -> None:
        self._log_paths.ensure()
        self._started_at = datetime.now(timezone.utc)
        await self._run_housekeeping(force=True)
        log.info("Network Sentinel engine starting")

        reconcile_interval = max(5, int(settings.NETWORK_SENTINEL_ENGINE_RECONCILE_SECONDS))
        while not self._stop.is_set():
            try:
                services = await NetworkSentinelDB.list_enabled_services()
                await self._reconcile_services(services)
                self._last_reconcile_at = datetime.now(timezone.utc)

                if self._should_run_housekeeping():
                    await self._run_housekeeping()
            except Exception as exc:
                self._last_error = f"reconcile_error: {type(exc).__name__}"
                log.error(f"Failed to reconcile network services: {exc}")

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=reconcile_interval)
            except asyncio.TimeoutError:
                pass

        log.info("Network Sentinel engine stopping")

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def get_health(self) -> dict[str, Any]:
        checker_runtime = get_ping_runtime_details()
        return {
            "online": not self._stop.is_set() and self._started_at is not None,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_reconcile_at": self._last_reconcile_at.isoformat() if self._last_reconcile_at else None,
            "last_housekeeping_at": self._last_housekeeping_at.isoformat() if self._last_housekeeping_at else None,
            "last_housekeeping_summary": self._last_housekeeping_summary,
            "last_error": self._last_error,
            "active_service_workers": len(self._service_tasks),
            "checker_runtime": checker_runtime,
            "retention": {
                "raw_history_days": settings.NETWORK_SENTINEL_RAW_RETENTION_DAYS,
                "sample_history_days": settings.NETWORK_SENTINEL_SAMPLE_RETENTION_DAYS,
                "event_history_days": settings.NETWORK_SENTINEL_EVENT_RETENTION_DAYS,
                "outage_history_days": settings.NETWORK_SENTINEL_OUTAGE_RETENTION_DAYS,
                "sample_interval_seconds": settings.NETWORK_SENTINEL_SAMPLE_INTERVAL_SECONDS,
            },
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
        for queue in self._subscribers:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                try:
                    _ = queue.get_nowait()
                    queue.put_nowait(message)
                except Exception:
                    stale.append(queue)
            except Exception:
                stale.append(queue)

        for queue in stale:
            self._subscribers.discard(queue)

    def _should_run_housekeeping(self) -> bool:
        if self._last_housekeeping_at is None:
            return True
        interval = max(300, int(settings.NETWORK_SENTINEL_HOUSEKEEPING_INTERVAL_SECONDS))
        elapsed = (datetime.now(timezone.utc) - self._last_housekeeping_at).total_seconds()
        return elapsed >= interval

    async def _run_housekeeping(self, *, force: bool = False) -> None:
        if not force and not self._should_run_housekeeping():
            return

        now = datetime.now(timezone.utc)
        raw_deleted = prune_old_logs(self._log_paths.base_dir, settings.NETWORK_SENTINEL_RAW_RETENTION_DAYS, now=now)
        db_summary = await NetworkSentinelDB.purge_old_history(
            sample_retention_days=settings.NETWORK_SENTINEL_SAMPLE_RETENTION_DAYS,
            event_retention_days=settings.NETWORK_SENTINEL_EVENT_RETENTION_DAYS,
            outage_retention_days=settings.NETWORK_SENTINEL_OUTAGE_RETENTION_DAYS,
        )

        summary = {"raw_logs_deleted": raw_deleted, **db_summary}
        self._last_housekeeping_at = now
        self._last_housekeeping_summary = summary

        if any(summary.values()):
            log.info(f"Network Sentinel housekeeping completed: {summary}")

    async def _reconcile_services(self, services: list[NetworkService]) -> None:
        wanted_ids = {str(service.id) for service in services}
        current_ids = set(self._service_tasks.keys())

        for service_id in current_ids - wanted_ids:
            task = self._service_tasks.pop(service_id, None)
            if task:
                task.cancel()
            self._service_configs.pop(service_id, None)
            self._state.pop(service_id, None)

        for service in services:
            sid = str(service.id)
            config_sig = (
                service.address,
                service.port,
                service.check_icmp,
                service.check_tcp,
                service.timeout_ms,
                service.interval_seconds,
            )
            if sid not in self._service_tasks:
                self._state[sid] = ServiceRuntimeState()
                self._service_configs[sid] = config_sig
                self._service_tasks[sid] = asyncio.create_task(self._monitor_loop(service), name=f"network-svc-{sid}")
                continue

            previous_sig = self._service_configs.get(sid)
            if previous_sig != config_sig:
                old_task = self._service_tasks.get(sid)
                if old_task:
                    old_task.cancel()
                self._service_configs[sid] = config_sig
                self._service_tasks[sid] = asyncio.create_task(self._monitor_loop(service), name=f"network-svc-{sid}")

    async def _record_state_event(
        self,
        *,
        service: NetworkService,
        checked_at: datetime,
        overall_status: str,
        previous_status: str,
        reason: str | None,
        outage_cause: str,
        icmp_latency_ms: int | None,
        tcp_latency_ms: int | None,
    ) -> None:
        if overall_status == "DOWN":
            await NetworkSentinelDB.record_event(
                category="OUTAGE",
                event_type="OUTAGE_STARTED",
                severity="CRITICAL",
                title=f"{service.name} is down",
                summary=reason or "The service became unreachable.",
                service_id=service.id,
                service_name=service.name,
                service_address=service.address,
                service_port=service.port,
                details={
                    "checked_at": checked_at.isoformat(),
                    "previous_status": previous_status,
                    "cause": outage_cause,
                    "reason": reason,
                    "icmp_latency_ms": icmp_latency_ms,
                    "tcp_latency_ms": tcp_latency_ms,
                },
            )
            return

        if overall_status == "DEGRADED":
            await NetworkSentinelDB.record_event(
                category="STATE",
                event_type="SERVICE_DEGRADED",
                severity="WARN",
                title=f"{service.name} is degraded",
                summary=reason or "The service is reachable but impaired.",
                service_id=service.id,
                service_name=service.name,
                service_address=service.address,
                service_port=service.port,
                details={
                    "checked_at": checked_at.isoformat(),
                    "previous_status": previous_status,
                    "reason": reason,
                    "icmp_latency_ms": icmp_latency_ms,
                    "tcp_latency_ms": tcp_latency_ms,
                },
            )
            return

        if overall_status == "UNKNOWN":
            await NetworkSentinelDB.record_event(
                category="STATE",
                event_type="SERVICE_UNKNOWN",
                severity="WARN",
                title=f"{service.name} status is unknown",
                summary=reason or "Monitoring could not determine a clear state.",
                service_id=service.id,
                service_name=service.name,
                service_address=service.address,
                service_port=service.port,
                details={
                    "checked_at": checked_at.isoformat(),
                    "previous_status": previous_status,
                    "reason": reason,
                },
            )
            return

        if overall_status == "UP" and previous_status == "DEGRADED":
            await NetworkSentinelDB.record_event(
                category="STATE",
                event_type="SERVICE_STABILIZED",
                severity="INFO",
                title=f"{service.name} is stable again",
                summary="The degraded state cleared and the service returned to normal.",
                service_id=service.id,
                service_name=service.name,
                service_address=service.address,
                service_port=service.port,
                details={
                    "checked_at": checked_at.isoformat(),
                    "previous_status": previous_status,
                },
            )

    async def _hydrate_existing_alert_state(self, service: NetworkService, state: ServiceRuntimeState) -> None:
        if state.last_overall_status != "DOWN":
            state.down_since = None
            state.last_critical_notification_at = None
            return

        open_outage = await NetworkSentinelDB.get_open_outage(service.id)
        state.down_since = (
            (open_outage or {}).get("started_at")
            or state.last_state_change_at
            or NetworkSentinelDB.utc_now()
        )
        state.last_critical_notification_at = await NetworkSentinelDB.get_latest_alert_event_at(
            service.id,
            outage_started_at=state.down_since,
        )

    async def _dispatch_outage_notification(
        self,
        *,
        service: NetworkService,
        checked_at: datetime,
        down_since: datetime,
        reason: str | None,
        outage_cause: str,
        reminder: bool,
    ) -> bool:
        recipients = await NetworkSentinelDB.list_current_shift_participant_contacts(checked_at)
        if not recipients:
            log.info(
                "No active shift participants available for Network Sentinel alert "
                f"{service.name} at {checked_at.isoformat()}"
            )
            return False

        outage_seconds = max(0, int((checked_at - down_since).total_seconds()))
        title = (
            f"CRITICAL: {service.name} still down"
            if reminder
            else f"CRITICAL: {service.name} is down"
        )
        message = (
            f"{service.name} at {service.address}"
            f"{f':{service.port}' if service.port is not None else ''} "
            f"has been DOWN for {outage_seconds}s and remains unavailable."
        )
        if reason:
            message = f"{message} Reason: {reason}."

        notification_results = await asyncio.gather(
            *[
                asyncio.to_thread(
                    NotificationDBService.create_notification,
                    title,
                    message,
                    user_id=recipient["id"],
                    related_entity="network_service",
                    related_id=service.id,
                    priority="high",
                )
                for recipient in recipients
            ],
            return_exceptions=True,
        )
        successful_notifications = sum(1 for result in notification_results if not isinstance(result, Exception))
        failed_notifications = len(notification_results) - successful_notifications
        if not successful_notifications:
            log.warning(
                f"Network Sentinel alert dispatch failed for {service.name}; "
                f"no notifications reached the current shift participants."
            )
            return False
        if failed_notifications:
            log.warning(
                f"Network Sentinel alert dispatch for {service.name} partially failed: "
                f"{failed_notifications} of {len(notification_results)} notifications were not created."
            )

        for recipient in recipients:
            email = (recipient.get("email") or "").strip()
            if not email:
                continue
            subject, text_body, html_body = network_outage_alert_template(
                recipient_name=recipient.get("recipient_name"),
                service_id=str(service.id),
                service_name=service.name,
                address=service.address,
                port=service.port,
                downtime_seconds=outage_seconds,
                reason=reason,
                reminder=reminder,
            )
            send_email_fire_and_forget([email], subject, text_body, html_body)

        await NetworkSentinelDB.record_event(
            category="ALERT",
            event_type="OUTAGE_REMINDER" if reminder else "OUTAGE_ALERTED",
            severity="CRITICAL",
            title=title,
            summary=(
                f"Sent {'reminder ' if reminder else ''}alert to {successful_notifications} "
                f"current-shift participant{'s' if successful_notifications != 1 else ''} after {outage_seconds}s of downtime."
            ),
            service_id=service.id,
            service_name=service.name,
            service_address=service.address,
            service_port=service.port,
            details={
                "checked_at": checked_at.isoformat(),
                "down_since": down_since.isoformat(),
                "outage_seconds": outage_seconds,
                "recipient_count": successful_notifications,
                "recipient_attempt_count": len(recipients),
                "cause": outage_cause,
                "reason": reason,
                "notification_scope": "current_shift_participants",
                "delivery_channels": ["in_app", "email"],
            },
        )
        return True

    async def _dispatch_recovery_notification(
        self,
        *,
        service: NetworkService,
        checked_at: datetime,
        duration_seconds: int,
        reason: str | None,
    ) -> None:
        recipients = await NetworkSentinelDB.list_current_shift_participant_contacts(checked_at)
        if not recipients:
            log.info(
                "No active shift participants available for Network Sentinel recovery "
                f"{service.name} at {checked_at.isoformat()}"
            )
            return

        title = f"RECOVERY: {service.name} is back up"
        message = (
            f"{service.name} at {service.address}"
            f"{f':{service.port}' if service.port is not None else ''} "
            f"recovered after {duration_seconds}s of downtime."
        )
        if reason:
            message = f"{message} Latest state: {reason}."

        await asyncio.gather(
            *[
                asyncio.to_thread(
                    NotificationDBService.create_notification,
                    title,
                    message,
                    user_id=recipient["id"],
                    related_entity="network_service",
                    related_id=service.id,
                    priority="medium",
                )
                for recipient in recipients
            ],
            return_exceptions=True,
        )

        for recipient in recipients:
            email = (recipient.get("email") or "").strip()
            if not email:
                continue
            subject, text_body, html_body = network_outage_recovered_template(
                recipient_name=recipient.get("recipient_name"),
                service_id=str(service.id),
                service_name=service.name,
                address=service.address,
                port=service.port,
                downtime_seconds=duration_seconds,
                reason=reason,
            )
            send_email_fire_and_forget([email], subject, text_body, html_body)

        log.info(
            "Dispatched Network Sentinel recovery notification for %s to %s current-shift participants",
            service.name,
            len(recipients),
        )

    async def _maybe_send_outage_notification(
        self,
        *,
        service: NetworkService,
        state: ServiceRuntimeState,
        checked_at: datetime,
        reason: str | None,
        outage_cause: str,
    ) -> None:
        if state.down_since is None:
            return

        if checked_at - state.down_since < CRITICAL_OUTAGE_NOTIFICATION_DELAY:
            return

        if state.last_critical_notification_at is not None:
            if checked_at - state.last_critical_notification_at < CRITICAL_OUTAGE_REMINDER_INTERVAL:
                return
            reminder = True
        else:
            reminder = False

        if await self._dispatch_outage_notification(
            service=service,
            checked_at=checked_at,
            down_since=state.down_since,
            reason=reason,
            outage_cause=outage_cause,
            reminder=reminder,
        ):
            state.last_critical_notification_at = checked_at

    async def _monitor_loop(self, service: NetworkService) -> None:
        sid = str(service.id)
        state = self._state[sid]

        try:
            existing = await NetworkSentinelDB.get_current_status(service.id)
            if existing:
                state.last_overall_status = existing.get("overall_status") or "UNKNOWN"
                state.consecutive_failures = int(existing.get("consecutive_failures") or 0)
                state.last_state_change_at = existing.get("last_state_change_at")
                state.has_observed = True
                await self._hydrate_existing_alert_state(service, state)
        except Exception:
            pass

        sample_interval = max(
            int(service.interval_seconds),
            int(settings.NETWORK_SENTINEL_SAMPLE_INTERVAL_SECONDS),
        )

        while not self._stop.is_set():
            checked_at = NetworkSentinelDB.utc_now()
            ts = checked_at.strftime("%Y-%m-%d %H:%M:%S")
            icmp = None
            tcp = None

            try:
                if service.check_icmp:
                    icmp_output = await asyncio.to_thread(ping_once, service.address, service.timeout_ms)
                    icmp = parse_ping(icmp_output)
                if service.check_tcp and service.port is not None:
                    tcp = await check_tcp(service.address, int(service.port), service.timeout_ms)

                icmp_up = icmp.up if icmp is not None else None
                tcp_up = tcp.up if tcp is not None else None
                overall_status, reason, outage_cause = _derive_overall_status(service, icmp_up, tcp_up)

                was_observed = state.has_observed
                previous_status = state.last_overall_status
                is_down = overall_status == "DOWN"
                state.consecutive_failures = state.consecutive_failures + 1 if is_down else 0

                state_changed_at = None
                if overall_status != state.last_overall_status:
                    state.last_overall_status = overall_status
                    state.last_state_change_at = checked_at
                    state_changed_at = checked_at
                    if overall_status == "DOWN":
                        state.down_since = checked_at
                        state.last_critical_notification_at = None
                    else:
                        state.down_since = None
                        state.last_critical_notification_at = None

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

                should_sample = (
                    state.last_sampled_at is None
                    or state_changed_at is not None
                    or (checked_at - state.last_sampled_at).total_seconds() >= sample_interval
                )
                if should_sample:
                    await NetworkSentinelDB.record_sample(
                        service_id=service.id,
                        sampled_at=checked_at,
                        overall_status=overall_status,
                        icmp_up=icmp_up,
                        icmp_bytes=icmp.bytes_val if icmp else None,
                        icmp_latency_ms=icmp.latency_ms if icmp else None,
                        icmp_ttl=icmp.ttl if icmp else None,
                        tcp_up=tcp_up,
                        tcp_latency_ms=tcp.latency_ms if tcp else None,
                        reason=reason,
                        consecutive_failures=state.consecutive_failures,
                    )
                    state.last_sampled_at = checked_at

                if state_changed_at is not None and (was_observed or overall_status in {"DOWN", "DEGRADED", "UNKNOWN"}):
                    await self._record_state_event(
                        service=service,
                        checked_at=checked_at,
                        overall_status=overall_status,
                        previous_status=previous_status,
                        reason=reason,
                        outage_cause=outage_cause,
                        icmp_latency_ms=icmp.latency_ms if icmp else None,
                        tcp_latency_ms=tcp.latency_ms if tcp else None,
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

                if overall_status == "DOWN":
                    outage_started = await NetworkSentinelDB.start_outage_if_needed(
                        service_id=service.id,
                        started_at=checked_at,
                        cause=outage_cause,
                        details={"reason": reason, "address": service.address, "port": service.port},
                    )
                    if outage_started and state_changed_at is not None:
                        append_line(self._log_paths.file_for(sid, checked_at), f"{ts} | OUTAGE DETECTED")
                    if state.down_since is None:
                        open_outage = await NetworkSentinelDB.get_open_outage(service.id)
                        state.down_since = (
                            (open_outage or {}).get("started_at")
                            or state.last_state_change_at
                            or checked_at
                        )
                    await self._maybe_send_outage_notification(
                        service=service,
                        state=state,
                        checked_at=checked_at,
                        reason=reason,
                        outage_cause=outage_cause,
                    )
                else:
                    closed = await NetworkSentinelDB.end_outage_if_open(
                        service_id=service.id,
                        ended_at=checked_at,
                        cause=outage_cause,
                        details={"reason": reason, "address": service.address, "port": service.port},
                    )
                    if closed is not None:
                        duration_seconds = int(closed.get("duration_seconds") or 0)
                        append_line(self._log_paths.file_for(sid, checked_at), f"{ts} | RECOVERED | Outage lasted {float(duration_seconds):.2f}s")
                        await NetworkSentinelDB.record_event(
                            category="OUTAGE",
                            event_type="OUTAGE_RESOLVED",
                            severity="INFO",
                            title=f"{service.name} recovered",
                            summary=f"Downtime lasted {duration_seconds}s.",
                            service_id=service.id,
                            service_name=service.name,
                            service_address=service.address,
                            service_port=service.port,
                            details={
                                "checked_at": checked_at.isoformat(),
                                "duration_seconds": duration_seconds,
                                "reason": reason,
                                "cause": closed.get("cause") or outage_cause,
                                "started_at": closed.get("started_at").isoformat() if closed.get("started_at") else None,
                                "ended_at": closed.get("ended_at").isoformat() if closed.get("ended_at") else checked_at.isoformat(),
                            },
                        )
                        if state.last_critical_notification_at is not None:
                            await self._dispatch_recovery_notification(
                                service=service,
                                checked_at=checked_at,
                                duration_seconds=duration_seconds,
                                reason=reason,
                            )
                    state.down_since = None
                    state.last_critical_notification_at = None

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

                append_line(self._log_paths.file_for(sid, checked_at), line)
                state.has_observed = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
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
                self._last_error = f"monitoring_error: {type(exc).__name__}"
                log.error(f"Monitoring error for {service.name} ({service.address}:{service.port}): {exc}")

            interval = max(1, int(service.interval_seconds))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
