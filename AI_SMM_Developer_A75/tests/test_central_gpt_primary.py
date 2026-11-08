import unittest
from unittest.mock import patch
import central_dialogue_transport as c

class CentralGPTPrimaryTests(unittest.TestCase):
    def setUp(self):
        self.mode=patch.dict('os.environ',{'AI_SMM_DIALOGUE_MODE':'gpt'})
        self.mode.start();self.addCleanup(self.mode.stop)

    def test_cloud_is_primary(self):
        with patch('text_providers.call_text', return_value={'message':{'content':'{}'}}) as cloud:
            result=c.request_central_dialogue({'model':'openai/gpt-5','messages':[]},stage='dialogue')
        self.assertEqual(result['message']['content'],'{}')
        cloud.assert_called_once()
    def test_qwen_fallback_on_cloud_failure(self):
        events=[]
        with patch('text_providers.call_text', side_effect=RuntimeError('API unavailable')):
            with patch('ollama_resilience.request_chat', return_value={'message':{'content':'{}'}}) as local:
                c.request_central_dialogue({'model':'openai/gpt-5','messages':[]},stage='dialogue',report=lambda *a,**kw:events.append((a,kw)))
        self.assertEqual(local.call_args.args[0]['model'],'qwen3:8b')
        self.assertEqual(events[0][0][0],'central_coordinator_fallback')
    def test_fallback_can_be_disabled(self):
        with patch.dict('os.environ',{'AI_SMM_CENTRAL_FALLBACK':'0'}):
            with patch('text_providers.call_text',side_effect=RuntimeError('unavailable')):
                with self.assertRaises(RuntimeError):
                    c.request_central_dialogue({'model':'openai/gpt-5','messages':[]},stage='dialogue')
