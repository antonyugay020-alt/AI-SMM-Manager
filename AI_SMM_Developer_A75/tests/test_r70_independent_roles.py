"""Four criteria, separate qualified voices and author capacity; no live QA claim."""
import json
import os
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import Mock,patch
import generic_text_review as review
from model_roles import authors_with_review_capacity,allocate_revision_roles,model_family
from random_acceptance_tasks import build_randomized_workflow_cases
import review_qualification as qualification


class QualifiedRoleAllocation(unittest.TestCase):
    def test_two_qualified_families_cover_all_four_criteria_and_rotate(self):
        pool=['cloud_a:cloud','cloud_b:cloud'];previous=None
        for seed in range(100):
            assignments=review.select_reviewer_models('writer',candidates=pool,previous_reviewers=previous,chooser=random.Random(seed))
            self.assertEqual(set(assignments),set(review.CHECKS));self.assertEqual(set(assignments.values()),set(pool))
            source=assignments['factual_grounding']
            self.assertTrue(all(name!=source for key,name in assignments.items() if key!='factual_grounding'))
            self.assertTrue(review._independent_panel(assignments))
            if previous:self.assertTrue(all(assignments[key]!=previous[key] for key in review.CHECKS))
            previous=assignments
    def test_single_family_multiple_tags_or_providers_cannot_create_independence(self):
        for pool in (['only:8b','only:cloud'],['groq/only','openai/only']):
            with self.assertRaises(ValueError):review.select_reviewer_models('writer',candidates=pool)
        roles=dict.fromkeys(review.CHECKS,'only:cloud')
        self.assertFalse(review._independent_panel(roles))
        with self.assertRaises(ValueError):review.validate_model_assignments('writer',roles)
    def test_source_cannot_adjudicate_findings_it_originated(self):
        valid={'factual_grounding':'a','requirements':'b','completeness':'b','format_and_tone':'b'}
        self.assertEqual(review.validate_model_assignments('writer',valid),valid)
        for change in ({'requirements':'a'},{'factual_grounding':'writer'},{'completeness':None}):
            with self.assertRaises(ValueError):review.validate_model_assignments('writer',{**valid,**change})
    def test_four_voices_are_retained_when_available(self):
        roles=review.select_reviewer_models('writer',candidates=['a','b','c','d'],chooser=random.Random(70))
        self.assertEqual(len(set(roles.values())),4)
    def test_original_writer_is_retained_if_consuming_a_judge_would_break_independence(self):
        roles=allocate_revision_roles('writer',['cloud_a:cloud','cloud_b:cloud'],required_reviewers=2)
        self.assertEqual(roles.revision_author,'writer');self.assertEqual(set(roles.reviewer_pool),{'cloud_a:cloud','cloud_b:cloud'})
        self.assertEqual(roles.reason,'original_writer_retained_to_preserve_independent_review_capacity')
    def test_all_criteria_really_run_when_editorial_voice_is_reused(self):
        roles={'requirements':'editor','completeness':'editor','format_and_tone':'editor','factual_grounding':'source'}
        seen=[]
        def dimension(request,draft,context,check,model,**kw):
            seen.append((check,model));return {'pass':True,'issues':[]}
        with patch.object(review,'review_dimension',side_effect=dimension):
            result=review.review_text('Write.','A useful explanation.',{},model='writer',reviewer_models=roles)
        self.assertEqual({check for check,_ in seen},set(review.CHECKS));self.assertEqual(len(seen),4)
        self.assertTrue(result['pass']);self.assertTrue(result['independent_model']);self.assertEqual(result['reviewer_family_count'],2)


class ActualQualificationCapacity(unittest.TestCase):
    def test_actual_received_controls_enable_local_author_and_both_cloud_readers(self):
        data=json.loads(Path(__file__).with_name('r69_qualification_fixture.json').read_text(encoding='utf-8'))
        reviewers=[row['model'] for row in data['results'] if row['pass']]
        authors=[row['model'] for row in data['results'] if row['contract_pass']]
        viable,deferred=authors_with_review_capacity(authors,reviewers)
        self.assertEqual(set(reviewers),{'gpt-oss:20b-cloud','gemma4:cloud'})
        self.assertEqual(viable,['qwen3:8b']);self.assertEqual({row['model'] for row in deferred},set(reviewers))
        for row in deferred:self.assertEqual(row['available_reviewer_families'],1)
    def test_actual_r69_certificates_do_not_authorize_the_new_scope_protocol(self):
        data=json.loads(Path(__file__).with_name('r69_qualification_fixture.json').read_text(encoding='utf-8'))
        passed=[row for row in data['results'] if row['pass']]
        with patch.dict(qualification._CACHE,{},clear=True), \
             patch.dict(os.environ,{qualification.CERT_ENV:json.dumps(qualification.qualification_envelope(passed))},clear=False), \
             patch('review_qualification.time.time',return_value=max(row['checked_at'] for row in passed)+1):
            for row in passed:self.assertIsNone(qualification.cached_qualification(row['model']))
    def test_native_compound_stops_before_generation_without_second_independent_reader(self):
        from production_pipeline import run_document_task
        with patch('generic_text_review.select_independent_model',side_effect=['reader',ValueError('No independent backup')]), \
             patch('production_pipeline.run_compound_with_retry') as execute:
            with self.assertRaises(qualification.ReviewerQualificationUnavailable):run_document_task('Strategy and calendar.',{},model='writer')
        execute.assert_not_called()
    def test_one_author_gets_two_distinct_random_workflows_without_claiming_two_authors(self):
        signatures=set()
        for seed in range(25):
            cases=build_randomized_workflow_cases(seed,['writer'],ensure_workflow_coverage=True)
            self.assertEqual(len(cases),2);self.assertEqual({row['author_model'] for row in cases},{'writer'})
            self.assertEqual({row['workflow_type'] for row in cases},{'standalone_post','compound_strategy_calendar'})
            self.assertNotEqual(cases[0]['request'],cases[1]['request'])
            signatures.add(tuple(row['request'] for row in cases))
        self.assertEqual(len(signatures),25)


class ContractFailureDiagnostics(unittest.TestCase):
    def test_first_real_contract_error_and_reply_survive_single_call_limit(self):
        # Complete IDs but one missing target: the first real field error must survive.
        def wire(payload,**kw):
            user=json.loads(payload['messages'][-1]['content'])
            decisions={row['id']:{'action':'keep_artifact','reason':'An independently checked control rationale.',
                'required_change':'','remedy_scope':'none'} for row in user['review_findings']}
            decisions['A001'].pop('remedy_scope')
            reply=json.dumps({'decisions':decisions})
            return {'message':{'content':reply}}
        sender=Mock(side_effect=wire)
        result=qualification.probe_qualification('bad_contract',seed=70,transport=sender,force=True)
        self.assertFalse(result['pass']);self.assertFalse(result['contract_pass']);self.assertEqual(sender.call_count,1)
        self.assertIn('A001',result['error']);self.assertIn('remedy_scope',result['error'])
        self.assertIn('decisions',json.loads(result['raw_reply']));self.assertTrue(result['contract_errors'])
        self.assertEqual(result['semantic_controls_evaluated'],0);self.assertIsNone(result['semantic_controls_correct'])
        self.assertNotIn('ограничена одним запросом',result['error'])
    def test_child_certificate_excludes_large_raw_replies_and_failed_models(self):
        from qualification_fixture import qualification_reply
        good=qualification.probe_qualification('certificate_judge',seed=6901,transport=lambda p,**kw:qualification_reply(p),force=True)
        good['raw_reply']='diagnostic text'*10000
        failed={**good,'model':'bad','pass':False}
        envelope=qualification.qualification_envelope([good,failed])
        self.assertEqual(set(envelope['results']),{'certificate_judge'})
        self.assertNotIn('raw_reply',envelope['results']['certificate_judge'])
        self.assertLess(len(json.dumps(envelope,ensure_ascii=False)),10000)
        self.assertTrue(qualification._valid_record(envelope['results']['certificate_judge'],'certificate_judge'))
    def test_invalid_arbitrator_packet_does_not_assert_writer_incompatibility(self):
        def wire(payload,**kw):
            data=json.loads(payload['messages'][-1]['content'])
            reply={'decisions':{row['id']:{'action':'keep_artifact','reason':'A concrete control rationale.',
                'required_change':''} for row in data['review_findings']}}
            return {'message':{'content':json.dumps(reply)},'done_reason':'stop'}
        result=qualification.probe_qualification('writer_candidate',seed=70,transport=wire,force=True)
        self.assertFalse(result['contract_pass']);self.assertFalse(result['pass'])
        self.assertTrue(result['author_response_compatible'])
    def test_non_json_or_truncated_control_does_not_admit_writer_candidate(self):
        for content,reason in [('not JSON','stop'),('{"decisions":{}}','length')]:
            result=qualification.probe_qualification('invalid_writer',seed=70,
                transport=lambda *a,**kw:{'message':{'content':content},'done_reason':reason},force=True)
            self.assertFalse(result['author_response_compatible'])
    def test_undamaged_wrong_decision_is_not_misreported_as_contract_failure(self):
        from qualification_fixture import qualification_reply
        result=qualification.probe_qualification('wrong_semantics',seed=6901,transport=lambda p,**kw:
            qualification_reply(p,wrong_kind='literal_present'),force=True)
        self.assertTrue(result['contract_pass']);self.assertFalse(result['pass'])
        self.assertEqual(result['semantic_controls_evaluated'],6);self.assertEqual(result['semantic_controls_correct'],5)
        self.assertFalse(result['contract_errors']);self.assertTrue(result['raw_reply'])


class MixedWorkflowRunner(unittest.TestCase):
    def test_runner_separates_readers_from_authors_and_covers_two_workflows(self):
        import real_workflow_acceptance as acceptance
        from content_requirements import publication_scope
        from compound_tasks import is_compound_request
        from test_windows_acceptance_v4 import TaskRecoveryTests
        calls=[]
        def task(request,context,*,model,**kwargs):
            calls.append((request,model))
            if is_compound_request(request):
                out=TaskRecoveryTests.result(True);count=publication_scope(request).total
                out['content_delivery'].update(checked_posts=count,scope={'weeks':1,'per_week':count,'planned_total':count},
                    independent_editor={'status':'PASS','issues':[]})
                return out
            return {'action':'write','answer':'A useful post and a reader question.',
                'quality_review':{'status':'PASS','pass':True,'independent_model':True,'issues':[],
                    'checks':dict.fromkeys(review.CHECKS,True),'reviewer_models':{
                        'requirements':'cloud_a:cloud','completeness':'cloud_a:cloud','format_and_tone':'cloud_a:cloud',
                        'factual_grounding':'cloud_b:cloud'}}}
        with tempfile.TemporaryDirectory() as home, \
             patch.dict(os.environ,{'AI_SMM_TEST_ROOT':home,'AI_SMM_TEST_PROFILE':'fast','AI_SMM_ACCEPTANCE_SEED':'7001',
                'AI_SMM_ACCEPTANCE_AUTHOR_MODELS':'["writer","cloud_a:cloud","cloud_b:cloud"]',
                'AI_SMM_ACCEPTANCE_REVIEWER_MODELS':'["cloud_a:cloud","cloud_b:cloud"]'},clear=False), \
             patch('generic_text_review.list_installed_ollama_models',return_value=['writer','cloud_a:cloud','cloud_b:cloud']), \
             patch('review_qualification.probe_qualification',side_effect=lambda name:{'model':name,'pass':True}), \
             patch('text_workflow.run_text_task',side_effect=task),patch('builtins.print'):
            exit_code=acceptance.run()
            result=json.loads((Path(home)/'real_workflow_results.json').read_text(encoding='utf-8'))
            self.assertEqual(exit_code,0)
        self.assertEqual(len(calls),2);self.assertEqual({model for _,model in calls},{'writer'})
        self.assertEqual(len({req for req,_ in calls}),2)
        self.assertEqual(result['task_generation']['selected_author_model_count'],1)
        self.assertEqual(result['task_generation']['case_count'],2)
        self.assertFalse(result['task_generation']['coverage_complete'])
        self.assertEqual(result['task_generation']['author_models_tested'],['writer'])
        self.assertEqual({item['model'] for item in result['task_generation']['author_models_deferred_for_review_capacity']},
            {'cloud_a:cloud','cloud_b:cloud'})
        self.assertEqual({case['workflow_type'] for case in result['cases']},{'standalone_post','compound_strategy_calendar'})
        self.assertTrue(all(case['status']=='PASS' for case in result['cases']),result)


if __name__=='__main__':unittest.main()
