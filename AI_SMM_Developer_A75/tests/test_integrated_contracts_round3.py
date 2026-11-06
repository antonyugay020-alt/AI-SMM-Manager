"""Adversarial cross-component tests: stored results, plan, state, closure."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from task_orchestration import TaskLedger
from plan_governance import govern_plan, structural_issues
from completion_gate import can_close_task
try:
    from task_artifacts import is_latest_document
except ImportError:
    def is_latest_document(*args):
        return False


class DurableAndConsistentTaskTests(unittest.TestCase):
    def test_corrupt_boolean_does_not_complete_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            data={'signature':'boolean', 'completed':'false', 'outputs': {
                'strategy':{'kind':'document','status':'accepted','revisions':0},
                'calendar':{'kind':'document','status':'accepted','revisions':0}}}
            (Path(tmp)/'task_orchestration_boolean.json').write_text(json.dumps(data))
            ledger=TaskLedger.open(tmp,'boolean')
            self.assertIs(ledger.completed,False)

    def test_accepted_status_reason_update_preserves_completed_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=TaskLedger.open(tmp,'finished')
            ledger.register('document',['strategy','calendar'])
            ledger.reconcile([],strategy=True,calendar=True)
            self.assertTrue(ledger.completed)
            ledger.set_status('calendar','accepted',reason='verified')
            self.assertTrue(ledger.completed)
            self.assertTrue(TaskLedger.open(tmp,'finished').completed)

    def test_registration_kind_collision_is_rejected_without_side_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=TaskLedger.open(tmp,'identity')
            ledger.register('document',['strategy'])
            before=dict(ledger.outputs['strategy'])
            with self.assertRaises(ValueError):
                ledger.register('publication',['strategy'])
            self.assertEqual(ledger.outputs['strategy'],before)

    def test_replanned_candidate_must_remain_structurally_valid(self):
        slots=[dict(week=1,day='Вт',topic='How to cultivate pottery colors',format='Reels',
                    platform='Instagram',goal='Education',audience='Art buyers',brief='Technique'),
               dict(week=1,day='Пт',topic='How to change engine oil',format='Карусель',
                    platform='Instagram',goal='Education',audience='Art buyers',brief='Technique')]
        candidate={k:v for k,v in slots[1].items() if k!='platform'}
        candidate['topic']='Texture experiments in ceramic glazing'
        def review(model,system,user):
            obj=json.loads(user)
            return json.dumps({'issues':[{'id':'1/Пт','dimension':'topic','reason':'out of business scope'}]} if 'plan' in obj else {'issues':[]})
        with patch('plan_governance.propose_topic_replacement',return_value=[candidate]):
            revised,blocked,available=govern_plan(review,'local-model','Pottery business',{},'',slots,set(),
                                                   expected_ids=['1/Вт','1/Пт'])
        self.assertFalse(available and not blocked and structural_issues(revised,['1/Вт','1/Пт']))
        self.assertIn('1/Пт',blocked)

    def test_closure_requires_explicit_recognized_media_state(self):
        report={'scope':{'weeks':1,'per_week':1,'planned_total':1},
                'deliverables':{'strategy':'validated','calendar':'validated'},
                'checked_posts':1,
                'content_components_checked':{f:1 for f in ('caption','visual','cta','kpi','audience','measurement','materials')},
                'unfulfilled':[],'plan_preflight':{'reviewer_available':True,'unresolved_slots':[]},
                'task_orchestration':{'completed':True},'independent_editor':{'status':'PASS','issues':[]}}
        self.assertFalse(can_close_task({'strategy','calendar'},report))
        report['rendered_media']='mystery'
        self.assertFalse(can_close_task({'strategy','calendar'},report))
        report['rendered_media']='not_requested'
        self.assertTrue(can_close_task({'strategy','calendar'},report))

    def test_document_save_verification_must_reject_older_versions(self):
        db=sqlite3.connect(':memory:')
        try:
            db.execute('CREATE TABLE manager_documents (id TEXT, project_id TEXT, kind TEXT, title TEXT, content TEXT, created_at TEXT, updated_at TEXT)')
            rows=[('1','project-A','strategy','title','old verified content','2026-01-01','2026-01-01'),
                  ('2','project-A','strategy','title','new human-edited content','2026-01-02','2026-01-02'),
                  ('3','project-B','strategy','title','old verified content','2026-01-01','2026-01-01')]
            db.executemany('INSERT INTO manager_documents VALUES (?,?,?,?,?,?,?)',rows)
            self.assertFalse(is_latest_document(db,'project-A','strategy','old verified content'))
            self.assertTrue(is_latest_document(db,'project-A','strategy','new human-edited content'))
            self.assertTrue(is_latest_document(db,'project-B','strategy','old verified content'))
        finally:
            db.close()

if __name__=='__main__': unittest.main()

class NaturalLanguageContractTests(unittest.TestCase):
    def test_weekly_cadence_variants_must_not_silently_change_workload(self):
        from content_requirements import publication_scope
        requests=(
            'Создай стратегию и контент-план на 2 недели: по два поста каждую неделю',
            'Создай стратегию и контент-план на 2 недели: публиковать 2 поста еженедельно',
            'Создай стратегию и контент-план на 2 недели: еженедельно по 2 публикации',
            'Создай стратегию и контент-план на 2 недели: дважды в неделю публикации',
        )
        for req in requests:
            with self.subTest(req=req):
                scope=publication_scope(req)
                self.assertEqual((scope.weeks, scope.per_week,scope.total),(2,2,4))
                self.assertEqual(scope.cadence_source,'explicit')

    def test_conflicting_weeks_and_months_cannot_be_silently_ignored(self):
        from professional_content import requested_weeks
        for request in ('Контент-план на один месяц и на десять недель',
                        'Контент-план на 1 месяц и 10 недель',
                        'Контент-план на 2 недели и 3 месяца'):
            with self.subTest(request=request):
                with self.assertRaisesRegex(ValueError,'Противоречивые'):
                    requested_weeks(request)

    def test_plan_audit_conflicting_or_duplicate_reviewer_response_fails_closed(self):
        from plan_governance import parse_review
        ids={'1/Вт','1/Пт'}
        for body in ('{"issues":[],"approved":false}',
                     '{"issues":[{"id":"1/Вт","reason":"а"},{"id":"1/Вт","reason":"б"}]}',
                     '{"issues":[],"failed":true}'):
            with self.subTest(body=body):
                self.assertIsNone(parse_review(body,ids))

    def test_bad_checkpoint_entry_never_permits_recovery_as_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            data={'signature':'damaged','completed':True,'outputs':{
                'strategy':{'kind':'document','status':'accepted','revisions':0},
                'post':{'kind':'publication','status':'accepted','revisions':[]}}}
            (Path(tmp)/'task_orchestration_damaged.json').write_text(json.dumps(data))
            ledger=TaskLedger.open(tmp,'damaged')
            self.assertFalse(ledger.completed)

class AtomicStorageBoundaryTests(unittest.TestCase):
    def test_interrupted_checkpoint_commit_preserves_old_record(self):
        from durable_json import atomic_write_json
        with tempfile.TemporaryDirectory() as td:
            dest=Path(td)/'checkpoint.json'
            atomic_write_json(dest,{'signature':'a','status':'accepted'})
            old=dest.read_bytes()
            with patch('durable_json.os.replace',side_effect=OSError('simulated disk error')):
                with self.assertRaises(OSError):
                    atomic_write_json(dest,{'signature':'b','status':'blocked'})
            self.assertEqual(dest.read_bytes(),old)
            self.assertEqual([p.name for p in Path(td).iterdir()],['checkpoint.json'])

    def test_consecutive_writes_produce_complete_json_without_temps(self):
        from durable_json import atomic_write_json
        with tempfile.TemporaryDirectory() as td:
            dest=Path(td)/'state.json'
            for n in range(120):
                value={'revision':n,'values':list(range(n))}
                atomic_write_json(dest,value)
                self.assertEqual(json.loads(dest.read_text(encoding="utf-8")),value)
            self.assertEqual([p.name for p in Path(td).iterdir()],['state.json'])
