"""Independent acceptance controls cover the actual material route; no live calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import generic_text_review as review
import independent_acceptance_editor as editor
from test_r73_bounded_acceptance import packet


def standalone():
    return {'workflow_type':'standalone_post','author_model':'writer:8b','request':'Write a factual post; no address or price is supplied.',
        'response':{'action':'write','answer':'A useful completed post.',
            'quality_review':{'pass':True,'issues':[],'review_errors':{},'review_state':'complete'}}}


def control_editor(request,strategy,calendar,**kw):
    if 'Неизвестная 999' in strategy:
        return {'pass':False,'issues':[{'type':'invented_fact','adjudication':'unsupported','quote':'Неизвестная 999','reason':'Address not supplied.'}]}
    if 'снимаем мерки' in calendar:
        return {'pass':False,'issues':[{'type':'invented_fact','quote':'снимаем мерки',
            'reason':'Процесс не подтверждён.'}]}
    return {'pass':True,'issues':[]}


class AuditWorkflows(unittest.TestCase):
    def run_audit(self,values,*,negative='reject',destination=False):
        seen=[]
        def audit(request,answer,context,**kw):
            seen.append((request,answer,copy.deepcopy(context),kw))
            if 'Неизвестная 999' not in answer:return {'pass':True,'issues':[],'review_state':'complete'}
            if negative=='raise':raise ValueError('Malformed reviewer reply')
            if negative=='accept':return {'pass':True,'issues':[],'review_state':'complete'}
            out={'pass':False,'issues':[{'type':'invented_fact','adjudication':'unsupported','quote':'Неизвестная 999','reason':'Address not supplied.'}],
                'review_state':'complete'}
            if negative=='source':
                out['issues'][0].pop('type');out['issues'][0]['check']='factual_grounding'
            if negative=='style':out['issues'][0].update(type='quality',adjudication=None)
            if negative=='partial':out.update(review_state='partial',review_errors={'factual_grounding':'unavailable'})
            return out
        with tempfile.TemporaryDirectory() as home:
            root=Path(home)
            for index,v in enumerate(values):
                (root/f'{index}_synthetic_documents.json').write_text(json.dumps(v),encoding='utf-8')
            with patch.object(review,'review_text',side_effect=audit),patch.object(editor,'call_editor',side_effect=control_editor):
                result=editor.review_saved_results(root,ready_only=True,checkpoint_path=root/'report.json' if destination else None)
            if destination:self.assertEqual(json.loads((root/'report.json').read_text(encoding='utf-8')),result)
            self.assertEqual([json.loads((root/f'{index}_synthetic_documents.json').read_text(encoding='utf-8')) for index in range(len(values))],values)
        return result,seen
    def test_two_standalone_candidates_run_control_on_same_route_and_can_pass(self):
        values=[standalone(),standalone()];values[1]['response']['answer']='A different useful completed post.'
        out,seen=self.run_audit(values,destination=True)
        self.assertEqual(out['status'],'PASS');self.assertEqual(len(seen),3)
        self.assertEqual([c['status'] for c in out['cases']],['PASS','PASS'])
        self.assertEqual(out['negative_control']['workflow_type'],'standalone_post')
        self.assertEqual(out['negative_control']['status'],'PASS')
        self.assertEqual(seen[-1][0],values[0]['request']);self.assertTrue(seen[-1][1].startswith(values[0]['response']['answer']))
        self.assertIn('Неизвестная 999',seen[-1][1]);self.assertEqual(seen[-1][3]['model'],'writer:8b')
    def test_received_ready_post_and_blocked_compound_keep_failure_with_control_executed(self):
        out,seen=self.run_audit([standalone(),packet(False)])
        self.assertEqual([c['status'] for c in out['cases']],['PASS','NOT_RUN'])
        self.assertNotEqual(out['status'],'PASS');self.assertEqual(out['negative_control']['status'],'PASS')
        self.assertEqual(len(seen),2)
    def test_blanket_pass_or_partial_control_does_not_certify_acceptance(self):
        for negative in ('accept','partial','raise','style'):
            with self.subTest(negative=negative):
                out,_=self.run_audit([standalone(),standalone()],negative=negative,destination=True)
                self.assertEqual(out['status'],'FAIL');self.assertEqual(out['negative_control']['status'],'FAIL')
                self.assertTrue(out['finished'])
    def test_typed_source_finding_without_legacy_type_is_valid_detection(self):
        out,_=self.run_audit([standalone(),standalone()],negative='source')
        self.assertEqual(out['status'],'PASS');self.assertEqual(out['negative_control']['status'],'PASS')
    def test_blocked_inputs_make_no_calls_and_leave_controls_unrun(self):
        data=standalone();data['response']['action']='write_blocked'
        out,seen=self.run_audit([data,packet(False)])
        self.assertEqual(out['status'],'BLOCKED');self.assertEqual(seen,[])
        self.assertTrue(all(out[k]['status']=='NOT_TESTED' for k in ('negative_control','operational_claim_control','explicit_fact_control')))
    def test_compound_control_route_and_partial_original_behavior_remain(self):
        out,seen=self.run_audit([standalone(),packet(True)])
        self.assertEqual(out['status'],'PASS');self.assertEqual(len(seen),1)
        self.assertEqual(out['negative_control']['workflow_type'],'compound_strategy_calendar')
        self.assertEqual(out['negative_control']['status'],'PASS')
    def test_negative_control_checkpoint_survives_interruption(self):
        class Interrupted(BaseException):pass
        with tempfile.TemporaryDirectory() as home:
            root=Path(home);data=standalone()
            (root/'one_synthetic_documents.json').write_text(json.dumps(data), encoding='utf-8')
            def audit(request,answer,*args,**kw):
                if 'Неизвестная 999' in answer:raise Interrupted()
                return {'pass':True,'issues':[]}
            with patch.object(review,'review_text',side_effect=audit),patch.object(editor,'call_editor',side_effect=control_editor),self.assertRaises(Interrupted):
                editor.review_saved_results(root,ready_only=True,checkpoint_path=root/'report.json')
            out=json.loads((root/'report.json').read_text(encoding='utf-8'))
            self.assertEqual(out['active_stage'],'negative_control');self.assertFalse(out['finished'])
            self.assertEqual(out['cases'][0]['status'],'PASS')
    def test_revision_creators_and_content_permission_preserved_in_both_calls(self):
        data=standalone();data['response']['quality_review']['revision_author_model']='reviser:cloud'
        permission={'mode':'creative','permission_id':'provided'}
        context_calls=[]
        from contextlib import contextmanager
        @contextmanager
        def pool(models):
            context_calls.append(models)
            yield
        with patch.object(review,'acceptance_model_pool',side_effect=pool), \
             patch.object(review,'list_acceptance_models',return_value=['writer:cloud','reviser:8b','judge_one','judge_two']), \
             patch('text_task_contract.bind_creative_permission',return_value=permission):
            out,seen=self.run_audit([data,packet(False)])
        self.assertEqual(context_calls,[['judge_one','judge_two'],['judge_one','judge_two']])
        self.assertEqual([s[3]['model'] for s in seen],['reviser:cloud','reviser:cloud'])
        self.assertTrue(all(s[2]=={'proposed_content_contract':permission} for s in seen))
        self.assertEqual(out['negative_control']['status'],'PASS')
    def test_compound_partial_control_is_not_a_negative_success(self):
        with tempfile.TemporaryDirectory() as home:
            root=Path(home)
            for index in range(2):(root/f'{index}_synthetic_documents.json').write_text(json.dumps(packet(True)), encoding='utf-8')
            def audit(req,st,ca,**kw):
                out=control_editor(req,st,ca,**kw)
                if 'Неизвестная 999' in st:out['review_state']='unavailable'
                return out
            with patch.object(editor,'call_editor',side_effect=audit):out=editor.review_saved_results(root,ready_only=True)
        self.assertEqual(out['negative_control']['status'],'FAIL');self.assertEqual(out['status'],'FAIL')

    def test_controls_reuse_qualified_audit_voice_and_exclude_both_creators(self):
        data=standalone();data['response']['quality_review']['revision_author_model']='reviser:cloud'
        candidates=[(data,'writer:cloud'),(data,'reviser:8b'),(data,'unqualified:cloud'),(data,'admitted:cloud')]
        with patch('review_qualification.cached_qualification',side_effect=lambda m:{'pass':m!='unqualified:cloud'}):
            self.assertEqual(editor._audit_control_model(candidates,live=True),'admitted:cloud')
        self.assertEqual(editor._audit_control_model([],live=False),'test-adapter')
    def test_missing_qualified_control_voice_blocks_without_default_model_call(self):
        from review_qualification import ReviewerQualificationUnavailable
        with patch('review_qualification.cached_qualification',return_value={'pass':False}),self.assertRaises(ReviewerQualificationUnavailable):
            editor._audit_control_model([(standalone(),'unqualified:cloud')],live=True)
        with tempfile.TemporaryDirectory() as home:
            root=Path(home)
            for i in range(2):(root/f'{i}_synthetic_documents.json').write_text(json.dumps(standalone()), encoding='utf-8')
            with patch.object(review,'review_text',return_value={'pass':True,'issues':[],'reviewer_models':{}}), \
                 patch.object(editor,'_call_editor_once',wraps=editor._call_editor_once) as network:
                # Keep live-route identity while preventing any default control traffic.
                with patch.object(editor,'_DEFAULT_CALL_EDITOR_ONCE',editor._call_editor_once):
                    out=editor.review_saved_results(root,ready_only=True)
            network.assert_not_called()
        self.assertEqual(out['status'],'FAIL');self.assertEqual(out['explicit_fact_control']['status'],'FAIL')


if __name__=='__main__':unittest.main()
