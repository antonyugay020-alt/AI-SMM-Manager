from test_paid_text_support import authorized_call
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import text_providers


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps({
            'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":true}'}}],
            'usage': {'prompt_tokens': 3, 'completion_tokens': 2},
        }).encode('utf-8')


class OpenAIProviderR36Tests(unittest.TestCase):
    def test_explicit_openai_acceptance_opt_in(self):
        base = {
            'OPENAI_API_KEY': 'mock-only',
            'AI_SMM_CLOUD_MODELS': 'openai/gpt-6-luna',
            'AI_SMM_ENABLE_OPENAI_API': '1',
        }
        self.assertEqual(text_providers.configured_cloud_models(base), [])
        base['AI_SMM_ENABLE_OPENAI_ACCEPTANCE'] = '1'
        self.assertEqual(text_providers.configured_cloud_models(base), ['openai/gpt-6-luna'])

    def test_openai_json_transport_is_mocked(self):
        observed = {}

        def fake_urlopen(request, timeout):
            observed['url'] = request.full_url
            observed['body'] = json.loads(request.data)
            observed['authorization'] = request.get_header('Authorization')
            return _FakeResponse()

        with patch('urllib.request.urlopen', fake_urlopen):
            result = authorized_call(
                'openai/gpt-6-luna', [{'role': 'user', 'content': 'mock test'}],
                environ={'OPENAI_API_KEY': 'mock-only'}, response_format='json_object')

        self.assertEqual(observed['url'], 'https://api.openai.com/v1/chat/completions')
        self.assertEqual(observed['body']['response_format'], {'type': 'json_object'})
        self.assertNotIn('temperature', observed['body'])
        self.assertEqual(observed['authorization'], 'Bearer mock-only')
        self.assertEqual(result['message']['content'], '{"ok":true}')
        self.assertEqual(result['provider'], 'openai')


if __name__ == '__main__':
    unittest.main()
