import unittest
from unittest.mock import patch

from ollama_resilience import request_chat


class CloudModelRoutingTests(unittest.TestCase):
    def test_explicit_cloud_model_routes_through_provider_adapter(self):
        response={'message':{'content':'cloud answer'},'provider':'groq','model':'openai/gpt-oss-20b'}
        events=[]
        with patch('text_providers.call_text',return_value=response) as cloud, \
             patch('ollama_resilience.urllib.request.urlopen') as local:
            result=request_chat({'model':'groq/openai/gpt-oss-20b','messages':[]},stage='test',report=lambda *a,**k:events.append((a,k)))
        cloud.assert_called_once()
        local.assert_not_called()
        self.assertEqual(result['message']['content'],'cloud answer')
        self.assertEqual(events[0][0][0],'cloud_model_call')

    def test_local_model_keeps_ollama_transport(self):
        import json
        from unittest.mock import MagicMock
        response=MagicMock();response.__enter__.return_value=response
        response.__exit__.return_value=False
        response.read.return_value=json.dumps({'message':{'content':'local'}}).encode()
        with patch('ollama_resilience.urllib.request.urlopen',return_value=response):
            result=request_chat({'model':'qwen3:8b','messages':[]},stage='test')
        self.assertEqual(result['message']['content'],'local')


if __name__=='__main__': unittest.main()
