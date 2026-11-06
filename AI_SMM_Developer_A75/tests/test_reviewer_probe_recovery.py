import io
import json
import unittest
from unittest.mock import patch

import windows_acceptance
from qualification_fixture import qualification_reply
from real_workflow_acceptance import (eligible_author_models,
                                      validate_standalone_review_capacity)


class ReviewerProbeRecoveryTests(unittest.TestCase):
    def test_fast_profile_uses_core_regressions_and_full_keeps_complete_suite(self):
        selected=windows_acceptance.unit_test_selection('fast')
        self.assertIsNotNone(selected)
        self.assertGreaterEqual(len(selected),20)
        self.assertTrue({'test_assertion_scope','test_a58_shared_publication_schema',
                         'test_reviewer_probe_recovery','test_a60_typed_assertion_inventory',
                         'test_claim_support',
                         'test_production_pipeline','test_stage75_orchestration'} <= set(selected))
        self.assertIsNone(windows_acceptance.unit_test_selection('full'))
        self.assertIsNone(windows_acceptance.unit_test_selection('unknown'))
        ignore=windows_acceptance._test_source_ignore('fast')
        self.assertIsNotNone(ignore)
        self.assertNotIn('test_assertion_scope.py',ignore('',[
            'test_assertion_scope.py','test_unselected_historical.py','fixture.json']))
        self.assertIn('test_unselected_historical.py',ignore('',[
            'test_assertion_scope.py','test_unselected_historical.py','fixture.json']))

    def test_long_campaign_requires_reviewer_capability_gate(self):
        checks={key:{'status':'PASS'} for key in (
            'ollama_roundtrip','ollama_structured_json','ollama_constrained_schema',
            'ollama_inventory_map_schema','reviewer_models')}
        clean_env={'AI_SMM_ENABLE_OPENAI_API':'','AI_SMM_ENABLE_OPENAI_ACCEPTANCE':'',
                   'AI_SMM_CLOUD_MODELS':''}
        with patch.dict('os.environ',clean_env,clear=False):
            self.assertTrue(windows_acceptance.live_acceptance_ready(checks))
            checks['reviewer_models']={'status':'FAIL'}
            self.assertFalse(windows_acceptance.live_acceptance_ready(checks))

    def test_enabled_openai_requires_successful_preflight(self):
        checks={key:{'status':'PASS'} for key in (
            'ollama_roundtrip','ollama_structured_json','ollama_constrained_schema',
            'ollama_inventory_map_schema','reviewer_models')}
        enabled={'AI_SMM_ENABLE_OPENAI_API':'1','AI_SMM_ENABLE_OPENAI_ACCEPTANCE':'1',
                 'AI_SMM_CLOUD_MODELS':'openai/gpt-6-luna'}
        with patch.dict('os.environ',enabled,clear=False):
            self.assertFalse(windows_acceptance.live_acceptance_ready(checks))
            checks['openai_api_preflight']={'status':'FAIL'}
            self.assertFalse(windows_acceptance.live_acceptance_ready(checks))
            checks['openai_api_preflight']={'status':'PASS'}
            self.assertTrue(windows_acceptance.live_acceptance_ready(checks))

    def test_probe_calibrates_each_family_once_and_excludes_invalid_json_model(self):
        models=['qwen3:8b','llama3.1:8b','deepseek-r1:8b','mistral:7b','gemma3:4b']
        events=[];calls=[]
        def fake_urlopen(request,timeout):
            payload=json.loads(request.data.decode('utf-8'));calls.append((payload,timeout))
            response=({'message':{'content':''},'done_reason':'length'}
                if payload['model']=='deepseek-r1:8b' else qualification_reply(payload))
            return io.BytesIO(json.dumps(response).encode('utf-8'))
        with patch.dict('os.environ',{'AI_SMM_ACCEPTANCE_AUTHOR_MODELS':''},clear=False), \
             patch('generic_text_review.list_installed_ollama_models',return_value=models), \
             patch('review_qualification.secrets.randbits',return_value=6901), \
             patch.object(windows_acceptance.urllib.request,'urlopen',side_effect=fake_urlopen), \
             patch.object(windows_acceptance,'record',side_effect=lambda *a,**k:events.append((a,k))):
            self.assertTrue(windows_acceptance.reviewer_model_probe())
            self.assertEqual(json.loads(__import__('os').environ['AI_SMM_ACCEPTANCE_AUTHOR_MODELS']),
                             ['qwen3:8b','llama3.1:8b','mistral:7b','gemma3:4b'])
        self.assertEqual(len(calls),5)
        self.assertTrue(all(call[0]['options']['num_predict']==1800 for call in calls))
        self.assertTrue(all(timeout==45 for _,timeout in calls))
        pool_events=[details for args,details in events if args and args[0]=='reviewer_models']
        self.assertEqual(pool_events[0]['reviewer_models'],
                         ['qwen3:8b','llama3.1:8b','mistral:7b','gemma3:4b'])
        self.assertEqual(pool_events[0]['minimum_reviewer_pool'],2)
        failures=[args for args,details in events if args and args[0]=='reviewer_capability_deepseek-r1']
        self.assertEqual(len(failures),1)
        self.assertFalse(failures[0][1])

    def test_probe_allows_workflows_only_with_author_plus_four_reviewer_families(self):
        models=['qwen3:8b','llama3.1:8b','deepseek-r1:8b','mistral:7b',
                'gemma3:4b','phi4:14b']
        calls=[]
        def fake_urlopen(request,timeout):
            payload=json.loads(request.data.decode('utf-8'));calls.append(payload['model'])
            return io.BytesIO(json.dumps(qualification_reply(payload)).encode('utf-8'))
        with patch.dict('os.environ',{'AI_SMM_ACCEPTANCE_AUTHOR_MODELS':''},clear=False), \
             patch('generic_text_review.list_installed_ollama_models',return_value=models), \
             patch('review_qualification.secrets.randbits',return_value=6901), \
             patch.object(windows_acceptance.urllib.request,'urlopen',side_effect=fake_urlopen), \
             patch.object(windows_acceptance,'record'):
            self.assertTrue(windows_acceptance.reviewer_model_probe())
        self.assertEqual(calls,models)

    def test_cloud_candidate_failure_does_not_abort_remaining_qualified_models(self):
        from unittest.mock import patch
        events=[]
        cloud=['openai/bad','openai/good']
        def probe(model,force=False):
            if model=='openai/bad': raise RuntimeError('provider unavailable')
            return {'model':model,'pass':True,'author_response_compatible':True}
        with patch.dict('os.environ',{'AI_SMM_ENABLE_OPENAI_ACCEPTANCE':'1'},clear=False), \
             patch('generic_text_review.list_installed_ollama_models',return_value=['qwen3:8b','llama3.1:8b']), \
             patch('review_qualification.probe_qualification',side_effect=probe), \
             patch('text_providers.configured_cloud_models',return_value=cloud), \
             patch.object(windows_acceptance,'record',side_effect=lambda *a,**kw: events.append((a,kw))), \
             patch.dict(windows_acceptance.RESULT['checks'],{'openai_api_preflight':{'status':'PASS'}}):
            self.assertTrue(windows_acceptance.reviewer_model_probe())
        result=[kw for a,kw in events if a and a[0]=='reviewer_models'][-1]
        self.assertIn('openai/good',result['reviewer_models'])
        self.assertNotIn('openai/bad',result['reviewer_models'])
        self.assertTrue(any(d.get('model')=='openai/bad' and 'RuntimeError' in d.get('error','') for d in result['details']))

    def test_cloud_inventory_exception_is_reported_without_abort(self):
        events=[]
        def probe(model,force=False):
            return {'model':model,'pass':True,'author_response_compatible':True}
        with patch.dict('os.environ',{'AI_SMM_ENABLE_OPENAI_ACCEPTANCE':'1'},clear=False), \
             patch('generic_text_review.list_installed_ollama_models',return_value=['qwen3:8b','llama3.1:8b','mistral:7b']), \
             patch('review_qualification.probe_qualification',side_effect=probe), \
             patch('text_providers.configured_cloud_models',side_effect=RuntimeError('bad cloud config')), \
             patch.object(windows_acceptance,'record',side_effect=lambda *a,**kw:events.append((a,kw))), \
             patch.dict(windows_acceptance.RESULT['checks'],{'openai_api_preflight':{'status':'PASS'}}):
            self.assertTrue(windows_acceptance.reviewer_model_probe())
        self.assertTrue(any(a[0]=='reviewer_cloud_inventory' and not a[1] and
            'RuntimeError' in kw['error'] for a,kw in events if a))

    def test_standalone_capacity_is_checked_before_any_generation(self):
        case={'workflow_type':'standalone_post','author_model':'gemma3:4b'}
        four=['gemma3:4b','qwen3:8b','llama3.1:8b','mistral:7b']
        with self.assertRaisesRegex(ValueError,'нужно 2 семейства помимо автора'):
            validate_standalone_review_capacity([case],['gemma3:4b','qwen3:8b'])
        self.assertTrue(validate_standalone_review_capacity([case],four))
        five=four+['gpt-oss:20b-cloud']
        self.assertTrue(validate_standalone_review_capacity([case],five))
        api_case={'workflow_type':'standalone_post','author_model':'openai/test-model'}
        self.assertTrue(validate_standalone_review_capacity([api_case],four))

    def test_author_pool_excludes_failed_json_models_but_keeps_preflighted_api(self):
        installed=['qwen3:8b','deepseek-r1:8b','gemma4:cloud','openai/gpt-6-luna']
        eligible,excluded=eligible_author_models(installed,
            ['qwen3:8b','gemma4:cloud','openai/gpt-6-luna'])
        self.assertEqual(eligible,['qwen3:8b','gemma4:cloud','openai/gpt-6-luna'])
        self.assertEqual(excluded,['deepseek-r1:8b'])
        no_api,excluded=eligible_author_models(installed,['qwen3:8b','gemma4:cloud'])
        self.assertEqual(no_api,['qwen3:8b','gemma4:cloud'])
        self.assertEqual(excluded,['deepseek-r1:8b','openai/gpt-6-luna'])
        fallback,excluded=eligible_author_models(installed,None)
        self.assertEqual(fallback,installed)
        self.assertEqual(excluded,[])


if __name__=='__main__':
    unittest.main()
