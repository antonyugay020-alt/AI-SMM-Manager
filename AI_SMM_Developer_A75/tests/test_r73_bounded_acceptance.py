"""Phase scheduling, timeout boundaries and durable reports; no live model calls."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import generic_text_review as review
import independent_acceptance_editor as editor
import windows_acceptance as windows


def packet(ready=True):
    return {'workflow_type':'compound_strategy_calendar','author_model':'writer','request':'Prepare both documents.',
        'response':{'documents':[{'kind':'strategy','content':'A complete strategy.','valid':True},
                                 {'kind':'calendar','content':'A complete calendar.','valid':ready}],
            'content_delivery':{'independent_editor':{'status':'PASS' if ready else 'BLOCKED'},
                                'unfulfilled':[] if ready else ['Calendar incomplete']}}}


class AuditScheduling(unittest.TestCase):
    def write(self,root,name,data):
        (root/(name+'_synthetic_documents.json')).write_text(json.dumps(data),encoding='utf-8')
    def test_blocked_candidates_skip_every_model_call_and_remain_blocked(self):
        with tempfile.TemporaryDirectory() as home:
            root=Path(home);destination=root/'audit.json'
            self.write(root,'one',packet(False))
            self.write(root,'two',{'workflow_type':'standalone_post','response':{'action':'write_blocked','answer':'A rejected draft.'}})
            with patch.object(editor,'call_editor',side_effect=AssertionError('No model work on blocked candidates')), \
                 patch.object(review,'review_text',side_effect=AssertionError('No model work on blocked candidates')):
                out=editor.review_saved_results(root,ready_only=True,checkpoint_path=destination)
            self.assertEqual(out['status'],'BLOCKED');self.assertTrue(out['finished'])
            self.assertEqual([c['status'] for c in out['cases']],['NOT_RUN','NOT_RUN'])
            self.assertTrue(all(out[k]['status']=='NOT_TESTED' for k in ('negative_control','operational_claim_control','explicit_fact_control')))
            self.assertEqual(json.loads(destination.read_text(encoding='utf-8')),out)
    def test_ready_candidate_is_audited_and_skipped_sibling_cannot_become_pass(self):
        standalone={'workflow_type':'standalone_post','author_model':'writer','request':'Write.',
            'response':{'action':'write','answer':'A ready draft.','quality_review':{'pass':True,'issues':[],'review_errors':{}}}}
        with tempfile.TemporaryDirectory() as home:
            root=Path(home);self.write(root,'one',standalone);self.write(root,'two',packet(False))
            with patch.object(review,'review_text',return_value={'pass':True,'issues':[]}) as audit, \
                 patch.object(editor,'call_editor',return_value={'pass':True,'issues':[]}):
                out=editor.review_saved_results(root,ready_only=True)
            self.assertEqual(audit.call_count,2);self.assertNotEqual(out['status'],'PASS')
            self.assertEqual(audit.call_args_list[0].args[1],'A ready draft.')
            self.assertIn('Неизвестная 999',audit.call_args_list[1].args[1])
            self.assertEqual(out['negative_control']['status'],'FAIL')
            self.assertEqual([c['status'] for c in out['cases']],['PASS','NOT_RUN'])
    def test_ready_compound_still_crosses_the_independent_gate(self):
        with tempfile.TemporaryDirectory() as home:
            root=Path(home);self.write(root,'one',packet(True))
            with patch.object(editor,'call_editor',return_value={'pass':False,'issues':[
                    {'quote':'A complete strategy.','reason':'An independent defect remains.'}]}) as audit:
                out=editor.review_saved_results(root,ready_only=True)
            self.assertGreaterEqual(audit.call_count,1);self.assertEqual(out['cases'][0]['status'],'FAIL')
            self.assertNotEqual(out['status'],'PASS')
    def test_inconsistent_or_malformed_readiness_cannot_schedule_as_ready(self):
        values=[None,{}, {'response':{}}, {'task_profile':['bad'],'response':{}},
                {'workflow_type':'unknown','response':{}},packet(False)]
        for data in values:self.assertFalse(editor._ready_for_independent_review(data))
        data=packet();data['response']['documents'][1]['valid']='true'
        self.assertFalse(editor._ready_for_independent_review(data))
        data=packet();data['response']['documents'].append(copy.deepcopy(data['response']['documents'][0]))
        self.assertFalse(editor._ready_for_independent_review(data))
    def test_completed_sibling_and_active_phase_survive_interruption(self):
        class Interrupted(BaseException):pass
        calls=[]
        def audit(*args,**kw):
            calls.append(1)
            if len(calls)==2:raise Interrupted()
            return {'pass':True,'issues':[]}
        with tempfile.TemporaryDirectory() as home:
            root=Path(home);destination=root/'audit.json'
            self.write(root,'one',packet());self.write(root,'two',packet())
            with patch.object(editor,'call_editor',side_effect=audit),self.assertRaises(Interrupted):
                editor.review_saved_results(root,ready_only=True,checkpoint_path=destination)
            saved=json.loads(destination.read_text(encoding='utf-8'))
            self.assertEqual([c['status'] for c in saved['cases']],['PASS','RUNNING'])
            self.assertFalse(saved['finished']);self.assertEqual(saved['active_case'],'two')
            self.assertEqual(saved['active_stage'],'case_started')


class TimeoutReport(unittest.TestCase):
    def test_child_timeout_keeps_durable_partial_json_and_captured_output(self):
        # Deterministically exercise the interruption contract. A real cold
        # Python process may not start within a 10-second timeout under CI
        # contention; the separate no-checkpoint test covers that situation.
        with tempfile.TemporaryDirectory() as home:
            root=Path(home);destination=root/'independent_editorial_results.json'
            partial={'status':'FAIL','finished':False,'active_stage':'case_started','active_case':'two',
                     'cases':[{'case':'one','status':'PASS'},{'case':'two','status':'RUNNING'}]}
            def interrupt(args,**kwargs):
                destination.write_text(json.dumps(partial), encoding='utf-8')
                raise subprocess.TimeoutExpired(args,kwargs.get('timeout',10),output=b'partial-output')
            with patch.object(windows,'OUT',root),patch.object(windows,'RESULT',{'checks':{}}), \
                 patch.object(subprocess,'run',side_effect=interrupt):
                windows.child_run('independent_semantic_acceptance',[sys.executable,'-c','pass'],10,root)
                check=windows.RESULT['checks']['independent_semantic_acceptance']
                self.assertTrue(check['timed_out']);self.assertEqual(check['status'],'FAIL')
            saved=json.loads(destination.read_text(encoding='utf-8'))
            self.assertEqual(saved['cases'],partial['cases']);self.assertEqual(saved['active_case'],'two')
            self.assertEqual(saved['status'],'BLOCKED');self.assertEqual(saved['review_state'],'timed_out')
            self.assertIn('partial-output',(root/'independent_semantic_acceptance.log').read_text(encoding='utf-8'))
    def test_timeout_before_first_checkpoint_still_has_a_diagnostic_report(self):
        with tempfile.TemporaryDirectory() as home:
            root=Path(home)
            with patch.object(windows,'OUT',root),patch.object(windows,'RESULT',{'checks':{}}), \
                 patch.object(subprocess,'run',side_effect=subprocess.TimeoutExpired(['a'],3,output=b'partial',stderr=b'error')):
                windows.child_run('independent_semantic_acceptance',['a'],3,root)
            saved=json.loads((root/'independent_editorial_results.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['active_stage'],'before_first_checkpoint');self.assertFalse(saved['finished'])


class ReusedReviewerTimeout(unittest.TestCase):
    assignments={'requirements':'editor:8b','completeness':'editor:cloud',
                 'factual_grounding':'source:cloud','format_and_tone':'editor:8b'}
    def run_review(self,context,timeout=True):
        seen=[];events=[]
        def dimension(req,draft,ctx,check,model,**kw):
            seen.append(check)
            if check=='requirements':
                if not timeout:raise ValueError('Invalid contract')
                try:raise TimeoutError('timed out')
                except TimeoutError as exc:raise RuntimeError('transport wrapper') from exc
            return {'pass':True,'issues':[]}
        with patch.object(review,'review_dimension',side_effect=dimension):
            out=review.review_text('Write.','An unchanged draft.',context,model='writer',
                                  reviewer_models=self.assignments,report=lambda s,**kw:events.append({'stage':s,**kw}))
        return out,seen,events
    def test_fast_skips_timed_out_family_but_checks_other_family_and_blocks(self):
        out,seen,events=self.run_review({'randomized_acceptance_run':True,'acceptance_profile':'fast'})
        self.assertEqual(seen,['requirements','factual_grounding'])
        self.assertFalse(out['pass']);self.assertEqual(out['completed_dimensions'],['factual_grounding'])
        self.assertEqual(len(out['review_errors']),3)
        self.assertEqual(sum(e['stage']=='text_review_dimension_skipped_after_timeout' for e in events),2)
    def test_interactive_full_and_non_timeout_contract_failures_keep_their_checks(self):
        for context,timeout in [({},True),({'randomized_acceptance_run':True,'acceptance_profile':'full'},True),
                               ({'randomized_acceptance_run':True,'acceptance_profile':'fast'},False)]:
            out,seen,_=self.run_review(context,timeout)
            self.assertEqual(seen,list(review.CHECKS));self.assertFalse(out['pass'])
