"""Post-creation welcome delivery, independent of the user transaction."""
import logging
from starlette.concurrency import run_in_threadpool
from app.core.email_design import render_email
from app.core.emailer import send_email
from app.core.frontend_links import build_frontend_url
from app.notifications.db_service import NotificationDBService

log = logging.getLogger(__name__)


async def welcome_user(user_id, email, first_name, username):
    try:
        link = build_frontend_url()
        name = first_name or username
        title = 'Welcome to SentinelOps'
        intro = f'{name}, welcome to a clearer, more connected operation.'
        lines = [
            'Your SentinelOps account is ready. Bring your work into focus, stay connected with your team, and make every handover count.',
            'Sign in with your assigned credentials. Your workspace shows the tools available to your section and role.',
            'Start with your available workspaces and the in-app guides. If you need help signing in or accessing a tool, contact your SentinelOps administrator.',
            'Clarity. Continuity. Control. Your contribution makes the difference.',
        ]
        text = '\n\n'.join([intro, *lines, f'Open SentinelOps: {link}'])
        inbox_text = (f'{intro} Your account is ready. Sign in to discover your assigned '
                      f'workspaces, connect with your team, and keep every handover clear. '
                      f'Open SentinelOps: {link}')
        try:
            await run_in_threadpool(NotificationDBService.create_notification,
                title=title, message=inbox_text, user_id=user_id,
                related_entity='account_welcome', priority='low')
        except Exception:
            log.exception('Welcome inbox delivery failed for user %s', user_id)
        try:
            await send_email([email], title, text, render_email(
                badge='Your workspace awaits', headline=title, intro=intro,
                lines=lines, cta_label='Open SentinelOps', link=link))
        except Exception:
            log.exception('Welcome email delivery failed for user %s', user_id)
    except Exception:
        log.exception('Unable to prepare welcome for user %s', user_id)
