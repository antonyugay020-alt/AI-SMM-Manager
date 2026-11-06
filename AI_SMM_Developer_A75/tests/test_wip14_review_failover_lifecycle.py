"""Distinct reviewer infrastructure failures never certify author output.

Each scenario has a unique task brief, separate from legacy fixed regressions.
"""
import random
import secrets
import unittest
from unittest.mock import patch
from production_pipeline import run_document_task
from reviewer_outcomes import review_unavailable, unavailable_verdict
from qt_worker_lifecycle import active_workers, drain_finished_workers


class Wip14ReviewerLifecycleTests(unittest.TestCase):
    def run_case(self, primary, backup):
        seed=secrets.randbits(64)
        print(f'WIP14 reviewer task seed: {seed}',flush=True)
        rng=random.Random(seed)
        token=f'{rng.getrandbits(80):020x}'
        request=f'Разработать отдельную стратегию для нового сервиса {token}; не смешивать с другим проектом'
        seen=[];events=[]
        def fake_editor(req,strategy,calendar,**kw):
            self.assertEqual(req,request)
            model=kw['model'];seen.append(model)
            v=primary if model=='primary:7b' else backup
            if isinstance(v,Exception):raise v
            return v
        def fake_gate(result,req,review):
            return {'verdict':review(req,'strategy original immutable','calendar original immutable')}
        with patch('generic_text_review.select_independent_model',side_effect=['primary:7b','backup:8b','spare:4b']), \
            patch('production_pipeline.run_compound_with_retry',return_value={'documents':[]}), \
            patch('production_pipeline.final_quality_gate',side_effect=fake_gate), \
            patch('independent_acceptance_editor.call_editor',side_effect=fake_editor), \
            patch('independent_acceptance_editor.call_substance_reader',return_value={'pass':True,'issues':[]}):
            result=run_document_task(request,{'task_id':token},progress=events.append,model='author:8b')
        return result['verdict'],seen,events

    def test_transport_failure_uses_qualified_backup(self):
        verdict,seen,events=self.run_case({'pass':False,'issues':[],
            'review_transport_unavailable':True}, {'pass':True,'issues':[]})
        self.assertTrue(verdict['pass']);self.assertEqual(seen,['primary:7b','backup:8b'])
        self.assertEqual(verdict['independent_reader']['requested_model'],'spare:4b')
        self.assertTrue(any(x.get('stage')=='independent_reviewer_failover' for x in events))

    def test_unavailable_exception_uses_backup(self):
        verdict,seen,_=self.run_case(ConnectionError('review transport offline'),
                                     {'pass':True,'issues':[]})
        self.assertTrue(verdict['pass']);self.assertEqual(seen,['primary:7b','backup:8b'])
        self.assertEqual(verdict['independent_reader']['requested_model'],'spare:4b')

    def test_both_reviewers_unavailable_never_approve(self):
        verdict,seen,_=self.run_case(unavailable_verdict('first reviewer cannot decide'),
                                     unavailable_verdict('second reviewer cannot decide'))
        self.assertFalse(verdict['pass'])
        self.assertTrue(review_unavailable(verdict))
        self.assertEqual(seen,['primary:7b','backup:8b'])

    def test_review_outcome_treats_conflicting_success_as_unavailable(self):
        self.assertTrue(review_unavailable({'pass':True,'issues':[],
            'review_control_unavailable':True}))
        self.assertTrue(review_unavailable({'pass':True,'issues':[],
            'review_state':'partial'}))

    def test_unavailable_host_marker_does_not_grant_repair_authority(self):
        from independent_acceptance_editor import ValidatedEditorialVerdict
        from reviewer_outcomes import quarantine_unverified_findings
        raw=ValidatedEditorialVerdict({'pass':False,'review_state':'unavailable',
            'issues':[{'document':'calendar','type':'quality','reason':'Unconfirmed claim'}]})
        output=quarantine_unverified_findings(raw)
        self.assertNotIsInstance(output,ValidatedEditorialVerdict)
        self.assertFalse(output['pass'])
        self.assertEqual(output['issues'][0]['adjudication'],'unavailable')

    def test_worker_lifecycle_waits_for_actual_finish(self):
        class Worker:
            def __init__(self):self.running=True
            def isRunning(self):return self.running
        worker=Worker()
        class App:
            _background_workers=[worker]
        class Qt:
            def processEvents(self):worker.running=False
        self.assertEqual(active_workers(App()),[worker])
        self.assertTrue(drain_finished_workers(Qt(),App(),timeout_seconds=0.5))
        self.assertEqual(active_workers(App()),[])

    def test_worker_lifecycle_does_not_claim_finished_on_timeout(self):
        class Worker:
            def isRunning(self):return True
        class App:
            _background_workers=[Worker()]
        class Qt:
            def processEvents(self):pass
        self.assertFalse(drain_finished_workers(Qt(),App(),timeout_seconds=0))

if __name__=='__main__':unittest.main()
