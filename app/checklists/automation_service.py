from __future__ import annotations

from datetime import datetime
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
