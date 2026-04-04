from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional, Tuple
import os
from uuid import UUID

from app.core.config import settings
from app.core.logging import get_logger
from app.core.emailer import send_email_fire_and_forget
from app.db.database import get_async_connection
from app.checklists.db_service import ChecklistDBService
from app.notifications.db_service import NotificationDBService

log = get_logger("checklist-automation-service")


class ChecklistAutomationService:
    SHIFT_ORDER = ("MORNING", "AFTERNOON", "NIGHT")
    REMINDER_METADATA_KEY = "timed_reminders_sent"
    MISSED_REMINDER_METADATA_KEY = "timed_reminders_missed"
    ACTIVE_REMINDER_INSTANCE_STATUSES = ("OPEN", "IN_PROGRESS")
    ACTIONED_ITEM_STATUSES = ("COMPLETED", "SKIPPED", "FAILED")

    @staticmethod
    async def initialize_daily_shift_instances() -> Dict[str, Any]:
        """
        Daily system bootstrap for checklist instances.
        - Runs at 06:00 business timezone
        - Initializes all active shifts
        - Notifies participants in-app + email
        """
        tz = ZoneInfo(settings.TRUSTLINK_SCHEDULE_TIMEZONE)
        today = datetime.now(tz).date()

        actor_id, actor_username = await ChecklistAutomationService._resolve_system_actor()
        if not actor_id:
            raise RuntimeError("No active user available for system-triggered checklist creation")

        summary: List[Dict[str, Any]] = []
        for shift in ChecklistAutomationService.SHIFT_ORDER:
            try:
                # Skip shifts without an active template to avoid noisy failures.
                if not ChecklistDBService.get_active_template_for_shift(shift):
                    summary.append({"shift": shift, "status": "skipped", "reason": "no_active_template"})
                    continue

                result = ChecklistDBService.create_checklist_instance(
                    checklist_date=today,
                    shift=shift,
                    created_by=actor_id,
                    created_by_username=actor_username,
                    template_id=None,
                    section_id=None,
                )

                instance_data = (result or {}).get("instance") or {}
                instance_id = str((result or {}).get("id") or instance_data.get("id") or "")
                participants = instance_data.get("participants") or []
                created_new = (result or {}).get("message") == "New instance created"

                notified_count, emailed_count = ChecklistAutomationService._notify_shift_participants(
                    instance_id=instance_id,
                    checklist_date=str(today),
                    shift=shift,
                    participants=participants,
                    created_new=created_new,
                )

                summary.append(
                    {
                        "shift": shift,
                        "status": "created" if created_new else "existing",
                        "instance_id": instance_id,
                        "participants": len(participants),
                        "notified": notified_count,
                        "emailed": emailed_count,
                    }
                )
            except Exception as exc:
                log.error(f"Failed to initialize checklist for shift {shift} on {today}: {exc}")
                summary.append({"shift": shift, "status": "failed", "error": str(exc)})

        log.info(f"Daily checklist initialization complete for {today}: {summary}")
        return {"date": str(today), "runs": summary}

    @staticmethod
    async def process_due_timed_reminders(now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Sweep active checklist instances and send reminders for due timed items/subitems.
        Reminder windows are interpreted as "notify before the scheduled time arrives".
        """
        business_tz = ZoneInfo(settings.TRUSTLINK_SCHEDULE_TIMEZONE)
        now_local = (now or datetime.now(timezone.utc)).astimezone(business_tz)

        async with get_async_connection() as conn:
            instance_rows = await conn.fetch(
                """
                SELECT id
                FROM checklist_instances
                WHERE status::text = ANY($1::text[])
                ORDER BY checklist_date ASC, shift_start ASC
                """,
                list(ChecklistAutomationService.ACTIVE_REMINDER_INSTANCE_STATUSES),
            )

            results: List[Dict[str, Any]] = []
            for row in instance_rows:
                try:
                    result = await ChecklistAutomationService._process_instance_due_timed_reminders(
                        conn=conn,
                        instance_id=row["id"],
                        now_local=now_local,
                        business_tz=business_tz,
                    )
                    if result:
                        results.append(result)
                except Exception as exc:
                    log.error("Timed reminder sweep failed for instance %s: %s", row["id"], exc)
                    results.append(
                        {
                            "instance_id": str(row["id"]),
                            "status": "failed",
                            "error": str(exc),
                        }
                    )

        sent_count = sum(result.get("sent_reminders", 0) for result in results)
        notification_count = sum(result.get("notifications_created", 0) for result in results)
        email_count = sum(result.get("emails_targeted", 0) for result in results)
        missed_count = sum(result.get("missed_reminders", 0) for result in results)

        if sent_count:
            log.info(
                "Checklist timed reminders processed at %s: reminders=%s notifications=%s emails=%s",
                now_local.isoformat(),
                sent_count,
                notification_count,
                email_count,
            )
        if missed_count:
            log.warning(
                "Checklist timed reminders missed at %s: missed=%s",
                now_local.isoformat(),
                missed_count,
            )

        return {
            "checked_at": now_local.isoformat(),
            "instances": results,
            "sent_reminders": sent_count,
            "notifications_created": notification_count,
            "emails_targeted": email_count,
            "missed_reminders": missed_count,
        }

    @staticmethod
    async def _resolve_system_actor() -> Tuple[Optional[UUID], str]:
        async with get_async_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT u.id, u.username
                FROM users u
                LEFT JOIN user_roles ur ON ur.user_id = u.id
                LEFT JOIN roles r ON r.id = ur.role_id
                WHERE u.is_active = TRUE
                ORDER BY
                    CASE
                        WHEN LOWER(COALESCE(r.name, '')) = 'admin' THEN 0
                        WHEN LOWER(COALESCE(r.name, '')) = 'manager' THEN 1
                        ELSE 2
                    END,
                    u.created_at ASC
                LIMIT 1
                """
            )

        if not row:
            return None, "sentinel-system"

        return row["id"], row["username"] or "sentinel-system"

    @staticmethod
    async def _process_instance_due_timed_reminders(
        *,
        conn,
        instance_id,
        now_local: datetime,
        business_tz: ZoneInfo,
    ) -> Optional[Dict[str, Any]]:
        queued_reminders: List[Dict[str, Any]] = []
        participant_payloads: List[Dict[str, Any]] = []
        instance_payload: Optional[Dict[str, Any]] = None

        async with conn.transaction():
            instance = await conn.fetchrow(
                """
                SELECT id, checklist_date, shift, shift_start, shift_end, status, metadata
                FROM checklist_instances
                WHERE id = $1
                  AND status::text = ANY($2::text[])
                FOR UPDATE
                """,
                instance_id,
                list(ChecklistAutomationService.ACTIVE_REMINDER_INSTANCE_STATUSES),
            )
            if not instance:
                return None

            participants = await conn.fetch(
                """
                SELECT DISTINCT u.id, u.username, u.email, u.first_name, u.last_name
                FROM checklist_participants cp
                JOIN users u ON u.id = cp.user_id
                WHERE cp.instance_id = $1
                  AND u.is_active = TRUE
                ORDER BY u.username ASC
                """,
                instance_id,
            )
            if not participants:
                return {
                    "instance_id": str(instance_id),
                    "status": "skipped",
                    "reason": "no_participants",
                    "sent_reminders": 0,
                    "notifications_created": 0,
                    "emails_targeted": 0,
                }

            metadata = ChecklistAutomationService._coerce_instance_metadata(instance["metadata"])
            reminder_log = ChecklistAutomationService._coerce_reminder_log(
                metadata.get(ChecklistAutomationService.REMINDER_METADATA_KEY)
            )
            missed_log = ChecklistAutomationService._coerce_reminder_log(
                metadata.get(ChecklistAutomationService.MISSED_REMINDER_METADATA_KEY)
            )

            due_reminders, missed_reminders = await ChecklistAutomationService._collect_due_instance_reminders(
                conn=conn,
                instance=instance,
                now_local=now_local,
                business_tz=business_tz,
                reminder_log=reminder_log,
                missed_log=missed_log,
            )

            if not due_reminders and not missed_reminders:
                return {
                    "instance_id": str(instance_id),
                    "status": "idle",
                    "sent_reminders": 0,
                    "notifications_created": 0,
                    "emails_targeted": 0,
                    "missed_reminders": 0,
                }

            for reminder in due_reminders:
                reminder_log[reminder["dedupe_key"]] = {
                    "sent_at": now_local.isoformat(),
                    "scheduled_for": reminder["scheduled_at"].isoformat(),
                    "kind": reminder["kind"],
                    "entity_id": reminder["entity_id"],
                    "notify_before_minutes": reminder["notify_before_minutes"],
                }

            for reminder in missed_reminders:
                missed_log[reminder["dedupe_key"]] = {
                    "logged_at": now_local.isoformat(),
                    "scheduled_for": reminder["scheduled_at"].isoformat(),
                    "kind": reminder["kind"],
                    "entity_id": reminder["entity_id"],
                    "notify_before_minutes": reminder["notify_before_minutes"],
                }

            metadata[ChecklistAutomationService.REMINDER_METADATA_KEY] = reminder_log
            metadata[ChecklistAutomationService.MISSED_REMINDER_METADATA_KEY] = missed_log
            await conn.execute(
                """
                UPDATE checklist_instances
                SET metadata = $2::jsonb
                WHERE id = $1
                """,
                instance["id"],
                json.dumps(metadata),
            )

            instance_payload = {
                "instance_id": str(instance["id"]),
                "checklist_date": str(instance["checklist_date"]),
                "shift": instance["shift"],
            }
            participant_payloads = [
                {
                    "id": str(participant["id"]),
                    "email": participant["email"],
                    "username": participant["username"],
                    "first_name": participant["first_name"],
                    "last_name": participant["last_name"],
                }
                for participant in participants
            ]
            queued_reminders = due_reminders

        if not instance_payload or not queued_reminders:
            if missed_reminders:
                for reminder in missed_reminders:
                    log.warning(
                        "Missed checklist reminder window for instance %s (%s %s): %s at %s",
                        instance_payload["instance_id"] if instance_payload else str(instance_id),
                        reminder["kind"],
                        reminder["entity_id"],
                        reminder["item_title"],
                        reminder["scheduled_at"].isoformat(),
                    )
                return {
                    "instance_id": str(instance_id),
                    "status": "missed",
                    "sent_reminders": 0,
                    "notifications_created": 0,
                    "emails_targeted": 0,
                    "missed_reminders": len(missed_reminders),
                }
            return None

        notifications_created = 0
        emails_targeted = 0
        for reminder in queued_reminders:
            notify_count, email_count = ChecklistAutomationService._notify_instance_participants_for_reminder(
                instance_id=instance_payload["instance_id"],
                checklist_date=instance_payload["checklist_date"],
                shift=instance_payload["shift"],
                participants=participant_payloads,
                reminder=reminder,
            )
            notifications_created += notify_count
            emails_targeted += email_count

        return {
            "instance_id": instance_payload["instance_id"],
            "status": "sent",
            "sent_reminders": len(queued_reminders),
            "notifications_created": notifications_created,
            "emails_targeted": emails_targeted,
            "missed_reminders": len(missed_reminders),
        }

    @staticmethod
    async def _collect_due_instance_reminders(
        *,
        conn,
        instance,
        now_local: datetime,
        business_tz: ZoneInfo,
        reminder_log: Dict[str, Any],
        missed_log: Dict[str, Any],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        reminders: List[Dict[str, Any]] = []
        missed_reminders: List[Dict[str, Any]] = []

        timed_item_rows = await conn.fetch(
            """
            SELECT
                cii.id AS instance_item_id,
                cti.title,
                cti.description,
                cii.scheduled_at,
                COALESCE(cii.notify_before_minutes, 0) AS notify_before_minutes,
                cii.status
            FROM checklist_instance_items cii
            JOIN checklist_template_items cti ON cti.id = cii.template_item_id
            WHERE cii.instance_id = $1
              AND cii.scheduled_at IS NOT NULL
              AND cii.status::text <> ALL($2::text[])
            ORDER BY cii.scheduled_at ASC, cti.sort_order ASC, cti.title ASC
            """,
            instance["id"],
            list(ChecklistAutomationService.ACTIONED_ITEM_STATUSES),
        )
        for row in timed_item_rows:
            scheduled_at = row["scheduled_at"].astimezone(business_tz)
            reminder = ChecklistAutomationService._build_due_reminder_payload(
                reminder_log=reminder_log,
                now_local=now_local,
                scheduled_at=scheduled_at,
                notify_before_minutes=row["notify_before_minutes"],
                kind="timed_item",
                entity_id=str(row["instance_item_id"]),
                item_title=row["title"],
                item_description=row["description"],
                parent_title=None,
            )
            if reminder:
                reminders.append(reminder)
            else:
                missed = ChecklistAutomationService._build_missed_reminder_payload(
                    reminder_log=reminder_log,
                    missed_log=missed_log,
                    now_local=now_local,
                    scheduled_at=scheduled_at,
                    notify_before_minutes=row["notify_before_minutes"],
                    kind="timed_item",
                    entity_id=str(row["instance_item_id"]),
                    item_title=row["title"],
                    item_description=row["description"],
                    parent_title=None,
                )
                if missed:
                    missed_reminders.append(missed)

        timed_subitem_rows = await conn.fetch(
            """
            SELECT
                cis.id AS instance_subitem_id,
                cii.id AS instance_item_id,
                cti.title AS parent_title,
                cis.title,
                cis.description,
                cis.scheduled_at,
                COALESCE(cis.notify_before_minutes, 0) AS notify_before_minutes,
                cis.status
            FROM checklist_instance_subitems cis
            JOIN checklist_instance_items cii ON cii.id = cis.instance_item_id
            JOIN checklist_template_items cti ON cti.id = cii.template_item_id
            WHERE cii.instance_id = $1
              AND cis.scheduled_at IS NOT NULL
              AND cis.status::text <> ALL($2::text[])
            ORDER BY cis.scheduled_at ASC, cti.sort_order ASC, cis.sort_order ASC, cis.title ASC
            """,
            instance["id"],
            list(ChecklistAutomationService.ACTIONED_ITEM_STATUSES),
        )
        for row in timed_subitem_rows:
            scheduled_at = row["scheduled_at"].astimezone(business_tz)
            reminder = ChecklistAutomationService._build_due_reminder_payload(
                reminder_log=reminder_log,
                now_local=now_local,
                scheduled_at=scheduled_at,
                notify_before_minutes=row["notify_before_minutes"],
                kind="timed_subitem",
                entity_id=str(row["instance_subitem_id"]),
                item_title=row["title"],
                item_description=row["description"],
                parent_title=row["parent_title"],
            )
            if reminder:
                reminders.append(reminder)
            else:
                missed = ChecklistAutomationService._build_missed_reminder_payload(
                    reminder_log=reminder_log,
                    missed_log=missed_log,
                    now_local=now_local,
                    scheduled_at=scheduled_at,
                    notify_before_minutes=row["notify_before_minutes"],
                    kind="timed_subitem",
                    entity_id=str(row["instance_subitem_id"]),
                    item_title=row["title"],
                    item_description=row["description"],
                    parent_title=row["parent_title"],
                )
                if missed:
                    missed_reminders.append(missed)

        scheduled_event_rows = await conn.fetch(
            """
            SELECT
                cii.id AS instance_item_id,
                cti.title,
                cti.description,
                cise.id AS scheduled_event_id,
                cise.event_datetime,
                COALESCE(cise.notify_before_minutes, 30) AS notify_before_minutes,
                cii.status
            FROM checklist_instance_scheduled_events cise
            JOIN checklist_instance_items cii ON cii.id = cise.instance_item_id
            JOIN checklist_template_items cti ON cti.id = cii.template_item_id
            WHERE cii.instance_id = $1
              AND cii.status::text <> ALL($2::text[])
            ORDER BY cise.event_datetime ASC, cti.sort_order ASC
            """,
            instance["id"],
            list(ChecklistAutomationService.ACTIONED_ITEM_STATUSES),
        )
        for row in scheduled_event_rows:
            scheduled_at = row["event_datetime"].astimezone(business_tz)
            reminder = ChecklistAutomationService._build_due_reminder_payload(
                reminder_log=reminder_log,
                now_local=now_local,
                scheduled_at=scheduled_at,
                notify_before_minutes=row["notify_before_minutes"],
                kind="scheduled_event",
                entity_id=str(row["scheduled_event_id"]),
                item_title=row["title"],
                item_description=row["description"],
                parent_title=None,
            )
            if reminder:
                reminders.append(reminder)
            else:
                missed = ChecklistAutomationService._build_missed_reminder_payload(
                    reminder_log=reminder_log,
                    missed_log=missed_log,
                    now_local=now_local,
                    scheduled_at=scheduled_at,
                    notify_before_minutes=row["notify_before_minutes"],
                    kind="scheduled_event",
                    entity_id=str(row["scheduled_event_id"]),
                    item_title=row["title"],
                    item_description=row["description"],
                    parent_title=None,
                )
                if missed:
                    missed_reminders.append(missed)

        reminders.sort(key=lambda reminder: reminder["scheduled_at"])
        return reminders, missed_reminders

    @staticmethod
    def _build_due_reminder_payload(
        *,
        reminder_log: Dict[str, Any],
        now_local: datetime,
        scheduled_at: Optional[datetime],
        notify_before_minutes: Optional[int],
        kind: str,
        entity_id: str,
        item_title: str,
        item_description: Optional[str],
        parent_title: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if scheduled_at is None:
            return None

        lead_minutes = int(notify_before_minutes or 0)
        reminder_at = scheduled_at - timedelta(minutes=lead_minutes)
        if not (reminder_at <= now_local < scheduled_at):
            return None

        dedupe_key = f"{kind}:{entity_id}:{scheduled_at.isoformat()}:{lead_minutes}"
        if dedupe_key in reminder_log:
            return None

        return {
            "kind": kind,
            "entity_id": entity_id,
            "item_title": item_title,
            "item_description": item_description,
            "parent_title": parent_title,
            "notify_before_minutes": lead_minutes,
            "scheduled_at": scheduled_at,
            "reminder_at": reminder_at,
            "dedupe_key": dedupe_key,
        }

    @staticmethod
    def _build_missed_reminder_payload(
        *,
        reminder_log: Dict[str, Any],
        missed_log: Dict[str, Any],
        now_local: datetime,
        scheduled_at: Optional[datetime],
        notify_before_minutes: Optional[int],
        kind: str,
        entity_id: str,
        item_title: str,
        item_description: Optional[str],
        parent_title: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if scheduled_at is None or now_local < scheduled_at:
            return None

        lead_minutes = int(notify_before_minutes or 0)
        dedupe_key = f"{kind}:{entity_id}:{scheduled_at.isoformat()}:{lead_minutes}"
        if dedupe_key in reminder_log or dedupe_key in missed_log:
            return None

        return {
            "kind": kind,
            "entity_id": entity_id,
            "item_title": item_title,
            "item_description": item_description,
            "parent_title": parent_title,
            "notify_before_minutes": lead_minutes,
            "scheduled_at": scheduled_at,
            "dedupe_key": dedupe_key,
        }

    @staticmethod
    def _resolve_scheduled_datetime(
        *,
        checklist_date,
        scheduled_time,
        shift_start: datetime,
        shift_end: datetime,
        business_tz: ZoneInfo,
    ) -> Optional[datetime]:
        if scheduled_time is None:
            return None

        localized_start = shift_start.astimezone(business_tz)
        localized_end = shift_end.astimezone(business_tz)
        scheduled_at = datetime.combine(checklist_date, scheduled_time, tzinfo=business_tz)

        if localized_end.date() > localized_start.date() and scheduled_at < localized_start:
            scheduled_at += timedelta(days=1)

        return scheduled_at

    @staticmethod
    def _coerce_instance_metadata(raw_metadata: Any) -> Dict[str, Any]:
        if isinstance(raw_metadata, dict):
            return dict(raw_metadata)
        if raw_metadata in (None, ""):
            return {}
        try:
            if isinstance(raw_metadata, str):
                parsed = json.loads(raw_metadata)
                return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass
        return {}

    @staticmethod
    def _coerce_reminder_log(raw_log: Any) -> Dict[str, Any]:
        if isinstance(raw_log, dict):
            return dict(raw_log)
        return {}

    @staticmethod
    def _notify_instance_participants_for_reminder(
        *,
        instance_id: str,
        checklist_date: str,
        shift: str,
        participants,
        reminder: Dict[str, Any],
    ) -> Tuple[int, int]:
        title, message = ChecklistAutomationService._build_reminder_notification(
            shift=shift,
            reminder=reminder,
        )
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")
        checklist_link = f"{frontend_url}/checklist/{instance_id}"

        notified = 0
        recipient_emails: List[str] = []
        for participant in participants:
            participant_id = participant.get("id") if isinstance(participant, dict) else participant["id"]
            participant_email = participant.get("email") if isinstance(participant, dict) else participant["email"]
            if participant_id:
                try:
                    NotificationDBService.create_notification(
                        title=title,
                        message=message,
                        user_id=UUID(str(participant_id)),
                        related_entity="checklist_instance",
                        related_id=UUID(instance_id),
                    )
                    notified += 1
                except Exception as exc:
                    log.warning(
                        "Failed to create timed reminder notification for participant %s on instance %s: %s",
                        participant_id,
                        instance_id,
                        exc,
                    )

            email = (participant_email or "").strip()
            if email:
                recipient_emails.append(email)

        unique_emails = sorted(set(recipient_emails))
        emailed = 0
        if unique_emails:
            subject, text_body, html_body = ChecklistAutomationService._build_timed_reminder_email(
                shift=shift,
                checklist_date=checklist_date,
                checklist_link=checklist_link,
                reminder=reminder,
            )
            send_email_fire_and_forget(
                [os.getenv("SMTP_FROM", "sysops-alerts@afcholdings.co.zw")],
                subject,
                text_body,
                html_body,
                bcc=unique_emails,
            )
            emailed = len(unique_emails)

        return notified, emailed

    @staticmethod
    def _build_reminder_notification(
        *,
        shift: str,
        reminder: Dict[str, Any],
    ) -> Tuple[str, str]:
        schedule_label = reminder["scheduled_at"].strftime("%H:%M")
        lead_minutes = reminder["notify_before_minutes"]
        lead_label = "now" if lead_minutes == 0 else f"in {lead_minutes} min"

        if reminder["kind"] == "timed_subitem":
            title = f"SentinelOps Reminder • {shift} Subitem Due {lead_label}"
            message = (
                f"Timed subitem '{reminder['item_title']}' under '{reminder['parent_title']}' is due at {schedule_label}. "
                f"This reminder was scheduled {lead_minutes} minute(s) before execution."
            )
        elif reminder["kind"] == "scheduled_event":
            title = f"SentinelOps Reminder • {shift} Scheduled Event {lead_label}"
            message = (
                f"Scheduled checklist event '{reminder['item_title']}' is due at {schedule_label}. "
                f"Open the active shift instance and action it before the event time arrives."
            )
        else:
            title = f"SentinelOps Reminder • {shift} Timed Item Due {lead_label}"
            message = (
                f"Timed checklist item '{reminder['item_title']}' is due at {schedule_label}. "
                f"This reminder was scheduled {lead_minutes} minute(s) before execution."
            )

        return title, message

    @staticmethod
    def _build_timed_reminder_email(
        *,
        shift: str,
        checklist_date: str,
        checklist_link: str,
        reminder: Dict[str, Any],
    ) -> Tuple[str, str, str]:
        schedule_label = reminder["scheduled_at"].strftime("%H:%M")
        lead_minutes = reminder["notify_before_minutes"]
        trigger_label = "At scheduled time" if lead_minutes == 0 else f"{lead_minutes} minute(s) before"
        focus_label = reminder["item_title"]
        detail_label = reminder["parent_title"] if reminder["parent_title"] else None
        kind_label = {
            "timed_item": "Timed checklist item",
            "timed_subitem": "Timed checklist subitem",
            "scheduled_event": "Scheduled checklist event",
        }.get(reminder["kind"], "Checklist reminder")

        subject = f"SentinelOps // {shift} Shift Reminder: {focus_label} at {schedule_label}"
        text_body = (
            "SentinelOps Timed Reminder\n\n"
            f"Shift: {shift}\n"
            f"Date: {checklist_date}\n"
            f"Type: {kind_label}\n"
            f"Item: {focus_label}\n"
            + (f"Parent Item: {detail_label}\n" if detail_label else "")
            + f"Scheduled Time: {schedule_label}\n"
            + f"Reminder Trigger: {trigger_label}\n\n"
            + "Open the active shift checklist:\n"
            + f"{checklist_link}\n"
        )

        description = reminder.get("item_description") or "Stay ahead of the timed control window and close the action inside the live shift instance."
        parent_html = (
            f"""
              <tr>
                <td style="padding:10px 0;border-bottom:1px solid rgba(148,163,184,0.16);color:#94a3b8;font-size:12px;text-transform:uppercase;letter-spacing:0.08em;">Parent Item</td>
                <td style="padding:10px 0;border-bottom:1px solid rgba(148,163,184,0.16);color:#e2e8f0;font-size:14px;text-align:right;">{detail_label}</td>
              </tr>
            """
            if detail_label
            else ""
        )

        html_body = f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:24px;background:#020617;font-family:'Segoe UI','Helvetica Neue',Arial,sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:720px;margin:0 auto;border-collapse:separate;border-spacing:0;">
      <tr>
        <td style="background:linear-gradient(145deg,#0f172a 0%,#111827 70%,#1e293b 100%);border:1px solid rgba(56,189,248,0.22);border-radius:22px;box-shadow:0 24px 70px rgba(2,6,23,0.55);overflow:hidden;">
          <div style="padding:28px 30px;background:linear-gradient(135deg,rgba(34,211,238,0.22) 0%,rgba(14,165,233,0.16) 55%,rgba(2,6,23,0.15) 100%);">
            <div style="display:inline-block;padding:6px 12px;border-radius:999px;background:rgba(148,163,184,0.2);color:#e2e8f0;font-size:11px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;">SentinelOps Timed Reminder</div>
            <h1 style="margin:14px 0 8px;color:#f8fafc;font-size:28px;line-height:1.2;">{focus_label} due at {schedule_label}</h1>
            <p style="margin:0;color:#cbd5e1;font-size:15px;line-height:1.7;">{kind_label}. {description}</p>
          </div>
          <div style="padding:24px 30px 30px;">
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">
              <tr>
                <td style="padding:10px 0;border-bottom:1px solid rgba(148,163,184,0.16);color:#94a3b8;font-size:12px;text-transform:uppercase;letter-spacing:0.08em;">Shift</td>
                <td style="padding:10px 0;border-bottom:1px solid rgba(148,163,184,0.16);color:#e2e8f0;font-size:14px;text-align:right;">{shift}</td>
              </tr>
              <tr>
                <td style="padding:10px 0;border-bottom:1px solid rgba(148,163,184,0.16);color:#94a3b8;font-size:12px;text-transform:uppercase;letter-spacing:0.08em;">Date</td>
                <td style="padding:10px 0;border-bottom:1px solid rgba(148,163,184,0.16);color:#e2e8f0;font-size:14px;text-align:right;">{checklist_date}</td>
              </tr>
              <tr>
                <td style="padding:10px 0;border-bottom:1px solid rgba(148,163,184,0.16);color:#94a3b8;font-size:12px;text-transform:uppercase;letter-spacing:0.08em;">Type</td>
                <td style="padding:10px 0;border-bottom:1px solid rgba(148,163,184,0.16);color:#e2e8f0;font-size:14px;text-align:right;">{kind_label}</td>
              </tr>
              <tr>
                <td style="padding:10px 0;border-bottom:1px solid rgba(148,163,184,0.16);color:#94a3b8;font-size:12px;text-transform:uppercase;letter-spacing:0.08em;">Item</td>
                <td style="padding:10px 0;border-bottom:1px solid rgba(148,163,184,0.16);color:#e2e8f0;font-size:14px;text-align:right;">{focus_label}</td>
              </tr>
{parent_html}
              <tr>
                <td style="padding:10px 0;border-bottom:1px solid rgba(148,163,184,0.16);color:#94a3b8;font-size:12px;text-transform:uppercase;letter-spacing:0.08em;">Scheduled Time</td>
                <td style="padding:10px 0;border-bottom:1px solid rgba(148,163,184,0.16);color:#e2e8f0;font-size:14px;text-align:right;">{schedule_label}</td>
              </tr>
              <tr>
                <td style="padding:10px 0;color:#94a3b8;font-size:12px;text-transform:uppercase;letter-spacing:0.08em;">Reminder Trigger</td>
                <td style="padding:10px 0;color:#e2e8f0;font-size:14px;text-align:right;">{trigger_label}</td>
              </tr>
            </table>
            <div style="margin-top:22px;">
              <a href="{checklist_link}" style="display:inline-block;padding:12px 20px;border-radius:14px;background:#22d3ee;color:#020617;font-size:14px;font-weight:800;text-decoration:none;">Open Shift Checklist</a>
            </div>
          </div>
        </td>
      </tr>
    </table>
  </body>
</html>
"""
        return subject, text_body, html_body

    @staticmethod
    def _notify_shift_participants(
        *,
        instance_id: str,
        checklist_date: str,
        shift: str,
        participants: List[dict],
        created_new: bool,
    ) -> Tuple[int, int]:
        if not instance_id or not participants:
            return 0, 0

        title = f"SentinelOps Shift Initialization • {shift}"
        state = "initialized" if created_new else "available"
        message = (
            f"The {shift} shift checklist for {checklist_date} is now {state}.\n"
            f"Open the checklist, review handover intelligence, and begin execution."
        )

        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")
        checklist_link = f"{frontend_url}/checklist/{instance_id}"

        notified = 0
        recipient_emails: List[str] = []
        for participant in participants:
            participant_id = participant.get("id")
            if participant_id:
                try:
                    NotificationDBService.create_notification(
                        title=title,
                        message=message,
                        user_id=UUID(str(participant_id)),
                        related_entity="checklist_instance",
                        related_id=UUID(instance_id),
                    )
                    notified += 1
                except Exception as exc:
                    log.warning(f"Failed to notify participant {participant_id} for {instance_id}: {exc}")

            email = (participant.get("email") or "").strip()
            if email:
                recipient_emails.append(email)

        emailed = 0
        unique_emails = sorted(set(recipient_emails))
        if unique_emails:
            subject, text_body, html_body = ChecklistAutomationService._build_shift_init_email(
                shift=shift,
                checklist_date=checklist_date,
                checklist_link=checklist_link,
                created_new=created_new,
            )
            send_email_fire_and_forget(unique_emails, subject, text_body, html_body)
            emailed = len(unique_emails)

        return notified, emailed

    @staticmethod
    def _build_shift_init_email(
        *,
        shift: str,
        checklist_date: str,
        checklist_link: str,
        created_new: bool,
    ) -> Tuple[str, str, str]:
        status_line = "Checklist initialized and mission-ready" if created_new else "Checklist already initialized and ready"
        subject = f"SentinelOps // {shift} Shift Checklist {status_line}"

        text_body = (
            "SentinelOps Shift Alert\n\n"
            f"Shift: {shift}\n"
            f"Date: {checklist_date}\n"
            f"Status: {status_line}\n\n"
            "Open Checklist:\n"
            f"{checklist_link}\n\n"
            "Proceed with handover review and execution discipline."
        )

        html_body = f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:24px;background:#020617;font-family:'Segoe UI','Helvetica Neue',Arial,sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:720px;margin:0 auto;border-collapse:separate;border-spacing:0;">
      <tr>
        <td style="background:linear-gradient(145deg,#0f172a 0%,#111827 70%,#1e293b 100%);border:1px solid rgba(56,189,248,0.22);border-radius:22px;box-shadow:0 24px 70px rgba(2,6,23,0.55);overflow:hidden;">
          <div style="padding:28px 30px;background:linear-gradient(135deg,rgba(34,211,238,0.22) 0%,rgba(59,130,246,0.14) 55%,rgba(2,6,23,0.15) 100%);">
            <div style="display:inline-block;padding:6px 12px;border-radius:999px;background:rgba(148,163,184,0.2);color:#e2e8f0;font-size:11px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;">SentinelOps Command Pulse</div>
            <h1 style="margin:14px 0 8px;color:#f8fafc;font-size:28px;line-height:1.2;">{shift} Shift Checklist Ready</h1>
            <p style="margin:0;color:#cbd5e1;font-size:15px;line-height:1.7;">{status_line}. Evidence capture and handover continuity are now active for this shift window.</p>
          </div>
          <div style="padding:24px 30px 30px;">
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">
              <tr>
                <td style="padding:10px 0;border-bottom:1px solid rgba(148,163,184,0.16);color:#94a3b8;font-size:12px;text-transform:uppercase;letter-spacing:0.08em;">Shift</td>
                <td style="padding:10px 0;border-bottom:1px solid rgba(148,163,184,0.16);color:#e2e8f0;font-size:14px;text-align:right;">{shift}</td>
              </tr>
              <tr>
                <td style="padding:10px 0;border-bottom:1px solid rgba(148,163,184,0.16);color:#94a3b8;font-size:12px;text-transform:uppercase;letter-spacing:0.08em;">Date</td>
                <td style="padding:10px 0;border-bottom:1px solid rgba(148,163,184,0.16);color:#e2e8f0;font-size:14px;text-align:right;">{checklist_date}</td>
              </tr>
              <tr>
                <td style="padding:10px 0;color:#94a3b8;font-size:12px;text-transform:uppercase;letter-spacing:0.08em;">Status</td>
                <td style="padding:10px 0;color:#e2e8f0;font-size:14px;text-align:right;">{status_line}</td>
              </tr>
            </table>
            <div style="margin-top:22px;">
              <a href="{checklist_link}" style="display:inline-block;padding:12px 20px;border-radius:14px;background:#22d3ee;color:#020617;font-size:14px;font-weight:800;text-decoration:none;">Open Shift Checklist</a>
            </div>
          </div>
        </td>
      </tr>
    </table>
  </body>
</html>
"""
        return subject, text_body, html_body
