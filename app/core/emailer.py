"""Simple async emailer using aiosmtplib."""

import asyncio
import logging
from email.message import EmailMessage
from typing import List, Optional

import aiosmtplib

from app.core.config import settings

log = logging.getLogger("app.core.emailer")

SMTP_HOST = settings.SMTP_HOST or ""
SMTP_PORT = settings.SMTP_PORT
SMTP_USER = settings.SMTP_USER or ""
SMTP_PASSWORD = settings.SMTP_PASSWORD or ""
SMTP_FROM = settings.SMTP_FROM or ""
SMTP_USE_TLS = settings.SMTP_USE_TLS
SMTP_STARTTLS = settings.SMTP_STARTTLS


async def _send(msg: EmailMessage):
    if not SMTP_HOST:
        log.warning("SMTP_HOST not configured; skipping email send")
        return
    if not SMTP_FROM:
        log.warning("SMTP_FROM not configured; skipping email send")
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
