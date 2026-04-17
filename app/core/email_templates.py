"""
SentinelOps email templates for operational notifications.
Each template returns (subject, text_body, html_body).
"""

from html import escape
from typing import Iterable, Optional, Tuple

from app.core.frontend_links import build_frontend_url


def _task_link(task_id: str) -> str:
    return build_frontend_url("/tasks", query={"task": str(task_id)})


def _schedule_link() -> str:
    return build_frontend_url("/schedule")


def _performance_link() -> str:
    return build_frontend_url("/performance", fragment="badge-forge")


def _checklist_link(instance_id: str) -> str:
    return build_frontend_url(f"/checklist/{instance_id}")


def _network_sentinel_link(service_id: str, *, tab: str = "timeline") -> str:
    return build_frontend_url("/network-sentinel", query={"service": str(service_id), "tab": tab})


def _text_body(
    headline: str,
    lines: Iterable[str],
    metadata: Iterable[Tuple[str, str]],
    cta_label: str,
    link: str
) -> str:
    meta_lines = [f"{label}: {value}" for label, value in metadata if value]
    parts = ["SentinelOps", headline, ""]
    parts.extend(lines)
    if meta_lines:
        parts.append("")
        parts.extend(meta_lines)
    parts.extend(["", f"{cta_label}: {link}", "", "SentinelOps Command Center"])
    return "\n".join(parts)


def _html_body(
    *,
    badge: str,
    headline: str,
    intro: str,
    lines: Iterable[str],
    metadata: Iterable[Tuple[str, str]],
    cta_label: str,
    link: str,
    accent: str,
) -> str:
    safe_badge = escape(badge)
    safe_headline = escape(headline)
    safe_intro = escape(intro)
    safe_link = escape(link, quote=True)
    safe_cta = escape(cta_label)

    line_html = "".join(
        f'<p style="margin:0 0 12px;color:#cbd5e1;font-size:15px;line-height:1.7;">{escape(line)}</p>'
        for line in lines
    )
    metadata_html = "".join(
        (
            '<tr>'
            f'<td style="padding:10px 0;color:#94a3b8;font-size:12px;letter-spacing:0.08em;'
            f'text-transform:uppercase;border-bottom:1px solid rgba(148,163,184,0.14);">{escape(label)}</td>'
            f'<td style="padding:10px 0;color:#e2e8f0;font-size:14px;text-align:right;'
            f'border-bottom:1px solid rgba(148,163,184,0.14);">{escape(value)}</td>'
            '</tr>'
        )
        for label, value in metadata
        if value
    )

    return f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:24px;background:#020617;font-family:'Segoe UI','Helvetica Neue',Arial,sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:680px;margin:0 auto;border-collapse:separate;border-spacing:0;">
      <tr>
        <td style="padding:0;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
            style="background:linear-gradient(180deg,#08111f 0%,#0f172a 100%);border:1px solid rgba(56,189,248,0.14);border-radius:24px;overflow:hidden;box-shadow:0 24px 64px rgba(2,6,23,0.48);">
            <tr>
              <td style="padding:0;background:linear-gradient(135deg,{accent} 0%,#0f172a 85%);">
                <div style="padding:28px 32px 30px;">
                  <div style="display:inline-block;padding:7px 12px;border-radius:999px;background:rgba(255,255,255,0.14);color:#e2e8f0;font-size:11px;font-weight:700;letter-spacing:0.16em;text-transform:uppercase;">
                    {safe_badge}
                  </div>
                  <div style="margin-top:18px;color:#ffffff;font-size:30px;font-weight:800;line-height:1.15;letter-spacing:-0.03em;">
                    {safe_headline}
                  </div>
                  <div style="margin-top:12px;color:#dbeafe;font-size:15px;line-height:1.7;max-width:520px;">
                    {safe_intro}
                  </div>
                </div>
              </td>
            </tr>
            <tr>
              <td style="padding:28px 32px 18px;">
                {line_html}
                <div style="margin:20px 0 24px;">
                  <a href="{safe_link}" style="display:inline-block;padding:13px 20px;border-radius:14px;background:{accent};color:#020617;text-decoration:none;font-size:14px;font-weight:800;letter-spacing:0.02em;">
                    {safe_cta}
                  </a>
                </div>
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
                  style="border-collapse:collapse;padding:16px 18px;background:rgba(15,23,42,0.72);border:1px solid rgba(148,163,184,0.14);border-radius:18px;">
                  <tr>
                    <td colspan="2" style="padding:0 0 12px;color:#f8fafc;font-size:13px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;">
                      Operational Snapshot
                    </td>
                  </tr>
                  {metadata_html}
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:0 32px 26px;color:#64748b;font-size:12px;line-height:1.6;">
                This message was generated by SentinelOps to keep task coordination visible across email and in-app channels.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


def _build_template(
    *,
    subject: str,
    badge: str,
    headline: str,
    intro: str,
    lines: Iterable[str],
    metadata: Iterable[Tuple[str, str]],
    cta_label: str = "Open task in SentinelOps",
    task_id: Optional[str] = None,
    link: Optional[str] = None,
    accent: str = "#38bdf8",
) -> Tuple[str, str, str]:
    resolved_link = link or (_task_link(task_id) if task_id else None)
    if not resolved_link:
        raise ValueError("Either task_id or link must be provided")

    text = _text_body(headline, lines, metadata, cta_label, resolved_link)
    html = _html_body(
        badge=badge,
        headline=headline,
        intro=intro,
        lines=lines,
        metadata=metadata,
        cta_label=cta_label,
        link=resolved_link,
        accent=accent,
    )
    return subject, text, html


def assignment_template(
    recipient_name: Optional[str],
    task_title: str,
    task_id: str,
    actor_name: Optional[str] = None
) -> Tuple[str, str, str]:
    actor = actor_name or "A SentinelOps teammate"
    headline = f'{actor} assigned "{task_title}"'
    intro = f"{recipient_name or 'Team member'}, a task has just entered your queue."
    lines = [
        f'{actor} assigned you the task "{task_title}".',
        "Open the task to review the brief, timeline, and next actions.",
    ]
    metadata = [("Task", task_title), ("Assigned by", actor), ("Task ID", task_id)]
    return _build_template(
        subject=f"SentinelOps | Task assigned: {task_title}",
        badge="Task Assignment",
        headline=headline,
        intro=intro,
        lines=lines,
        metadata=metadata,
        task_id=task_id,
        accent="#22c55e",
    )


def comment_template(
    task_title: str,
    task_id: str,
    comment_text: str,
    actor_name: Optional[str] = None
) -> Tuple[str, str, str]:
    actor = actor_name or "A SentinelOps teammate"
    preview = comment_text.strip().replace("\n", " ")
    if len(preview) > 180:
        preview = f"{preview[:177]}..."
    lines = [
        f'{actor} added a new comment on "{task_title}".',
        f'Latest note: "{preview}"',
    ]
    metadata = [("Task", task_title), ("Commented by", actor), ("Task ID", task_id)]
    return _build_template(
        subject=f"SentinelOps | New comment on {task_title}",
        badge="Task Commentary",
        headline=f'New task comment on "{task_title}"',
        intro="Discussion activity has been recorded on this task.",
        lines=lines,
        metadata=metadata,
        task_id=task_id,
        accent="#38bdf8",
    )


def attachment_template(
    task_title: str,
    task_id: str,
    filename: str,
    actor_name: Optional[str] = None
) -> Tuple[str, str, str]:
    actor = actor_name or "A SentinelOps teammate"
    lines = [
        f'{actor} uploaded "{filename}" to "{task_title}".',
        "Review the attachment inside the task thread to keep context and approvals aligned.",
    ]
    metadata = [("Task", task_title), ("Attachment", filename), ("Uploaded by", actor)]
    return _build_template(
        subject=f"SentinelOps | Attachment added to {task_title}",
        badge="Task Evidence",
        headline=f'New attachment on "{task_title}"',
        intro="Supporting material has been added to this task record.",
        lines=lines,
        metadata=metadata,
        task_id=task_id,
        accent="#f59e0b",
    )


def created_template(
    task_title: str,
    task_id: str,
    actor_name: Optional[str] = None
) -> Tuple[str, str, str]:
    actor = actor_name or "You"
    lines = [
        f'{actor} created the task "{task_title}".',
        "The task is now available in SentinelOps for execution, updates, and evidence capture.",
    ]
    metadata = [("Task", task_title), ("Created by", actor), ("Task ID", task_id)]
    return _build_template(
        subject=f"SentinelOps | Task created: {task_title}",
        badge="Task Created",
        headline=f'"{task_title}" is now live',
        intro="A new operational task has been opened in SentinelOps.",
        lines=lines,
        metadata=metadata,
        task_id=task_id,
        accent="#22c55e",
    )


def status_change_template(
    task_title: str,
    task_id: str,
    old_status: str,
    new_status: str,
    actor_name: Optional[str] = None
) -> Tuple[str, str, str]:
    actor = actor_name or "A SentinelOps teammate"
    lines = [
        f'{actor} changed "{task_title}" from {old_status} to {new_status}.',
        "Open the task to review the latest context, comments, and supporting evidence.",
    ]
    metadata = [
        ("Task", task_title),
        ("Previous status", old_status),
        ("Current status", new_status),
        ("Updated by", actor),
    ]
    return _build_template(
        subject=f"SentinelOps | Status updated: {task_title}",
        badge="Task Status",
        headline=f'"{task_title}" moved to {new_status}',
        intro="Task execution state changed in SentinelOps.",
        lines=lines,
        metadata=metadata,
        task_id=task_id,
        accent="#06b6d4",
    )


def task_updated_template(
    task_title: str,
    task_id: str,
    update_summary: str,
    actor_name: Optional[str] = None
) -> Tuple[str, str, str]:
    actor = actor_name or "A SentinelOps teammate"
    lines = [
        f'{actor} updated "{task_title}".',
        update_summary,
        "Open the task to review the latest brief, schedule, and execution context.",
    ]
    metadata = [
        ("Task", task_title),
        ("Updated by", actor),
        ("Update focus", update_summary),
    ]
    return _build_template(
        subject=f"SentinelOps | Task updated: {task_title}",
        badge="Task Update",
        headline=f'"{task_title}" was updated',
        intro="Task details changed in SentinelOps.",
        lines=lines,
        metadata=metadata,
        task_id=task_id,
        accent="#8b5cf6",
    )


def task_due_soon_template(
    *,
    recipient_name: Optional[str],
    task_title: str,
    task_id: str,
    stage_label: str,
    due_at_label: str,
    is_overdue: bool = False,
) -> Tuple[str, str, str]:
    intro_name = recipient_name or "Team member"
    if is_overdue:
        lines = [
            f"{intro_name}, \"{task_title}\" is still open past its due time.",
            f"The deadline passed at {due_at_label}.",
            "Open the task now to update progress, capture blockers, or close it with the right evidence.",
        ]
        metadata = [
            ("Task", task_title),
            ("State", "Overdue"),
            ("Due at", due_at_label),
        ]
        return _build_template(
            subject=f"SentinelOps | Task overdue: {task_title}",
            badge="Task Escalation",
            headline=f"\"{task_title}\" is overdue",
            intro="SentinelOps detected a task that is now beyond its committed due time.",
            lines=lines,
            metadata=metadata,
            task_id=task_id,
            accent="#ef4444",
        )

    lines = [
        f"{intro_name}, \"{task_title}\" is entering its deadline window.",
        f"Reminder window: {stage_label} remaining.",
        f"Due at: {due_at_label}.",
        "Open the task to finish execution, update progress, or record blockers before time runs out.",
    ]
    metadata = [
        ("Task", task_title),
        ("Reminder window", stage_label),
        ("Due at", due_at_label),
    ]
    return _build_template(
        subject=f"SentinelOps | Task reminder: {task_title}",
        badge="Task Reminder",
        headline=f"\"{task_title}\" is due in {stage_label}",
        intro="SentinelOps is keeping deadline pressure visible before execution slips.",
        lines=lines,
        metadata=metadata,
        task_id=task_id,
        accent="#f59e0b",
    )


def shift_assignment_template(
    recipient_name: Optional[str],
    *,
    shift_name: str,
    assignment_date: str,
    shift_window: Optional[str] = None,
    actor_name: Optional[str] = None,
    section_name: Optional[str] = None,
) -> Tuple[str, str, str]:
    actor = actor_name or "Your SentinelOps coordinator"
    headline = f"{shift_name} shift confirmed for {assignment_date}"
    intro = f"{recipient_name or 'Team member'}, your SentinelOps schedule has been updated with a new shift assignment."
    lines = [
        f"{actor} assigned you to the {shift_name} shift for {assignment_date}.",
        "Open your schedule to review the reporting window, surrounding roster, and any adjacent coverage changes.",
    ]
    metadata = [
        ("Shift", shift_name),
        ("Date", assignment_date),
        ("Shift Window", shift_window or ""),
        ("Assigned by", actor),
        ("Section", section_name or ""),
    ]
    return _build_template(
        subject=f"SentinelOps | Shift assigned: {shift_name} on {assignment_date}",
        badge="Shift Assignment",
        headline=headline,
        intro=intro,
        lines=lines,
        metadata=metadata,
        cta_label="Open my schedule",
        link=_schedule_link(),
        accent="#5ac8fa",
    )


def shift_pattern_assignment_template(
    recipient_name: Optional[str],
    *,
    pattern_name: str,
    start_date: str,
    end_date: Optional[str] = None,
    actor_name: Optional[str] = None,
    section_name: Optional[str] = None,
) -> Tuple[str, str, str]:
    actor = actor_name or "Your SentinelOps coordinator"
    period_label = f"{start_date} to {end_date}" if end_date else f"starting {start_date}"
    headline = f"Schedule updated with {pattern_name}"
    intro = f"{recipient_name or 'Team member'}, SentinelOps has refreshed your roster with a new shift pattern."
    lines = [
        f"{actor} applied the {pattern_name} pattern to your schedule {period_label}.",
        "Use the schedule view to inspect each assigned day, verify coverage timing, and plan around off days or handovers.",
    ]
    metadata = [
        ("Pattern", pattern_name),
        ("Effective window", period_label),
        ("Assigned by", actor),
        ("Section", section_name or ""),
    ]
    return _build_template(
        subject=f"SentinelOps | Schedule updated: {pattern_name}",
        badge="Schedule Update",
        headline=headline,
        intro=intro,
        lines=lines,
        metadata=metadata,
        cta_label="Review my schedule",
        link=_schedule_link(),
        accent="#22c55e",
    )


def performance_badge_unlocked_template(
    *,
    recipient_name: Optional[str],
    badge_name: str,
    badge_description: str,
    badge_target: str,
    badge_hint: Optional[str] = None,
) -> Tuple[str, str, str]:
    intro_name = recipient_name or "Operator"
    lines = [
        f"{intro_name}, you just unlocked the {badge_name} badge in SentinelOps.",
        badge_description,
        "Open Badge Forge to review the milestone and claim it into your collection.",
    ]
    if badge_hint:
        lines.append(badge_hint)

    metadata = [
        ("Badge", badge_name),
        ("Unlock Target", badge_target),
        ("Status", "Ready to claim"),
    ]

    return _build_template(
        subject=f"SentinelOps | Badge unlocked: {badge_name}",
        badge="Badge Unlocked",
        headline=f"{badge_name} is ready to claim",
        intro="Badge Forge recorded a new milestone from your live operational work.",
        lines=lines,
        metadata=metadata,
        cta_label="Open Badge Forge",
        link=_performance_link(),
        accent="#22c55e",
    )


def checklist_review_required_template(
    *,
    recipient_name: Optional[str],
    instance_id: str,
    checklist_name: str,
    checklist_date: str,
    shift: str,
    actor_name: Optional[str] = None,
    section_name: Optional[str] = None,
) -> Tuple[str, str, str]:
    actor = actor_name or "A SentinelOps operator"
    lines = [
        f"{actor} finished execution on {checklist_name}.",
        "The checklist is now waiting for managerial approval in SentinelOps.",
        "Open the checklist to review item activity, evidence, and any exceptions before completing approval.",
    ]
    metadata = [
        ("Checklist", checklist_name),
        ("Date", checklist_date),
        ("Shift", shift),
        ("Section", section_name or ""),
        ("Prepared by", actor),
    ]
    return _build_template(
        subject=f"SentinelOps | Checklist pending approval: {checklist_name}",
        badge="Checklist Approval",
        headline=f"{checklist_name} is pending approval",
        intro=f"{recipient_name or 'Manager'}, a checklist in your section is ready for approval.",
        lines=lines,
        metadata=metadata,
        cta_label="Open checklist review",
        link=_checklist_link(instance_id),
        accent="#f59e0b",
    )


def checklist_exception_alert_template(
    *,
    recipient_name: Optional[str],
    instance_id: str,
    checklist_name: str,
    checklist_date: str,
    shift: str,
    target_type: str,
    target_title: str,
    action_label: str,
    actor_name: Optional[str] = None,
    reason: Optional[str] = None,
    section_name: Optional[str] = None,
) -> Tuple[str, str, str]:
    actor = actor_name or "A SentinelOps operator"
    lines = [
        f"{actor} marked the {target_type.lower()} \"{target_title}\" as {action_label.lower()}.",
        "This exception requires manager attention inside the live checklist record.",
    ]
    if reason:
        lines.append(f"Reported reason: {reason}")

    metadata = [
        ("Checklist", checklist_name),
        ("Date", checklist_date),
        ("Shift", shift),
        ("Section", section_name or ""),
        ("Affected", f"{target_type}: {target_title}"),
        ("Recorded by", actor),
    ]
    return _build_template(
        subject=f"SentinelOps | Critical checklist exception: {target_title}",
        badge="Critical Exception",
        headline=f"{target_title} needs manager attention",
        intro=f"{recipient_name or 'Manager'}, SentinelOps recorded a critical checklist exception in your section.",
        lines=lines,
        metadata=metadata,
        cta_label="Open checklist evidence",
        link=_checklist_link(instance_id),
        accent="#ef4444",
    )


def network_outage_alert_template(
    *,
    recipient_name: Optional[str],
    service_id: str,
    service_name: str,
    address: str,
    port: Optional[int],
    downtime_seconds: int,
    reason: Optional[str] = None,
    reminder: bool = False,
) -> Tuple[str, str, str]:
    service_endpoint = f"{address}{f':{port}' if port is not None else ''}"
    headline = f"{service_name} is still down" if reminder else f"{service_name} is down"
    lines = [
        f"{recipient_name or 'Operator'}, SentinelOps detected an active outage on {service_name}.",
        f"The service at {service_endpoint} has been unavailable for {downtime_seconds} seconds.",
        "Open Network Sentinel to review the live signal, outage timeline, and evidence window.",
    ]
    if reason:
        lines.insert(2, f"Observed reason: {reason}")

    metadata = [
        ("Service", service_name),
        ("Endpoint", service_endpoint),
        ("Downtime", f"{downtime_seconds} seconds"),
        ("Signal", "Reminder" if reminder else "Initial alert"),
    ]

    return _build_template(
        subject=f"SentinelOps | {'Reminder: ' if reminder else ''}{service_name} outage",
        badge="Critical Outage",
        headline=headline,
        intro="Network Sentinel escalated a live service interruption to the current shift.",
        lines=lines,
        metadata=metadata,
        cta_label="Open Network Sentinel",
        link=_network_sentinel_link(service_id),
        accent="#ef4444",
    )


def network_outage_recovered_template(
    *,
    recipient_name: Optional[str],
    service_id: str,
    service_name: str,
    address: str,
    port: Optional[int],
    downtime_seconds: int,
    reason: Optional[str] = None,
) -> Tuple[str, str, str]:
    service_endpoint = f"{address}{f':{port}' if port is not None else ''}"
    lines = [
        f"{recipient_name or 'Operator'}, {service_name} is reachable again in Network Sentinel.",
        f"The outage at {service_endpoint} lasted {downtime_seconds} seconds before recovery was confirmed.",
        "Open the timeline to review the resolved incident and its retained evidence.",
    ]
    if reason:
        lines.insert(2, f"Latest recovery note: {reason}")

    metadata = [
        ("Service", service_name),
        ("Endpoint", service_endpoint),
        ("Downtime", f"{downtime_seconds} seconds"),
        ("State", "Recovered"),
    ]

    return _build_template(
        subject=f"SentinelOps | {service_name} recovered",
        badge="Service Recovery",
        headline=f"{service_name} is back online",
        intro="Network Sentinel confirmed that a previously alerted outage has cleared.",
        lines=lines,
        metadata=metadata,
        cta_label="Review recovery timeline",
        link=_network_sentinel_link(service_id),
        accent="#22c55e",
    )
