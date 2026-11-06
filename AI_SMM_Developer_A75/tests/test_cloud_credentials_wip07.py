"""Regression tests for one RAM-only OpenAI key used across providers."""
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

TEST_PATH = Path(__file__).resolve()
SOURCE_CANDIDATES = (
    TEST_PATH.parents[1],
    TEST_PATH.parents[1] / 'application',
    TEST_PATH.parents[2] / 'AI_SMM_Work_Architecture_A75',
    TEST_PATH.parents[1] / 'source',
)
SOURCE = next((candidate for candidate in SOURCE_CANDIDATES
               if (candidate / 'app.py').is_file()), SOURCE_CANDIDATES[0])
if not (SOURCE / 'app.py').is_file():
    raise RuntimeError('Live source tree is missing; refusing to run a vacuous test')
if str(SOURCE) not in sys.path:
    sys.path.insert(0,str(SOURCE))
from cloud_credentials import (get_openai_key, set_openai_key, clear_openai_key)
import text_providers


class CredentialsTests(unittest.TestCase):
    def tearDown(self):
        clear_openai_key()

    def test_in_memory_key_not_written_to_environment(self):
        before=dict(os.environ)
        set_openai_key('test-key-123')
        self.assertEqual(get_openai_key(),'test-key-123')
        self.assertEqual(dict(os.environ),before)

    def test_existing_environment_value_survives_clear(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY':'environment-key'}):
            set_openai_key('session-key')
            self.assertEqual(get_openai_key(), 'session-key')
            clear_openai_key()
            self.assertEqual(get_openai_key(), 'environment-key')

    def test_explicit_environment_is_not_overridden(self):
        set_openai_key('secret-session-key')
        self.assertEqual(get_openai_key(environ={'OPENAI_API_KEY':'explicit'}),'explicit')
        self.assertEqual(get_openai_key(environ={}),'')

    def test_invalid_key_fails_closed(self):
        for value in ('','\r','key\nheader'):
            with self.assertRaises(ValueError):set_openai_key(value)
        self.assertEqual(get_openai_key(environ={}), '')

    def test_text_transport_receives_session_key_without_environment_mutation(self):
        from unittest.mock import patch as mockpatch
        seen={}
        set_openai_key('unique-test-key')
        def fake_chat_request(provider, model_id, messages, timeout, api_key, temperature, response_format=None):
            seen.update(provider=provider, key=api_key)
            return 'OK',{}
        with mockpatch.dict(os.environ,{'OPENAI_API_KEY':''}),mockpatch.object(text_providers,'_chat_request',side_effect=fake_chat_request):
            r=text_providers.call_text('openai/test-model',[{'role':'user','content':'hi'}])
            self.assertEqual(os.environ['OPENAI_API_KEY'],'')
        self.assertEqual(seen,{'provider':'openai','key':'unique-test-key'})
        self.assertEqual(r['message']['content'],'OK')

    def test_model_inventory_respects_same_session_key(self):
        set_openai_key('inventory-only-key')
        import text_providers
        with patch.dict(os.environ, {'OPENAI_API_KEY':'', 'AI_SMM_CLOUD_MODELS':'openai/test-model',
                'AI_SMM_ENABLE_OPENAI_API':'1', 'AI_SMM_ENABLE_OPENAI_ACCEPTANCE':'1'}):
            self.assertEqual(text_providers.configured_cloud_models(), ['openai/test-model'])
            # Explicit mappings remain isolated from ambient session state.
            self.assertEqual(text_providers.configured_cloud_models(dict(os.environ)), [])
        clear_openai_key()

    def test_key_not_serialized_into_provider_exception(self):
        set_openai_key('secret-test-key-345')
        with patch.object(text_providers,'_chat_request',side_effect=RuntimeError('provider error')):
            with self.assertRaisesRegex(RuntimeError,'provider error') as exc:
                text_providers.call_text('openai/test-model',[])
        self.assertNotIn('secret-test-key-345',str(exc.exception))

    def test_gui_source_references_single_key_source(self):
        loaded=Path(text_providers.__file__).resolve()
        self.assertIn(loaded.parent, (SOURCE.resolve(), Path(__file__).resolve().parent),
                      'Provider loaded from an unknown source tree')
        self.assertEqual(loaded.read_bytes(), (SOURCE/'text_providers.py').read_bytes(),
                         'Cloud transport tests used code different from the application')
        source=(SOURCE/'app.py').read_text(encoding='utf-8')
        self.assertIn('set_openai_key(key.text().strip())',source)
        self.assertIn('CloudEditWorker(self.source.path,reference,output,prompt,get_openai_key()',source)
        self.assertNotIn('if not self.cloud_key:',source)
        self.assertNotIn('prompt,self.cloud_key',source)

if __name__=='__main__':unittest.main()
