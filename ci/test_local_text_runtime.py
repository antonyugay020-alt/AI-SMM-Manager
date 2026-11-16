"""Loopback-only local GGUF transport contracts; network is mocked here."""
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'AI_SMM_Work_Architecture_A75'))
from local_text_runtime import configured_for,endpoint_for,request_local

ENV={'AI_SMM_LOCAL_LLM_BACKEND':'llama_cpp','AI_SMM_LOCAL_LLM_MODEL':'qwen3:local-gguf',
     'AI_SMM_LOCAL_LLM_URL':'http://127.0.0.1:18080'}
PAYLOAD={'model':'qwen3:local-gguf','messages':[{'role':'user','content':'Local structured task'}],
         'format':{'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok']},
         'options':{'num_predict':256,'temperature':0}}


class LocalTextRuntimeTests(unittest.TestCase):
    def test_explicit_loopback_configuration_only(self):
        self.assertFalse(configured_for(PAYLOAD['model'],environ={}))
        self.assertTrue(configured_for(PAYLOAD['model'],environ=ENV))
        self.assertFalse(configured_for('other-model',environ=ENV))
        for url in ('https://api.openai.com','http://10.0.0.1:8000','http://user:key@localhost:8080',
                    'http://localhost:8080/?api_key=secret','http://localhost:8080/other','file:///tmp/model'):
            with self.assertRaises(ValueError):endpoint_for(url)
        self.assertEqual(endpoint_for('http://[::1]:8080/v1'),'http://[::1]:8080/v1/chat/completions')

    def test_structured_native_response_normalized_without_cloud_credentials(self):
        sent=[]
        def opener(req,timeout):
            sent.append(req)
            return io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'{"ok":true}'}}],
                'usage':{'prompt_tokens':12,'completion_tokens':4}}).encode())
        result=request_local(PAYLOAD,environ=ENV,opener=opener)
        body=json.loads(sent[0].data)
        self.assertEqual(body['response_format']['schema'],PAYLOAD['format'])
        self.assertEqual(body['max_tokens'],256)
        self.assertFalse(body['chat_template_kwargs']['enable_thinking'])
        self.assertIsNone(sent[0].get_header('Authorization'))
        self.assertEqual(result['message']['content'],'{"ok":true}');self.assertEqual(result['eval_count'],4)

    def test_incomplete_empty_or_ambiguous_completion_never_counts_as_success(self):
        for data in ({'choices':[]}, {'choices':[{'finish_reason':'length','message':{'content':'partial'}}]},
                     {'choices':[{'finish_reason':'stop','message':{'content':''}}]}):
            with self.assertRaises(ValueError):request_local(PAYLOAD,environ=ENV,opener=lambda *a,**kw:io.BytesIO(json.dumps(data).encode()))

    def test_unselected_backend_never_opens_network(self):
        opener=Mock()
        with self.assertRaises(RuntimeError):request_local(PAYLOAD,environ={},opener=opener)
        opener.assert_not_called()

    def test_shared_gateway_uses_configured_local_backend_without_ollama_or_paid_api(self):
        from ollama_resilience import request_chat
        payload={**PAYLOAD,'format':'json'}
        with patch.dict('os.environ',ENV),patch('local_text_runtime.request_local',return_value={
                'model':payload['model'],'message':{'content':'{"ok":true}'},'done':True,'done_reason':'stop'}) as local, \
             patch('urllib.request.urlopen') as network,patch('text_providers.call_text') as cloud:
            response=request_chat(payload,stage='local-fixture',attempts=1)
        local.assert_called_once();network.assert_not_called();cloud.assert_not_called()
        self.assertEqual(json.loads(response['message']['content']),{'ok':True})

if __name__=='__main__':unittest.main()
