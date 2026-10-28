"""No SMTP or database connections; exercises delivery and creation boundaries."""
import ast
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, MagicMock, patch
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WelcomeTests(unittest.TestCase):
    def setUp(self):
        from starlette.concurrency import run_in_threadpool
        self.inbox = Mock()
        self.email = AsyncMock()
        deps = {}
        for key, attrs in {
            'app.core.emailer': {'send_email': self.email},
            'app.core.frontend_links': {'build_frontend_url': lambda: 'https://configured.invalid/sentinel'},
            'app.notifications.db_service': {'NotificationDBService': SimpleNamespace(create_notification=self.inbox)},
        }.items():
            dep = ModuleType(key); dep.__dict__.update(attrs); deps[key] = dep
        spec = importlib.util.spec_from_file_location('welcome_design', ROOT/'app/core/email_design.py')
        design = importlib.util.module_from_spec(spec); spec.loader.exec_module(design)
        deps['app.core.email_design'] = design
        with patch.dict(sys.modules,deps):
            spec = importlib.util.spec_from_file_location('welcome_under_test',ROOT/'app/users/welcome.py')
            self.module = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.module)

    def test_targeted_welcome_uses_configured_url_escaped_html_and_no_password(self):
        asyncio.run(self.module.welcome_user('new-id','new@test.invalid','<Ada>','ada'))
        self.inbox.assert_called_once()
        self.assertEqual(self.inbox.call_args.kwargs['user_id'],'new-id')
        self.assertEqual(self.inbox.call_args.kwargs['related_entity'],'account_welcome')
        args = self.email.await_args.args
        self.assertEqual(args[0],['new@test.invalid'])
        self.assertIn('https://configured.invalid/sentinel',args[2])
        self.assertIn('href="https://configured.invalid/sentinel"',args[3])
        self.assertIn('&lt;Ada&gt;',args[3])
        self.assertNotIn('sentinel.example',args[3])

    def test_inbox_failure_still_attempts_email(self):
        self.inbox.side_effect = RuntimeError('offline')
        with self.assertLogs('welcome_under_test',level='ERROR'):
            asyncio.run(self.module.welcome_user('id','new@test.invalid','','ada'))
        self.email.assert_awaited_once()

    def test_email_failure_keeps_inbox_and_does_not_escape(self):
        self.email.side_effect = RuntimeError('SMTP unavailable')
        with self.assertLogs('welcome_under_test',level='ERROR'):
            asyncio.run(self.module.welcome_user('id','new@test.invalid','Ada','ada'))
        self.inbox.assert_called_once()

    def creation_function(self, duplicate=False, commit_error=False):
        # Execute the actual route body with isolated dependencies; no application
        # startup, authentication server, hashing, SMTP, or database is contacted.
        tree = ast.parse((ROOT/'app/users/router.py').read_text(encoding='utf-8'))
        function = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='create_user')
        function.decorator_list=[]
        function.returns=None
        for arg in function.args.args: arg.annotation=None
        function.args.defaults=[]
        conn=MagicMock(); cursor=conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect=[(1,)] if duplicate else [None,('role-id',),('new-id','created')]
        if commit_error: conn.commit.side_effect=RuntimeError('commit failed')
        from fastapi import HTTPException, status
        tasks=Mock()
        tasks.add_task.side_effect=lambda *args: self.assertTrue(conn.commit.called)
        env={'_ensure_admin':Mock(), '_normalize_role_name':lambda x:x,'get_connection':lambda:conn,
             'hash_password':lambda _: 'hash', 'audit':Mock(), 'log':Mock(),
             'HTTPException':HTTPException,'status':status,'welcome_user':self.module.welcome_user,
             'UserResponse':lambda **kw:kw}
        # Context manager must return the same connection under test.
        conn.__enter__.return_value=conn
        exec(compile(ast.Module(body=[function],type_ignores=[]),'create_user','exec'),env)
        payload=SimpleNamespace(role='user',username='ada',email='new@test.invalid',password='never-send-this',first_name='Ada',last_name='Test',department_id=None,section_id=None)
        return env['create_user'],payload,tasks

    def test_creation_schedules_once_after_commit_without_sending_inline(self):
        create,payload,tasks=self.creation_function()
        result=create(payload,tasks,{'id':'admin'})
        self.assertEqual(result['id'],'new-id')
        tasks.add_task.assert_called_once_with(self.module.welcome_user,'new-id',payload.email,'Ada','ada')
        self.email.assert_not_called(); self.inbox.assert_not_called()

    def test_duplicate_and_failed_transaction_do_not_schedule_welcome(self):
        for options in ({'duplicate':True},{'commit_error':True}):
            create,payload,tasks=self.creation_function(**options)
            with self.assertRaises(Exception): create(payload,tasks,{'id':'admin'})
            tasks.add_task.assert_not_called()


if __name__=='__main__': unittest.main()
