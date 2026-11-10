import io
import json
import unittest
from unittest.mock import patch

from text_providers import (call_text, configured_cloud_models, parse_cloud_model,
                            is_ollama_cloud_tag)


class FakeResponse:
    def __init__(self, body): self.body=body
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self,*args): return json.dumps(self.body).encode()


class TextProviderTests(unittest.TestCase):
    def test_ollama_cloud_tags_are_reported_separately_from_api_cloud(self):
        self.assertTrue(is_ollama_cloud_tag('gemma4:cloud'))
        self.assertTrue(is_ollama_cloud_tag('gpt-oss:20b-cloud'))
        self.assertFalse(is_ollama_cloud_tag('qwen3:8b'))
        self.assertFalse(is_ollama_cloud_tag('openrouter/vendor/model:free'))

    def test_openrouter_accepts_only_pinned_free_model_ids(self):
        self.assertEqual(parse_cloud_model('openrouter/vendor/model:free'),
                         ('openrouter','vendor/model:free'))
        with self.assertRaises(ValueError): parse_cloud_model('openrouter/vendor/model')

    def test_cloud_candidates_are_explicit_and_key_gated(self):
        env={'AI_SMM_CLOUD_MODELS':'groq/qwen/qwen3.8-27b,openrouter/vendor/model:free',
             'GROQ_API_KEY':'g-key','AI_SMM_GROQ_FREE_TIER_ACK':'1'}
        self.assertEqual(configured_cloud_models(env),['groq/qwen/qwen3.8-27b'])
        env['OPENROUTER_API_KEY']='or-key'
        self.assertEqual(configured_cloud_models(env),
                         ['groq/qwen/qwen3.8-27b','openrouter/vendor/model:free'])

    def test_missing_key_fails_before_any_network_call(self):
        with patch('text_providers.urllib.request.urlopen') as opened:
            with self.assertRaisesRegex(RuntimeError,'облачный запрос не отправлен'):
                call_text('groq/openai/gpt-oss-20b',[{'role':'user','content':'hello'}],environ={})
        opened.assert_not_called()

    def test_groq_response_text_and_usage_are_normalized(self):
        body={'choices':[{'finish_reason':'stop','message':{'content':' answer '}}],
              'usage':{'prompt_tokens':12,'completion_tokens':7}}
        with patch('text_providers.urllib.request.urlopen',return_value=FakeResponse(body)) as opened:
            result=call_text('groq/openai/gpt-oss-20b',[{'role':'user','content':'synthetic'}],
                             environ={'GROQ_API_KEY':'secret','AI_SMM_GROQ_FREE_TIER_ACK':'1'})
        request=opened.call_args.args[0]
        self.assertEqual(request.full_url,'https://api.groq.com/openai/v1/chat/completions')
        self.assertNotIn('secret',request.data.decode())
        self.assertEqual(result['message']['content'],'answer')
        self.assertEqual(result['usage']['prompt_tokens'],12)

    def test_openrouter_route_is_pinned_to_free_model(self):
        body={'choices':[{'finish_reason':'stop','message':{'content':'result'}}]}
        with patch('text_providers.urllib.request.urlopen',return_value=FakeResponse(body)) as opened:
            result=call_text('openrouter/vendor/model:free',[],environ={'OPENROUTER_API_KEY':'secret'})
        self.assertEqual(opened.call_args.args[0].full_url,'https://openrouter.ai/api/v1/chat/completions')
        self.assertEqual(result['provider'],'openrouter')

    def test_gemini_free_tier_requires_explicit_ack_and_uses_stateless_interactions(self):
        env={'AI_SMM_CLOUD_MODELS':'gemini/gemini-3.1-flash-lite',
             'GEMINI_API_KEY':'secret'}
        self.assertEqual(configured_cloud_models(env),[])
        env['AI_SMM_GEMINI_FREE_TIER_ACK']='1'
        self.assertEqual(configured_cloud_models(env),['gemini/gemini-3.1-flash-lite'])
        with self.assertRaises(ValueError):
            configured_cloud_models({**env,'AI_SMM_CLOUD_MODELS':'gemini/gemini-3.1-pro'})
        body={'status':'completed','output_text':'result','usage':{'input_tokens':3}}
        with patch('text_providers.urllib.request.urlopen',return_value=FakeResponse(body)) as opened:
            result=call_text('gemini/gemini-3.1-flash-lite',
                [{'role':'system','content':'system rules'}, {'role':'user','content':'synthetic brief'}],
                environ=env)
        request=opened.call_args.args[0]
        payload=json.loads(request.data.decode())
        self.assertEqual(request.full_url,'https://generativelanguage.googleapis.com/v1beta/interactions')
        self.assertFalse(payload['store'])
        self.assertEqual(payload['system_instruction'],'system rules')
        self.assertEqual(result['message']['content'],'result')


if __name__=='__main__': unittest.main()
