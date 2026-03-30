"""
Simple async emailer using aiosmtplib.
Configuration via environment variables:
- SMTP_HOST
- SMTP_PORT (defaults to 587)
- SMTP_USER
- SMTP_PASSWORD
- SMTP_FROM
- SMTP_USE_TLS ("true"/"false") - wrap socket TLS
- SMTP_STARTTLS ("true"/"false") - use STARTTLS

This module exposes `send_email(to, subject, body_text, body_html=None, cc=None, bcc=None)` which is async
and safe to schedule via `asyncio.create_task()` from service code.
"""
import os
import asyncio
import logging
from email.message import EmailMessage
from typing import List, Optional

import aiosmtplib

log = logging.getLogger("app.core.emailer")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.office365.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "sysops-alerts@afcholdings.co.zw")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "##P@ssw0rd!!!")
SMTP_FROM = os.getenv("SMTP_FROM", "sysops-alerts@afcholdings.co.zw")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "false").lower() == "true"
SMTP_STARTTLS = os.getenv("SMTP_STARTTLS", "true").lower() == "true"


async def _send(msg: EmailMessage):
    if not SMTP_HOST:
        log.warning("SMTP_HOST not configured; skipping email send")
        return

    try:
        await aiosmtplib.send(
            msg,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            username=SMTP_USER,
            password=SMTP_PASSWORD,
            start_tls=SMTP_STARTTLS
        )
        log.debug("Email sent to %s subject=%s", msg.get_all("To"), msg.get("Subject"))
    except Exception as e:
        log.exception("Failed to send email: %s", e)


async def send_email(
    to: List[str],
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
):
    """Send an email asynchronously.

    This function raises no exceptions (exceptions are caught and logged).
    """
    if not to:
        log.debug("No recipients provided for email subject=%s; skipping", subject)
        return

    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        # Bcc header is optional for transport; keep for completeness
        msg["Bcc"] = ", ".join(bcc)

    msg["Subject"] = subject
    msg.set_content(body_text or "")

    if body_html:
        msg.add_alternative(body_html, subtype="html")

    # Run the send in background; allow callers to schedule via create_task
    try:
        await _send(msg)
    except Exception:
        # _send already logs exceptions; swallow here to avoid bubbling
        pass


def send_email_fire_and_forget(
    to: List[str],
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
):
    """Convenience to schedule an email without awaiting it."""
    try:
        # Avoid creating coroutine before loop availability is confirmed.
        loop = asyncio.get_running_loop()
        loop.create_task(send_email(to, subject, body_text, body_html, cc, bcc))
    except RuntimeError:
        # If event loop is not running in this thread, run in a new loop.
        def _runner():
            import asyncio as _asyncio
            _asyncio.run(send_email(to, subject, body_text, body_html, cc, bcc))
        import threading
        threading.Thread(target=_runner, daemon=True).start()
