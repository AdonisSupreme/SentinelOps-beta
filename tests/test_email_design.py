"""Pure rendering checks: no SMTP, scheduler, credentials or database access."""
import ast
import importlib.util
from pathlib import Path
import unittest
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("email_design", ROOT / "app/core/email_design.py")
design = importlib.util.module_from_spec(spec)
spec.loader.exec_module(design)


def builder(name):
    tree = ast.parse((ROOT / "app/checklists/automation_service.py").read_text(encoding="utf-8"))
    function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)
    function.decorator_list = []
    namespace = {"render_email": design.render_email}
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), function], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "email_builder", "exec"), namespace)
    return namespace[name]


class EmailDesignTests(unittest.TestCase):
    def test_untrusted_content_is_escaped_and_link_retained(self):
        html = design.render_email(badge="Reminder", headline='<script>alert(1)</script>', intro='A & B', metadata=[("Item", '<img src=x>')], link='https://sentinel.example/checklist/123?a=1&b=2', cta_label="Open")
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;img src=x&gt;', html)
        self.assertIn('123?a=1&amp;b=2', html)
        self.assertNotIn('gradient(', html)

    def test_timed_reminder_preserves_details_and_plain_text(self):
        subject, text, html = builder('_build_timed_reminder_email')(
            shift='MORNING', checklist_date='2026-10-05', checklist_link='https://sentinel.example/checklist/123',
            reminder=dict(scheduled_at=datetime(2026,10,5,14,50), notify_before_minutes=5, item_title='Send status update', parent_title='Postilion monitoring', kind='timed_subitem', item_description='Review the service status.'),
        )
        for value in ['14:50', '5 minute(s) before', 'Postilion monitoring', 'https://sentinel.example/checklist/123']:
            self.assertIn(value, text)
            self.assertIn(value, html)
        self.assertIn('Send status update', subject)

    def test_shift_variants_keep_audience_and_state(self):
        kwargs = dict(shift='NIGHT', checklist_date='2026-10-05', checklist_link='https://sentinel.example/checklist/123')
        for audience in ['manager', 'operator']:
            subject, text, html = builder('_build_shift_init_delivery_email')(**kwargs, audience=audience)
            self.assertIn('Supervision' if audience == 'manager' else 'Execution', text)
            self.assertIn('Open Shift Checklist', html)
        for created in [True, False]:
            _, text, html = builder('_build_shift_init_email')(**kwargs, created_new=created)
            self.assertIn('already initialized' if not created else 'mission-ready', html)

    def test_control_code_and_expiry_remain_visible(self):
        html = design.render_email(badge='Control verification', headline='Verify restart', intro='Confirm your request', code='123456', code_hint='Expires in 5 minutes')
        self.assertIn('123456', html)
        self.assertIn('Expires in 5 minutes', html)


if __name__ == '__main__':
    unittest.main()
