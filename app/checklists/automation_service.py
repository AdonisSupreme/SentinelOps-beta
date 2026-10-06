from __future__ import annotations
from app.core.email_design import render_email

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from app.core.config import settings
from app.core.frontend_links import build_frontend_url
from app.core.logging import get_logger
from app.core.emailer import send_email_fire_and_forget
from app.db.database import get_async_connection
from app.checklists.db_service import ChecklistDBService
from app.notifications.db_service import NotificationDBService

log = get_logger("checklist-automation-service")


class ChecklistAutomationService:
    REMINDER_METADATA_KEY = "timed_reminders_sent"
    MISSED_REMINDER_METADATA_KEY = "timed_reminders_missed"
    SHIFT_INIT_DELIVERY_METADATA_KEY = "shift_init_delivery"
    ACTIVE_REMINDER_INSTANCE_STATUSES = ("OPEN", "IN_PROGRESS")
    ACTIONED_ITEM_STATUSES = ("COMPLETED", "SKIPPED", "FAILED")

    @staticmethod
    def _ordered_shift_names(candidate_names: List[str]) -> List[str]:
        normalized_candidates = {
            ChecklistDBService._normalize_shift_name(name)
            for name in candidate_names
            if str(name or "").strip()
        }
        configured_order = [
            shift_def["name"]
            for shift_def in ChecklistDBService.list_shift_definitions()
            if shift_def.get("name")
        ]
        ordered = [shift for shift in configured_order if shift in normalized_candidates]
        ordered.extend(sorted(normalized_candidates.difference(ordered)))
        return ordered

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

        all_active_templates = ChecklistDBService.list_templates(active_only=True)
        templates_by_shift: Dict[str, List[dict]] = {}
        for template in all_active_templates:
            shift_name = ChecklistDBService._normalize_shift_name(template.get("shift"))
            if not shift_name:
                continue
            templates_by_shift.setdefault(shift_name, []).append(template)

        configured_shift_names = [
            shift_def["name"]
            for shift_def in ChecklistDBService.list_shift_definitions()
            if shift_def.get("name")
        ]

        summary: List[Dict[str, Any]] = []
        for shift in ChecklistAutomationService._ordered_shift_names(
            configured_shift_names + list(templates_by_shift.keys())
        ):
            active_templates = templates_by_shift.get(shift, [])
            if not active_templates:
                summary.append({"shift": shift, "status": "skipped", "reason": "no_active_template"})
                continue

            section_ids: List[str] = []
            seen_sections = set()
            for template in active_templates:
                template_section_id = str(template.get("section_id")) if template.get("section_id") else None
                if not template_section_id:
                    summary.append(
                        {
                            "shift": shift,
                            "template_id": str(template.get("id")),
                            "status": "skipped",
                            "reason": "missing_section_id",
                        }
                    )
                    continue
                if template_section_id in seen_sections:
                    continue
                seen_sections.add(template_section_id)
                section_ids.append(template_section_id)

            if not section_ids:
                summary.append({"shift": shift, "status": "skipped", "reason": "no_active_section_template"})
                continue

            for section_id in section_ids:
                try:
                    template = ChecklistDBService.get_active_template_for_shift(shift, section_id)
                    if not template:
                        summary.append(
                            {
                                "shift": shift,
                                "section_id": section_id,
                                "status": "skipped",
                                "reason": "no_active_section_template",
                            }
                        )
                        continue

                    result = ChecklistDBService.create_checklist_instance(
                        checklist_date=today,
                        shift=shift,
                        created_by=actor_id,
                        created_by_username=actor_username,
                        template_id=UUID(str(template["id"])),
                        section_id=section_id,
                    )

                    instance_data = (result or {}).get("instance") or {}
                    instance_id = str((result or {}).get("id") or instance_data.get("id") or "")
                    participants = instance_data.get("participants") or []
                    created_new = (result or {}).get("message") == "New instance created"

                    notified_count, emailed_count = await ChecklistAutomationService._deliver_shift_initialization(
                        instance_id=instance_id,
                        checklist_date=str(today),
                        shift=shift,
                        created_new=created_new,
                    )

                    summary.append(
                        {
                            "shift": shift,
                            "section_id": section_id,
                            "template_id": str(template["id"]),
                            "status": "created" if created_new else "existing",
                            "instance_id": instance_id,
                            "participants": len(participants),
                            "notified": notified_count,
                            "emailed": emailed_count,
                        }
                    )
                except Exception as exc:
                    log.error(
                        "Failed to initialize checklist for shift %s in section %s on %s: %s",
                        shift,
                        section_id,
                        today,
                        exc,
                    )
                    summary.append(
                        {
                            "shift": shift,
                            "section_id": section_id,
                            "status": "failed",
                            "error": str(exc),
                        }
                    )

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
    def _coerce_shift_init_delivery_log(raw_log: Any) -> Dict[str, List[str]]:
        default_log: Dict[str, List[str]] = {
            "created_notified_user_ids": [],
            "created_emailed_user_ids": [],
            "existing_notified_user_ids": [],
            "existing_emailed_user_ids": [],
        }
        if not isinstance(raw_log, dict):
            return default_log

        normalized = dict(default_log)
        for key in normalized:
            value = raw_log.get(key)
            if isinstance(value, list):
                normalized[key] = [str(item) for item in value if item not in (None, "")]
        return normalized

    @staticmethod
    async def _resolve_shift_init_recipients(instance_id: str) -> Tuple[Optional[dict], List[dict], Dict[str, List[str]]]:
        try:
            instance_uuid = UUID(instance_id)
        except Exception:
            return None, [], ChecklistAutomationService._coerce_shift_init_delivery_log(None)

        async with get_async_connection() as conn:
            instance_row = await conn.fetchrow(
                """
                SELECT id, checklist_date, shift, section_id, metadata
                FROM checklist_instances
                WHERE id = $1
                """,
                instance_uuid,
            )
            if not instance_row:
                return None, [], ChecklistAutomationService._coerce_shift_init_delivery_log(None)

            recipients_by_user: Dict[str, dict] = {}

            participant_rows = await conn.fetch(
                """
                SELECT DISTINCT u.id, u.username, u.email, u.first_name, u.last_name
                FROM checklist_participants cp
                JOIN users u ON u.id = cp.user_id
                WHERE cp.instance_id = $1
                  AND u.is_active = TRUE
                ORDER BY u.username ASC
                """,
                instance_uuid,
            )
            for row in participant_rows:
                user_id = str(row["id"])
                recipients_by_user[user_id] = {
                    "id": user_id,
                    "username": row["username"],
                    "email": row["email"],
                    "first_name": row["first_name"],
                    "last_name": row["last_name"],
                    "audience": "participant",
                }

            if instance_row["section_id"]:
                manager_rows = await conn.fetch(
                    """
                    SELECT DISTINCT u.id, u.username, u.email, u.first_name, u.last_name
                    FROM users u
                    JOIN user_roles ur ON ur.user_id = u.id
                    JOIN roles r ON r.id = ur.role_id
                    WHERE u.is_active = TRUE
                      AND u.section_id = $1
                      AND LOWER(COALESCE(r.name, '')) = 'manager'
                    ORDER BY u.username ASC
                    """,
                    instance_row["section_id"],
                )
                for row in manager_rows:
                    user_id = str(row["id"])
                    recipients_by_user[user_id] = {
                        "id": user_id,
                        "username": row["username"],
                        "email": row["email"],
                        "first_name": row["first_name"],
                        "last_name": row["last_name"],
                        "audience": "manager",
                    }

            delivery_log = ChecklistAutomationService._coerce_shift_init_delivery_log(
                ChecklistAutomationService._coerce_instance_metadata(instance_row["metadata"]).get(
                    ChecklistAutomationService.SHIFT_INIT_DELIVERY_METADATA_KEY
                )
            )

        return (
            {
                "id": str(instance_row["id"]),
                "checklist_date": str(instance_row["checklist_date"]),
                "shift": instance_row["shift"],
                "section_id": str(instance_row["section_id"]) if instance_row["section_id"] else None,
            },
            list(recipients_by_user.values()),
            delivery_log,
        )

    @staticmethod
    async def _store_shift_init_delivery_log(instance_id: str, delivery_log: Dict[str, List[str]]) -> None:
        try:
            instance_uuid = UUID(instance_id)
        except Exception:
            return

        async with get_async_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT metadata
                FROM checklist_instances
                WHERE id = $1
                """,
                instance_uuid,
            )
            if not row:
                return

            metadata = ChecklistAutomationService._coerce_instance_metadata(row["metadata"])
            metadata[ChecklistAutomationService.SHIFT_INIT_DELIVERY_METADATA_KEY] = delivery_log
            await conn.execute(
                """
                UPDATE checklist_instances
                SET metadata = $2::jsonb
                WHERE id = $1
                """,
                instance_uuid,
                json.dumps(metadata),
            )

    @staticmethod
    def _build_shift_init_delivery_notification(
        *,
        shift: str,
        checklist_date: str,
        audience: str,
        created_new: bool,
    ) -> Tuple[str, str, str, str]:
        if audience == "manager":
            title = f"SentinelOps Review Watch • {shift}"
            message = (
                f"The {shift} shift checklist for {checklist_date} is "
                f"{'ready for supervision and review' if created_new else 'still active in your section'}.\n"
                "Open the live checklist, monitor execution, and stay ready for manager review flow."
            )
            related_entity = "checklist_manager_review"
            priority = "medium"
        else:
            title = f"SentinelOps Shift Ready • {shift}"
            message = (
                f"The {shift} shift checklist for {checklist_date} is "
                f"{'ready for execution' if created_new else 'still active and ready'}.\n"
                "Open the checklist, review handover intelligence, and begin execution."
            )
            related_entity = "schedule"
            priority = "medium"

        return title, message, related_entity, priority

    @staticmethod
    def _build_shift_init_delivery_email(
        *,
        shift: str,
        checklist_date: str,
        checklist_link: str,
        audience: str,
    ) -> Tuple[str, str, str]:
        is_manager = audience == "manager"
        audience_label = "Supervision" if is_manager else "Execution"
        status_line = (
            "Checklist initialized and ready for supervision and review"
            if is_manager
            else "Checklist initialized and ready for execution"
        )
        subject = f"SentinelOps // {shift} Shift Checklist {audience_label} Ready"

        text_body = (
            "SentinelOps Shift Alert\n\n"
            f"Shift: {shift}\n"
            f"Date: {checklist_date}\n"
            f"Audience: {audience_label}\n"
            f"Status: {status_line}\n\n"
            "Open Checklist:\n"
            f"{checklist_link}\n\n"
            + (
                "Use this checklist for supervision, operational visibility, and approval readiness."
                if is_manager
                else "Proceed with handover review and disciplined execution inside the live checklist."
            )
        )

        html_body = render_email(badge="Shift ready", headline=f"{shift} shift checklist ready", intro=status_line, metadata=[("Shift", shift), ("Date", checklist_date), ("Audience", audience_label), ("Status", status_line)], lines=["Review the handover, then open the checklist to continue your shift."], cta_label="Open Shift Checklist", link=checklist_link)
        return subject, text_body, html_body

    @staticmethod
    async def _deliver_shift_initialization(
        *,
        instance_id: str,
        checklist_date: str,
        shift: str,
        created_new: bool,
    ) -> Tuple[int, int]:
        if not instance_id:
            return 0, 0

        _, recipients, delivery_log = await ChecklistAutomationService._resolve_shift_init_recipients(instance_id)
        if not recipients:
            return 0, 0

        notification_key = "created_notified_user_ids" if created_new else "existing_notified_user_ids"
        email_key = "created_emailed_user_ids" if created_new else "existing_emailed_user_ids"
        already_notified = set(delivery_log.get(notification_key, []))
        already_emailed = set(delivery_log.get(email_key, []))
        checklist_link = build_frontend_url(f"/checklist/{instance_id}")

        notified = 0
        emailed = 0
        for recipient in recipients:
            recipient_id = str(recipient.get("id") or "").strip()
            if not recipient_id:
                continue

            audience = "manager" if recipient.get("audience") == "manager" else "participant"
            title, message, related_entity, priority = ChecklistAutomationService._build_shift_init_delivery_notification(
                shift=shift,
                checklist_date=checklist_date,
                audience=audience,
                created_new=created_new,
            )

            if recipient_id not in already_notified:
                try:
                    NotificationDBService.create_notification(
                        title=title,
                        message=message,
                        user_id=UUID(recipient_id),
                        related_entity=related_entity,
                        related_id=UUID(instance_id),
                        priority=priority,
                    )
                    notified += 1
                    already_notified.add(recipient_id)
                except Exception as exc:
                    log.warning(
                        "Failed to create shift-init notification for %s (%s) on %s: %s",
                        recipient_id,
                        audience,
                        instance_id,
                        exc,
                    )

            if not created_new or recipient_id in already_emailed:
                continue

            email = str(recipient.get("email") or "").strip()
            if not email:
                continue

            subject, text_body, html_body = ChecklistAutomationService._build_shift_init_delivery_email(
                shift=shift,
                checklist_date=checklist_date,
                checklist_link=checklist_link,
                audience=audience,
            )
            send_email_fire_and_forget([email], subject, text_body, html_body)
            emailed += 1
            already_emailed.add(recipient_id)

        delivery_log[notification_key] = sorted(already_notified)
        delivery_log[email_key] = sorted(already_emailed)
        await ChecklistAutomationService._store_shift_init_delivery_log(instance_id, delivery_log)

        return notified, emailed

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
        checklist_link = build_frontend_url(f"/checklist/{instance_id}")

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
                [settings.SMTP_FROM] if settings.SMTP_FROM else [],
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
        html_body = render_email(badge="Timed reminder", headline=f"{focus_label} due at {schedule_label}", intro=f"{kind_label}. {description}", metadata=[("Shift", shift), ("Date", checklist_date), ("Type", kind_label), ("Item", focus_label), ("Parent item", detail_label), ("Scheduled time", schedule_label), ("Reminder trigger", trigger_label)], cta_label="Open Shift Checklist", link=checklist_link)
        return subject, text_body, html_body

    @staticmethod
    async def _notify_shift_participants(
        *,
        instance_id: str,
        checklist_date: str,
        shift: str,
        participants: List[dict],
        created_new: bool,
    ) -> Tuple[int, int]:
        if not instance_id:
            return 0, 0

        title = f"SentinelOps Shift Initialization • {shift}"
        state = "initialized" if created_new else "available"
        message = (
            f"The {shift} shift checklist for {checklist_date} is now {state}.\n"
            f"Open the checklist, review handover intelligence, and begin execution."
        )

        checklist_link = build_frontend_url(f"/checklist/{instance_id}")

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

        html_body = render_email(badge="Shift ready", headline=f"{shift} shift checklist ready", intro=status_line, metadata=[("Shift", shift), ("Date", checklist_date), ("Status", status_line)], cta_label="Open Shift Checklist", link=checklist_link)
        return subject, text_body, html_body
