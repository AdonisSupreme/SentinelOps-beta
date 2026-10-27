"""Deterministic performance regressions. No database or notifications are touched."""
import ast
import sys
import types
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from unittest.mock import AsyncMock

source = Path(__file__).resolve().parents[1] / 'app/gamification/performance_service.py'
tree = ast.parse(source.read_text(encoding='utf-8-sig'))
# Load production calculations without initializing deployment settings or mail transports.
tree.body = [node for node in tree.body if not (isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('app.')) and not (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'log' for t in node.targets))]
module = types.ModuleType('performance_calculations')
module.settings = types.SimpleNamespace(TRUSTLINK_SCHEDULE_TIMEZONE='Africa/Harare')
sys.modules[module.__name__] = module
exec(compile(tree, str(source), 'exec'), module.__dict__)
M = module.DailyMetrics
S = module.PerformanceCommandService

def score(metrics, days=30, contributions=1, overdue=0):
    return module._score_aggregate(aggregate=metrics, elapsed_days=days, contribution_days=contributions, current_streak=1, overdue_open_tasks=overdue)

def snapshot(metrics):
    uid = uuid4(); today = module._business_today()
    return S._build_window_snapshots(window_key='yearly', start_date=today.replace(month=1,day=1), end_date=today, directory={uid:module.UserDirectoryEntry(uid,'operator','Operator')}, daily_maps={uid:{today:metrics}}, overdue_snapshot={})[uid]

class PerformanceFairnessTests(unittest.TestCase):
    def test_roster_and_passive_membership_are_not_activity(self):
        self.assertFalse(M(assigned_shifts=3,staffed_shifts=2,checklists_joined=2).has_activity())
        self.assertTrue(M(subitems_completed=1).has_activity())
    def test_no_deadline_is_not_reliability_evidence(self):
        result=score(M(tasks_completed=4,task_points=40))
        self.assertEqual(result['evidence']['deadline_tasks'],0)
        self.assertEqual(result['evidence']['status'],'no_evidence')
        self.assertEqual(result['reliability_points'],0)
        self.assertEqual(result['command_points'],40)
    def test_deadline_rate_ignores_undated_tasks(self):
        result=score(M(tasks_completed=20,tasks_completed_on_time=3,late_tasks_completed=1))
        self.assertEqual(result['task_on_time_rate'],75)
        self.assertEqual(result['evidence']['deadline_tasks'],4)
    def test_days_off_do_not_reduce_grade(self):
        m=M(tasks_completed=4,tasks_completed_on_time=4)
        self.assertEqual(score(m,days=4)['operational_grade'],score(m,days=300)['operational_grade'])
    def test_current_backlog_does_not_rewrite_contribution(self):
        m=M(tasks_completed=3,task_points=60,tasks_completed_on_time=3)
        self.assertEqual(score(m,overdue=0)['command_points'],score(m,overdue=20)['command_points'])
    def test_lateness_is_not_deducted_twice(self):
        result=score(M(task_points=100,late_tasks_completed=3,late_task_penalty_points=18))
        self.assertEqual(result['command_points'],82)
        self.assertEqual(result['repeat_lateness_penalty'],0)
    def test_one_success_is_marked_limited(self):
        self.assertEqual(score(M(tasks_completed=1,tasks_completed_on_time=1))['evidence']['status'],'limited')
    def test_inactive_operators_are_unranked_and_ties_share_rank(self):
        ids=[uuid4() for _ in range(3)]; today=module._business_today()
        data=S._build_window_snapshots(window_key='monthly',start_date=today.replace(day=1),end_date=today,directory={i:module.UserDirectoryEntry(i,str(i),str(i)) for i in ids}, daily_maps={i:{today:M(tasks_completed=1,task_points=10)} for i in ids[:2]},overdue_snapshot={})
        self.assertEqual([data[i].rank for i in ids],[1,1,0])
    def test_award_levels_are_distinct_and_sequential(self):
        s=snapshot(M(night_shift_actions=36,night_shift_runs=6))
        b={b['key']:b for b in S._build_badges(s)}
        self.assertTrue(b['night_owl']['claimable'])
        self.assertTrue(b['night_owl_level_2']['earned'])
        self.assertFalse(b['night_owl_level_2']['claimable'])
        b={b['key']:b for b in S._build_badges(s,{'night_owl':{'claimed_at':datetime.now(timezone.utc)}})}
        self.assertTrue(b['night_owl_level_2']['claimable'])
        self.assertFalse(b['night_owl_level_3']['earned'])
    def test_missing_gate_never_reports_full_progress(self):
        b=next(b for b in S._build_badges(snapshot(M(night_shift_actions=100,night_shift_runs=1))) if b['key']=='night_owl')
        self.assertFalse(b['earned']);self.assertEqual(b['progress'],50)
    def test_legacy_claim_survives_year_reset(self):
        now=datetime.now(timezone.utc)
        b=next(b for b in S._build_badges(snapshot(M()),{'night_owl':{'claimed_at':now}}) if b['key']=='night_owl')
        self.assertTrue(b['claimed']);self.assertTrue(b['earned']);self.assertEqual(b['claimed_at'],now)
    def test_inactive_user_has_zero_award_progress(self):
        self.assertTrue(all(b['progress']==0 and not b['earned'] for b in S._build_badges(snapshot(M()))))
    def test_trend_points_sum_to_window(self):
        uid=uuid4(); today=module._business_today(); start=today.replace(day=1)
        daily={uid:{start:M(task_points=20,tasks_completed=1,tasks_completed_on_time=1), today:M(task_points=30,tasks_completed=1,late_tasks_completed=1,late_task_penalty_points=4)}}
        snap=S._build_window_snapshots(window_key='monthly',start_date=start,end_date=today,directory={uid:module.UserDirectoryEntry(uid,'a','A')},daily_maps=daily,overdue_snapshot={uid:12})[uid]
        trend=S._build_trend(user_id=uid,daily_maps=daily,window_key='monthly',start_date=start,end_date=today,snapshot=snap)
        self.assertEqual(sum(p['command_points'] for p in trend),snap.command_points)

class PerformanceQueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_queries_exclude_passive_and_undated_credit(self):
        queries=[]
        class Connection:
            async def fetch(self,q,*args):queries.append(q);return []
        class Context:
            async def __aenter__(self):return Connection()
            async def __aexit__(self,*args):pass
        module.get_async_connection=lambda:Context()
        previous=S._column_exists;S._column_exists=AsyncMock(return_value=True)
        try: await S._load_daily_maps([uuid4()],date(2026,1,1),date(2026,10,17))
        finally:S._column_exists=previous
        task=next(q for q in queries if 'AS task_points' in q)
        self.assertIn('t.due_date IS NOT NULL AND t.completed_at <= t.due_date',task)
        participation=next(q for q in queries if 'AS checklists_joined' in q)
        self.assertIn('AND (EXISTS',participation)
        shifts=next(q for q in queries if 'AS assigned_shifts' in q)
        self.assertIn('ss.end_ts < now()',shifts)
        timing=next(q for q in queries if 'starts.user_id' in q)
        self.assertNotIn('cii.completed_by',timing)
        self.assertNotIn('cis.completed_by',timing)
        self.assertIn("BETWEEN cii.scheduled_at - INTERVAL '5 minutes'",timing)
        self.assertIn('starts.late_start_penalty_points > 0',timing)
        completion=next(q for q in queries if 'AS night_shift_actions' in q)
        self.assertIn('cii.completed_at BETWEEN ci.shift_start AND ci.shift_end',completion)
        self.assertIn('cis.completed_at BETWEEN ci.shift_start AND ci.shift_end',completion)

if __name__=='__main__':unittest.main()
