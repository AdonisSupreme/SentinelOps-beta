"""Email transport and configured-link regression checks. Never connects to SMTP."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]

def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class EmailDeliveryRegressionTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(
            SMTP_HOST='smtp.test.invalid', SMTP_PORT=587, SMTP_USER='sender@test.invalid',
            SMTP_PASSWORD='test-only-password', SMTP_FROM='sender@test.invalid',
            SMTP_USE_TLS=False, SMTP_STARTTLS=True, FRONTEND_URL='http://configured-host:3033',
        )
        config = ModuleType('app.core.config')
        config.settings = self.settings
        transport = ModuleType('aiosmtplib')
        self.send = transport.send = AsyncMock(return_value=({}, 'OK'))
        self.modules = patch.dict(sys.modules, {'app.core.config':config, 'aiosmtplib':transport})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.emailer = load('regression_emailer', 'app/core/emailer.py')
        self.links = load('regression_links', 'app/core/frontend_links.py')
        self.design = load('regression_design', 'app/core/email_design.py')

    def test_html_and_plaintext_use_identical_transport_and_recipients(self):
        for html in [None, self.design.render_email(badge='Reminder', headline='Shift ready', intro='Open your shift')]:
            asyncio.run(self.emailer.send_email(['operator@test.invalid'], 'Shift ready', 'Original text', html,
                cc=['supervisor@test.invalid'], bcc=['audit@test.invalid']))
        first, second = self.send.await_args_list
        self.assertEqual(first.kwargs, second.kwargs)
        self.assertEqual(second.kwargs, dict(hostname=self.settings.SMTP_HOST, port=587,
            username=self.settings.SMTP_USER, password=self.settings.SMTP_PASSWORD, start_tls=True))
        for header in ['From', 'To', 'Cc', 'Bcc', 'Subject']:
            self.assertEqual(first.args[0][header], second.args[0][header])
        self.assertEqual(second.args[0].get_body(preferencelist=('plain',)).get_content(), 'Original text\n')
        self.assertIn('Shift ready', second.args[0].get_body(preferencelist=('html',)).get_content())

    def test_checklist_cta_uses_configured_host_without_demo_or_fallback(self):
        for host in ['http://configured-host:3033', 'https://operations.test.invalid/sentinel/']:
            self.settings.FRONTEND_URL = host
            link = self.links.build_frontend_url('/checklist/instance-123')
            html = self.design.render_email(badge='Reminder', headline='Shift ready', intro='Open your shift',
                link=link, cta_label='Open Shift Checklist')
            self.assertIn('href="' + host.rstrip('/') + '/checklist/instance-123"', html)
            self.assertNotIn('sentinel.example', html)
            self.assertNotIn('If the button does not open', html)

    def test_authentication_failure_does_not_retry_or_change_credentials(self):
        self.send.side_effect = RuntimeError('535 authentication rejected')
        with self.assertLogs('app.core.emailer', level='ERROR'):
            asyncio.run(self.emailer.send_email(['operator@test.invalid'], 'Test', 'Test'))
        self.send.assert_awaited_once()
        self.assertEqual(self.send.await_args.kwargs['password'], self.settings.SMTP_PASSWORD)

if __name__ == '__main__':
    unittest.main()
