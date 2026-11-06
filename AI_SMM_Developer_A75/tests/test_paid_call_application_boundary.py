"""Offline contract checks for production paid-call boundaries."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from cost_authorization import PaymentDenied
from durable_cost_gate import DurableCostGate
from paid_provider_boundary import require_priced_paid_call


class PaidCallBoundaryTests(unittest.TestCase):
    def test_missing_verified_quote_never_uses_network(self):
        from text_providers import call_text
        with patch('text_providers.urllib.request.urlopen') as opened:
            with self.assertRaises(PaymentDenied):
                call_text('openai/gpt-6', [{'role':'user','content':'synthetic'}],
                          environ={'OPENAI_API_KEY':'test'})
        opened.assert_not_called()

    def test_cloud_tag_is_not_assumed_local_or_free(self):
        from ollama_resilience import request_chat
        with patch('ollama_resilience.urllib.request.urlopen') as opened:
            with self.assertRaises(PaymentDenied):
                request_chat({'model':'gemma4:cloud',
                              'messages':[{'role':'user','content':'synthetic'}]},
                             stage='cost-gate-test', attempts=1)
        opened.assert_not_called()

    def test_central_dialogue_defaults_to_local(self):
        from central_dialogue_transport import request_central_dialogue
        payload={'model':'openai/gpt-6',
                 'messages':[{'role':'user','content':'synthetic project request'}]}
        with patch.dict(os.environ, {}, clear=True), \
             patch('ollama_resilience.request_chat', return_value={'message':{'content':'local'}}) as local, \
             patch('text_providers.call_text') as paid:
            result=request_central_dialogue(payload,stage='cost-gate-test')
        self.assertEqual(result['message']['content'],'local')
        local.assert_called_once()
        paid.assert_not_called()

    def test_approved_quote_consumes_single_use_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate=DurableCostGate(Path(tmp)/'cost.sqlite')
            token=gate.approve('provider','image_edit','2')
            reservation=require_priced_paid_call(
                'provider','image_edit',cost_gate=gate,
                approval_token=token,verified_max_usd='1.75')
            self.assertTrue(reservation)
            self.assertEqual(gate.usage()['committed_usd'],1.75)
            with self.assertRaises(PaymentDenied):
                require_priced_paid_call(
                    'provider','image_edit',cost_gate=gate,
                    approval_token=token,verified_max_usd='1.75')

    def test_image_worker_does_not_send_unpriced_request(self):
        from app import CloudEditWorker
        with tempfile.TemporaryDirectory() as tmp:
            worker=CloudEditWorker(Path(tmp)/'input.png',None,
                                   Path(tmp)/'output.png','synthetic','not-a-key')
            errors=[]
            worker.failed.connect(errors.append)
            with patch('app.urllib.request.urlopen') as opened:
                worker.run()
            opened.assert_not_called()
            self.assertTrue(errors)
            self.assertFalse((Path(tmp)/'output.png').exists())


if __name__ == '__main__':
    unittest.main()
