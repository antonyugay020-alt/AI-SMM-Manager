"""Admission and scoped repair authority; injected decisions, no live QA claim."""
import copy
import io
import json
import os
import time
import unittest
from unittest.mock import Mock, patch
import generic_text_review as review
import review_qualification as qualification
from review_arbitration import adjudicate_findings, FindingReviewUnavailable
from qualification_fixture import qualification_reply


class QualificationControls(unittest.TestCase):
    def setUp(self):
        self.cache=patch.dict(qualification._CACHE,{},clear=True);self.cache.start();self.addCleanup(self.cache.stop)
        self.env=patch.dict(os.environ,{qualification.CERT_ENV:''},clear=False);self.env.start();self.addCleanup(self.env.stop)
    def test_controls_are_balanced_new_and_oracle_is_not_in_model_fields(self):
        snapshots=set()
        for seed in range(70):
            request,fields,findings,oracle=qualification.build_controls(seed)
            self.assertEqual(len(fields),6);self.assertEqual(len(findings),6)
            self.assertEqual(sum(row['needs_change'] for row in oracle.values()),3)
            self.assertTrue(all(issue['quote']==fields[issue['field']] for issue in findings))
            self.assertNotIn('needs_change',json.dumps({'fields':fields,'findings':findings}))
            self.assertNotIn('source_supported',json.dumps(fields))
            snapshots.add(request)
        self.assertEqual(len(snapshots),70)
    def test_all_known_answers_pass_one_request_and_same_process_cache_is_reused(self):
        wire=Mock(side_effect=lambda payload,**kw:qualification_reply(payload))
        result=qualification.probe_qualification('judge',seed=6901,transport=wire)
        self.assertTrue(result['pass']);self.assertTrue(result['contract_pass'])
        self.assertEqual(result['model_calls'],1);self.assertEqual(wire.call_count,1)
        self.assertEqual(qualification.probe_qualification('judge',transport=wire),result)
        result['controls'][0]['pass']=False
        self.assertTrue(qualification.cached_qualification('judge')['controls'][0]['pass'])
    def test_one_wrong_control_disqualifies_reviewing_without_hiding_author_contract(self):
        for kind in ('scope_pending','scope_local','source_supported','source_changed','literal_present','literal_missing'):
            wire=Mock(side_effect=lambda payload,**kw:qualification_reply(payload,wrong_kind=kind))
            result=qualification.probe_qualification('judge',seed=6901,transport=wire,force=True)
            self.assertFalse(result['pass']);self.assertTrue(result['contract_pass'])
            self.assertEqual(sum(row['pass'] for row in result['controls']),5)
            self.assertEqual(wire.call_count,1)
    def test_always_approve_reject_or_uncertain_cannot_pass_balanced_controls(self):
        for action in ('keep_artifact','correct_artifact','review_unavailable'):
            def wire(payload,**kw):
                data=json.loads(payload['messages'][-1]['content'])
                return {'message':{'content':json.dumps({'decisions':{row['id']:{
                    'action':action,'remedy_target':row['local_target'] if action=='correct_artifact' else 'none',
                    'reason':'A concrete control rationale based on the provided text.',
                    'required_change':'Correct the recorded defect.' if action=='correct_artifact' else ''}
                    for row in data['review_findings']}})}}
            result=qualification.probe_qualification('judge',seed=6901,transport=wire,force=True)
            self.assertTrue(result['contract_pass']);self.assertFalse(result['pass']);self.assertEqual(result['model_calls'],1)
    def test_invalid_json_or_old_echo_is_not_retried_with_more_network_calls(self):
        for raw in ('not JSON','{"pass":true,"issues":[]}','{"decisions":{}}'):
            wire=Mock(return_value={'message':{'content':raw}})
            result=qualification.probe_qualification('bad',seed=6901,transport=wire,force=True)
            self.assertFalse(result['pass']);self.assertFalse(result['contract_pass']);self.assertEqual(wire.call_count,1)
    def test_failed_result_is_cached_and_force_is_explicit(self):
        wire=Mock(side_effect=TimeoutError('offline'))
        result=qualification.probe_qualification('bad',seed=6901,transport=wire)
        self.assertFalse(result['pass']);qualification.probe_qualification('bad',transport=wire)
        self.assertEqual(wire.call_count,1)
        qualification.probe_qualification('bad',seed=6901,transport=wire,force=True)
        self.assertEqual(wire.call_count,2)
    def test_transient_unavailability_can_be_rechecked_after_short_cache_expiry(self):
        calls=[]
        def send(payload,**kw):
            calls.append(payload)
            if len(calls)==1:raise TimeoutError('Temporary outage')
            return qualification_reply(payload)
        failed=qualification.probe_qualification('temporary',seed=6901,transport=send)
        self.assertFalse(failed['pass'])
        with patch('review_qualification.time.time',return_value=failed['checked_at']+121):
            recovered=qualification.probe_qualification('temporary',seed=6901,transport=send)
        self.assertTrue(recovered['pass']);self.assertEqual(len(calls),2)
    def test_invalid_seed_or_model_stops_before_transport(self):
        wire=Mock()
        for seed in (True,-1,2**64,'seed'):
            with self.assertRaises(ValueError):qualification.probe_qualification('judge',seed=seed,transport=wire,force=True)
        for model in (None,{},''):
            with self.assertRaises(ValueError):qualification.probe_qualification(model,seed=1,transport=wire,force=True)
        wire.assert_not_called()
    def test_versioned_current_certificates_transfer_to_child_without_reprobe(self):
        good=qualification.probe_qualification('judge',seed=6901,transport=lambda p,**kw:qualification_reply(p))
        qualification._CACHE.clear()
        with patch.dict(os.environ,{qualification.CERT_ENV:json.dumps(qualification.qualification_envelope([good])),
                'AI_SMM_ACCEPTANCE_REVIEWER_MODELS':'["judge"]'},clear=False):
            self.assertTrue(review._reviewer_pool_is_prevalidated())
            wire=Mock();self.assertTrue(qualification.probe_qualification('judge',transport=wire)['pass']);wire.assert_not_called()
    def test_stale_wrong_model_missing_controls_and_legacy_echo_are_not_certificates(self):
        good=qualification.probe_qualification('judge',seed=6901,transport=lambda p,**kw:qualification_reply(p))
        qualification._CACHE.clear()
        for changes in ({'protocol':'old'},{'checked_at':time.time()-9*3600},{'checked_at':time.time()+3600},
                        {'model':'different'},{'controls':[]},{'contract_pass':False}):
            record={**good,**changes}
            with patch.dict(os.environ,{qualification.CERT_ENV:json.dumps(qualification.qualification_envelope([record])),
                    'AI_SMM_ACCEPTANCE_REVIEWER_MODELS':'["judge"]'},clear=False):
                self.assertFalse(review._reviewer_pool_is_prevalidated())
        with patch.dict(os.environ,{qualification.CERT_ENV:'','AI_SMM_ACCEPTANCE_REVIEWER_MODELS':'["judge"]'},clear=False):
            self.assertFalse(review._reviewer_pool_is_prevalidated())
    def test_real_gateway_transports_the_same_action_and_scope_schema_for_local_and_cloud(self):
        for model in ('local-judge','cloud-judge:cloud'):
            seen=[]
            def wire(request,timeout):
                payload=json.loads(request.data);seen.append(payload)
                # Actual gateway may encode the schema as a tool or prompt.
                reply=qualification_reply({'messages':payload['messages']})
                return io.BytesIO(json.dumps(reply).encode())
            with patch('ollama_resilience.urllib.request.urlopen',side_effect=wire):
                result=qualification.probe_qualification(model,seed=6901,force=True)
            self.assertTrue(result['pass']);self.assertEqual(len(seen),1)
            self.assertEqual(seen[0]['options']['num_predict'],1800)
    def test_runtime_does_not_admit_json_only_reviewer(self):
        with patch('generic_text_review.list_installed_ollama_models',return_value=['author','bad','good']), \
             patch('review_qualification.secrets.randbits',return_value=6901), \
             patch('generic_text_review.request_chat',side_effect=lambda payload,**kw:
                 qualification_reply(payload,wrong_kind='scope_pending') if payload['model']=='bad' else qualification_reply(payload)):
            self.assertEqual(review.select_independent_model('author',chooser=type('Ordered',(),{'shuffle':staticmethod(lambda x:None),'choice':staticmethod(lambda x:x[0])})()),'good')
        self.assertFalse(qualification.cached_qualification('bad')['pass'])


class ScopeTargetContract(unittest.TestCase):
    fields={'draft':'An existing draft.'}
    issue={'field':'draft','quote':fields['draft'],'reason':'A future separate artifact is missing.',
        'review_scope':{'available_artifacts':['draft'],'pending_artifacts':['future']}}
    def test_other_artifact_cannot_authorize_current_draft_rewrite(self):
        seen=[]
        def ask(model,system,user,**kw):
            seen.append(user)
            return {'decisions':{'A001':{'action':'correct_artifact','remedy_target':'future',
                'reason':'A future separate artifact must be created.','required_change':'Create the future artifact.'}}}
        with self.assertRaises(FindingReviewUnavailable):adjudicate_findings(ask,'judge','Two separate artifacts.',self.fields,[self.issue])
        self.assertEqual(len(seen),2);self.assertEqual(seen[0],seen[1])
    def test_same_input_correction_can_dismiss_a_scope_error_without_approving_content(self):
        seen=[]
        def ask(model,system,user,**kw):
            seen.append(user);first=len(seen)==1
            return {'decisions':{'A001':{'action':'correct_artifact' if first else 'keep_artifact',
                'remedy_target':'future','reason':'The future artifact belongs to a later stage, separate from this draft.',
                'required_change':'Create the future artifact.' if first else ''}}}
        confirmed,dismissed=adjudicate_findings(ask,'judge','Two separate artifacts.',self.fields,[self.issue])
        self.assertFalse(confirmed);self.assertEqual(dismissed[0]['finding_validation']['remedy_target'],'future')
        self.assertEqual(seen[0],seen[1])
    def test_contradictory_or_malformed_scope_stops_before_transport(self):
        for scope in ([],{'pending_artifacts':'future'},
                {'available_artifacts':['draft'],'pending_artifacts':['draft']},
                {'available_artifacts':[None]}):
            ask=Mock()
            with self.assertRaises(ValueError):
                adjudicate_findings(ask,'judge','Two artifacts.',self.fields,[{**self.issue,'review_scope':scope}])
            ask.assert_not_called()
    def test_unknown_target_or_inconsistent_uncertainty_never_enters_repair(self):
        for target,action in [('not-issued','correct_artifact'),([], 'correct_artifact'),('draft','review_unavailable')]:
            def ask(*a,**kw):return {'decisions':{'A001':{'action':action,'remedy_target':target,
                'reason':'A concrete control rationale.','required_change':'Correct the defect.' if action=='correct_artifact' else ''}}}
            with self.assertRaises(FindingReviewUnavailable):adjudicate_findings(ask,'judge','Two artifacts.',self.fields,[self.issue])


class FinalTextApplicability(unittest.TestCase):
    original='Unsupported original claim. Useful body.'
    revised='A revised explanation. Ask the reader.'
    assignments={'factual_grounding':'source','requirements':'r1','completeness':'r2','format_and_tone':'r3'}
    def verdict(self,issues):
        return {'status':'PASS' if not issues else 'BLOCKED','pass':not issues,'issues':issues,
            'checks':{check:not any(i.get('check')==check for i in issues) for check in review.CHECKS},
            'review_errors':{},'review_state':'complete','reviewer_models':dict(self.assignments),
            'quality_model_pool':['writer','reviser','source','r1','r2','r3']}
    def test_revised_text_false_allegation_is_checked_and_requires_full_same_byte_review(self):
        source={'check':'factual_grounding','quote':'Unsupported original claim.','reason':'No supporting source.'}
        editorial={'check':'completeness','field':'standalone','quote':self.revised,'reason':'The closing reader question is allegedly missing.'}
        def wire(payload,**kw):
            data=json.loads(payload['messages'][-1]['content'])
            self.assertEqual(data['artifact_fields'],{'standalone':self.revised})
            return {'message':{'content':json.dumps({'decisions':{row['id']:{
                'action':'keep_artifact','remedy_target':'none','required_change':'',
                'reason':'The reader question is already present in the complete revised artifact.'}
                for row in data['review_findings']}})}}
        with patch.object(review,'review_text',side_effect=[self.verdict([source]),self.verdict([editorial]),self.verdict([])]) as reviewer, \
             patch.object(review,'repair_text',return_value=self.revised) as repairer, \
             patch.object(review,'_reviewer_pool_is_prevalidated',return_value=True), \
             patch.object(review,'allocate_revision_roles',return_value=__import__('model_roles').RevisionRoles(
                 'writer','reviser',('source','r1','r2','r3'),'injected_role_assignment')), \
             patch.object(review,'request_chat',side_effect=wire):
            out=review.run_quality_cycle('Write.',{'action':'write','answer':self.original},{},model='writer',reviewer=reviewer,repairer=repairer)
        self.assertEqual(out['action'],'write');self.assertEqual(out['answer'],self.revised)
        self.assertEqual(reviewer.call_count,3);self.assertEqual(reviewer.call_args.args[1],self.revised)
        self.assertTrue(out['quality_review']['full_recheck_after_finding_dismissal'])
    def test_source_rejection_is_preserved_while_false_editorial_finding_is_dismissed(self):
        initial={'check':'factual_grounding','quote':'Unsupported original claim.','reason':'No supporting source.'}
        source={'check':'factual_grounding','quote':self.revised,'reason':'An actual unsupported business claim remains.','severity':'major'}
        editorial={'check':'format_and_tone','field':'standalone','quote':self.revised,'reason':'A nonexistent heading is allegedly repeated.'}
        def wire(payload,**kw):
            data=json.loads(payload['messages'][-1]['content'])
            return {'message':{'content':json.dumps({'decisions':{row['id']:{'action':'keep_artifact',
                'remedy_target':'none','required_change':'','reason':'The alleged repetition is absent from the actual text.'}
                for row in data['review_findings']}})}}
        with patch.object(review,'review_text',side_effect=[self.verdict([initial]),self.verdict([source,editorial])]) as reviewer, \
             patch.object(review,'repair_text',return_value=self.revised) as repairer, \
             patch.object(review,'_reviewer_pool_is_prevalidated',return_value=True), \
             patch.object(review,'allocate_revision_roles',return_value=__import__('model_roles').RevisionRoles(
                 'writer','reviser',('source','r1','r2','r3'),'injected_role_assignment')), \
             patch.object(review,'request_chat',side_effect=wire):
            out=review.run_quality_cycle('Write.',{'action':'write','answer':self.original},{},model='writer',reviewer=reviewer,repairer=repairer)
        self.assertEqual(out['action'],'write_blocked');self.assertEqual(reviewer.call_count,2)
        self.assertEqual(out['quality_review']['issues'],[source]);self.assertTrue(out['quality_review']['checks']['format_and_tone'])


class ProductionAdmission(unittest.TestCase):
    def test_native_standalone_admission_happens_before_planner_and_never_repeats_task_binding(self):
        from text_workflow import run_text_task
        events=[]
        from review_qualification import ReviewerQualificationUnavailable
        with patch('generic_text_review.select_reviewer_models',side_effect=ReviewerQualificationUnavailable('No qualified reviewers')), \
             patch('agent_runtime.plan_task') as planner:
            with self.assertRaises(ReviewerQualificationUnavailable):
                run_text_task('Write a short explanation.',{},model='author',progress=events.append)
        planner.assert_not_called()
        self.assertEqual(sum(e['stage']=='text_task_contract_bound' for e in events),1)
    def test_explicit_native_reviewer_is_qualified_before_document_generation(self):
        from production_pipeline import run_document_task
        from review_qualification import ReviewerQualificationUnavailable
        with patch('review_qualification.probe_qualification',return_value={'pass':False}) as probe, \
             patch('production_pipeline.run_compound_with_retry') as execute:
            with self.assertRaises(ReviewerQualificationUnavailable):
                run_document_task('Prepare strategy and calendar.',{},model='author',review_model='judge')
        probe.assert_called_once_with('judge');execute.assert_not_called()
    def test_aggregate_preserves_qualification_proof_and_scope_without_alias(self):
        issue={'quote':'Exact draft.','reason':'Specific defect.','check':'requirements','adjudication':'unavailable',
            'review_scope':{'pending_artifacts':['future']},'finding_validation':{'action':'review_unavailable',
                'remedy_target':'none','decision':'uncertain'}}
        out=review.validate_verdict({'pass':False,'checks':dict.fromkeys(review.CHECKS,False),
            'issues':[issue],'review_errors':{}},'Exact draft.')
        self.assertEqual(out['issues'][0]['adjudication'],'unavailable')
        self.assertEqual(out['issues'][0]['finding_validation']['remedy_target'],'none')
        out['issues'][0]['finding_validation']['decision']='Changed'
        self.assertEqual(issue['finding_validation']['decision'],'uncertain')
    def test_corrupt_control_certificate_cannot_assert_prevalidation(self):
        with patch.dict(qualification._CACHE,{},clear=True):
            good=qualification.probe_qualification('judge',seed=6901,transport=lambda p,**kw:qualification_reply(p))
            good['controls'][0]['actual']='wrong';qualification._CACHE.clear()
            with patch.dict(os.environ,{qualification.CERT_ENV:json.dumps(qualification.qualification_envelope([good])),
                    'AI_SMM_ACCEPTANCE_REVIEWER_MODELS':'["judge"]'},clear=False):
                self.assertFalse(review._reviewer_pool_is_prevalidated())




class ReviewerQuickReport(unittest.TestCase):
    def test_importing_acceptance_does_not_delete_existing_validation_evidence(self):
        import importlib.util
        import tempfile
        from pathlib import Path
        import windows_acceptance
        with tempfile.TemporaryDirectory() as home:
            root=Path(home);module=root/'windows_acceptance.py'
            module.write_bytes(Path(windows_acceptance.__file__).read_bytes())
            out=root/'VALIDATION_RESULTS';out.mkdir();sentinel=out/'existing.json';sentinel.write_text('{"keep":true}')
            spec=importlib.util.spec_from_file_location('read_only_acceptance_import',module)
            loaded=importlib.util.module_from_spec(spec);spec.loader.exec_module(loaded)
            self.assertEqual(sentinel.read_text(),'{"keep":true}')
        with tempfile.TemporaryDirectory() as home:
            root=Path(home);module=root/'windows_acceptance.py';module.write_bytes(Path(windows_acceptance.__file__).read_bytes())
            spec=importlib.util.spec_from_file_location('no_output_acceptance_import',module)
            loaded=importlib.util.module_from_spec(spec);spec.loader.exec_module(loaded)
            self.assertFalse((root/'VALIDATION_RESULTS').exists())
    def test_quick_report_preserves_full_results_and_marks_unrun_content(self):
        import tempfile,zipfile
        from pathlib import Path
        import windows_acceptance as acceptance
        with tempfile.TemporaryDirectory() as home:
            root=Path(home);full=root/'VALIDATION_RESULTS';full.mkdir();sentinel=full/'acceptance.json';sentinel.write_text('existing full report')
            with patch.object(acceptance,'OUT',full/'REVIEWER_CHECK'), \
                 patch.object(acceptance,'RESULT',{'checks':{}}), \
                 patch('release_guard.verify_tree',return_value={'source_fingerprint':'f'*64}), \
                 patch.object(acceptance,'reviewer_model_probe',return_value=True) as probe:
                self.assertEqual(acceptance.reviewer_check_only(),0);probe.assert_called_once()
            self.assertEqual(sentinel.read_text(),'existing full report')
            with zipfile.ZipFile(full/'REVIEWER_CHECK.zip') as z:
                result=json.loads(z.read('REVIEWER_CHECK/reviewer_qualification.json'))
            self.assertEqual(result['status'],'PASS');self.assertEqual(result['content_generation'],'NOT_RUN')
            self.assertEqual(result['unit_tests'],'NOT_RUN');self.assertEqual(result['scope'],'reviewer_qualification_only')
    def test_bad_source_blocks_quick_model_calls(self):
        import tempfile
        from pathlib import Path
        import windows_acceptance as acceptance
        with tempfile.TemporaryDirectory() as home,patch.object(acceptance,'OUT',Path(home)/'REVIEWER_CHECK'), \
             patch.object(acceptance,'RESULT',{'checks':{}}), \
             patch('release_guard.verify_tree',side_effect=ValueError('source mismatch')), \
             patch.object(acceptance,'reviewer_model_probe') as probe:
            self.assertEqual(acceptance.reviewer_check_only(),2)
            probe.assert_not_called()


class PartialFullRecheck(unittest.TestCase):
    def test_unavailable_source_check_after_total_dismissal_preserves_its_real_error(self):
        draft='An exact reader-facing explanation.'
        roles={'requirements':'r1','completeness':'r2','format_and_tone':'r3','factual_grounding':'source'}
        issue={'check':'completeness','field':'standalone','quote':draft,'reason':'A missing conclusion is alleged.'}
        initial={'pass':False,'issues':[issue],'checks':{key:key!='completeness' for key in review.CHECKS},
            'review_errors':{},'reviewer_models':roles,'review_state':'complete'}
        fresh={'pass':False,'issues':[],'checks':{key:key!='factual_grounding' for key in review.CHECKS},
            'review_errors':{'factual_grounding':'Exact source validator outage'},'reviewer_models':roles,
            'review_state':'partial','grounding_audit':{'diagnostic':'preserved actual source error'}}
        def wire(payload,**kw):
            return {'message':{'content':json.dumps({'decisions':{'A001':{
                'action':'keep_artifact','remedy_target':'none','required_change':'',
                'reason':'The full explanation already contains the required conclusion.'}}})}}
        with patch.object(review,'request_chat',side_effect=wire):
            out=review._validate_text_applicability('Write.',draft,{},initial,'writer',full_review=lambda:fresh)
        self.assertFalse(out['pass']);self.assertFalse(out['issues'])
        self.assertEqual(out['review_errors'],{'factual_grounding':'Exact source validator outage'})
        self.assertEqual(out['grounding_audit'],fresh['grounding_audit'])
        self.assertEqual(out['finding_adjudication']['full_review_errors'],fresh['review_errors'])


if __name__=='__main__':unittest.main()
