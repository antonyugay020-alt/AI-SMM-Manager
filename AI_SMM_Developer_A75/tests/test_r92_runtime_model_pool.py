"""Explicit provider admission and recorded Windows qualification evidence."""
import json,os,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import generic_text_review as review
import review_qualification as qualification
from qualification_fixture import qualification_reply
from review_arbitration import adjudicate_findings

class RuntimePoolTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{},clear=True);self.env.start();self.addCleanup(self.env.stop)
        self.cache=patch.dict(qualification._CACHE,{},clear=True);self.cache.start();self.addCleanup(self.cache.stop)
        self.local=patch.object(review,'list_installed_ollama_models',return_value=['qwen3:8b','gemma4:cloud']);self.local.start();self.addCleanup(self.local.stop)
        self.scope=review.acceptance_model_pool([])
    def enable(self,**changes):
        values={'AI_SMM_CLOUD_MODELS':'openai/gpt-6-luna','OPENAI_API_KEY':'dummy-not-a-real-key',
                'AI_SMM_ENABLE_OPENAI_API':'1','AI_SMM_ENABLE_OPENAI_ACCEPTANCE':'1'}
        values.update(changes);os.environ.update(values)
    def probe(self,model,wrong=None):
        return qualification.probe_qualification(model,seed=6901,force=True,
            transport=lambda payload,**kw:qualification_reply(payload,wrong_kind=wrong))
    def test_reported_openai_configuration_enters_application_pool_without_free_flag(self):
        self.enable();self.assertEqual(review.list_acceptance_models(),['qwen3:8b','gemma4:cloud','openai/gpt-6-luna'])
        self.assertEqual(review.list_acceptance_models(include_cloud=False),['qwen3:8b','gemma4:cloud'])
    def test_openai_requires_both_permissions_and_key(self):
        for api,acceptance,key in [('0','1','dummy'),('1','0','dummy'),('1','1',''),('0','0','')]:
            with self.subTest(api=api,acceptance=acceptance,key=bool(key)):
                self.enable(AI_SMM_ENABLE_OPENAI_API=api,AI_SMM_ENABLE_OPENAI_ACCEPTANCE=acceptance,OPENAI_API_KEY=key)
                self.assertEqual(review.list_acceptance_models(),['qwen3:8b','gemma4:cloud'])
    def test_free_provider_needs_global_permission_ack_and_key(self):
        env={'AI_SMM_CLOUD_MODELS':'groq/qwen/qwen3.8-27b','GROQ_API_KEY':'dummy',
             'AI_SMM_GROQ_FREE_TIER_ACK':'1','AI_SMM_ENABLE_FREE_CLOUD_ACCEPTANCE':'1'}
        for missing in (None,'GROQ_API_KEY','AI_SMM_GROQ_FREE_TIER_ACK','AI_SMM_ENABLE_FREE_CLOUD_ACCEPTANCE'):
            with patch.dict(os.environ,env,clear=True):
                if missing:os.environ.pop(missing)
                models=review.list_acceptance_models()
                self.assertEqual('groq/qwen/qwen3.8-27b' in models,missing is None)
    def test_api_candidates_survive_offline_local_inventory(self):
        self.enable()
        with patch.object(review,'list_installed_ollama_models',side_effect=OSError('Ollama offline')):
            self.assertEqual(review.list_acceptance_models(),['openai/gpt-6-luna'])
            with self.assertRaises(OSError):review.list_acceptance_models(include_cloud=False)
    def test_no_implicit_fallback_without_enabled_provider(self):
        with patch.object(review,'list_installed_ollama_models',side_effect=OSError('Ollama offline')):
            with self.assertRaises(OSError):review.list_acceptance_models()
    def test_scoped_validation_pool_remains_authoritative(self):
        self.enable()
        with review.acceptance_model_pool(['only-approved-judge']):self.assertEqual(review.list_acceptance_models(),['only-approved-judge'])
        self.assertIn('openai/gpt-6-luna',review.list_acceptance_models())
    def test_explicit_child_pool_does_not_expand_to_other_provider_models(self):
        self.enable(AI_SMM_ACCEPTANCE_REVIEWER_MODELS='["only-approved-judge"]')
        self.assertEqual(review.list_acceptance_models(),['only-approved-judge'])
    def test_duplicates_and_cloud_exclusions_are_preserved(self):
        self.enable(AI_SMM_CLOUD_MODELS='openai/gpt-6-luna;openai/gpt-6-luna')
        self.assertEqual(review.list_acceptance_models().count('openai/gpt-6-luna'),1)
        with patch.object(review,'configured_model_pool',return_value={'allowlist':[],'exclude':['openai/gpt-6-luna']}),patch.object(review,'_has_structured_json_contract',return_value=True) as probe:
            self.assertEqual(review.select_independent_model('qwen3:8b'),'gemma4:cloud')
            self.assertEqual([c.args[0] for c in probe.call_args_list],['gemma4:cloud'])
    def test_enabled_provider_is_still_qualified_before_selection(self):
        self.enable()
        with patch.object(review,'_has_structured_json_contract',side_effect=lambda model:model=='openai/gpt-6-luna') as probe:
            self.assertEqual(review.select_independent_model('qwen3:8b'),'openai/gpt-6-luna')
            self.assertIn('openai/gpt-6-luna',[c.args[0] for c in probe.call_args_list])
    def test_transport_and_size_aliases_are_not_independent_reviewers(self):
        from model_roles import model_family,authors_with_review_capacity
        aliases=['gpt-oss:20b-cloud','gpt-oss:120b-cloud','groq/openai/gpt-oss-20b','openrouter/openai/gpt-oss-120b:free']
        self.assertEqual({model_family(n) for n in aliases},{'gpt-oss'})
        allowed,deferred=authors_with_review_capacity(['gpt-oss:20b-cloud'],['groq/openai/gpt-oss-20b','gemma4:cloud'])
        self.assertEqual(allowed,[]);self.assertEqual(deferred[0]['available_reviewer_families'],1)
    def test_distinct_families_remain_available_across_transports(self):
        from model_roles import model_family
        self.assertNotEqual(model_family('openai/gpt-6-luna'),model_family('gemma4:cloud'))
        self.assertEqual(model_family('gemini/gemini-3.1-flash-lite'),model_family('openrouter/google/gemini-3.1-flash-lite:free'))
    def test_pool_diagnostic_contains_routes_but_no_key(self):
        self.enable()
        with patch('diagnostic_report.record_model_event') as event:review.list_acceptance_models()
        event.assert_called_once();self.assertNotIn('dummy-not-a-real-key',repr(event.call_args))
        self.assertIn('openai/gpt-6-luna',repr(event.call_args))
    def test_fresh_and_cached_qualification_emit_complete_safe_summaries(self):
        with patch('diagnostic_report.record_model_event') as event:
            result=self.probe('judge');qualification.probe_qualification('judge')
        events=[c for c in event.call_args_list if c.args and c.args[0]=='reviewer_qualification_result']
        self.assertEqual(len(events),2);self.assertEqual(events[0].kwargs['controls_passed'],6)
        self.assertFalse(events[0].kwargs['cached']);self.assertTrue(events[1].kwargs['cached'])
        self.assertNotIn('raw_reply',repr(events));self.assertTrue(result['pass'])
    def test_native_bundle_error_retains_primary_and_failed_backup_evidence(self):
        good=self.probe('gemma4:cloud');bad=self.probe('gpt-oss:20b-cloud','literal_present')
        failure=qualification.ReviewerQualificationUnavailable('Backup unavailable',[bad])
        from production_pipeline import run_document_task
        with patch.object(review,'select_independent_model',side_effect=['gemma4:cloud',failure]),patch('compound_tasks.execute_compound') as executor:
            with self.assertRaises(qualification.ReviewerQualificationUnavailable) as caught:run_document_task('A new universal task',{},model='qwen3:8b')
        executor.assert_not_called();self.assertEqual({r['model'] for r in caught.exception.results},{good['model'],bad['model']})
        self.assertIn('gemma4:cloud',str(caught.exception));self.assertIn('5/6',str(caught.exception))
    def test_http_failure_reason_is_visible_without_raw_model_reply(self):
        record={'model':'openai/chosen-model','pass':False,'contract_pass':False,'controls':[],
                'error':'API HTTP 404 (model_not_found)','raw_reply':'PRIVATE_RESPONSE'}
        error=qualification.ReviewerQualificationUnavailable('No review capacity',[record])
        self.assertIn('model_not_found',str(error));self.assertNotIn('PRIVATE_RESPONSE',str(error))
    def test_recorded_windows_controls_keep_their_actual_scores(self):
        fixtures=json.loads(Path(__file__).with_name('r92_received_qualification.json').read_text(encoding='utf-8'))
        self.assertEqual(len(fixtures),6);passed=[]
        for case in fixtures:
            data=json.loads(case['request_payload']['messages'][-1]['content']);raw=case['response']['message']['content']
            confirmed,dismissed=adjudicate_findings(lambda *a,**kw:raw,case['model'],data['original_request'],data['artifact_fields'],[r['finding'] for r in data['review_findings']])
            actual={r['field']:r['finding_validation']['decision']=='confirmed' for r in confirmed+dismissed}
            correct=sum(actual.get(f)==v['needs_change'] for f,v in case['expected_controls'].items())
            self.assertEqual(correct,case['correct'])
            if correct==6:passed.append(case['model'])
        self.assertEqual(passed,['gemma4:cloud'])

if __name__=='__main__':unittest.main()
