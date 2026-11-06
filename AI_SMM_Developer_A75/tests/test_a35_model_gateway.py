"""Cross-role Ollama transport and telemetry contract."""
import io
import json
import time
import unittest
from unittest.mock import patch

from ollama_resilience import request_chat, OllamaStageError, request_deadline, model_call_role


class SharedOllamaGatewayTests(unittest.TestCase):
    def test_structured_request_uses_injected_endpoint_and_emits_content_free_timing(self):
        calls=[]; events=[]
        def fake_urlopen(req, timeout):
            calls.append((req.full_url, json.loads(req.data.decode('utf-8')), timeout))
            return io.BytesIO(json.dumps({'message':{'content':'{"pass":true}'}}).encode())
        payload={'model':'qwen3:8b','format':{'type':'object'},
                 'messages':[{'role':'user','content':'private project detail'}]}
        with patch('ollama_resilience.urllib.request.urlopen',side_effect=fake_urlopen):
            result=request_chat(payload,stage='source adjudication batch 1',timeout=17,
                attempts=1,endpoint='http://127.0.0.1:11435/api/chat',
                report=lambda name,**data:events.append((name,data)))
        self.assertEqual(result['message']['content'],'{"pass":true}')
        self.assertEqual(calls[0][0],'http://127.0.0.1:11435/api/chat')
        sent=calls[0][1]
        self.assertEqual(sent['format'],payload['format'])
        self.assertEqual(sent['messages'][-1],payload['messages'][0])
        self.assertIn('JSON Schema',sent['messages'][0]['content'])
        self.assertEqual(payload['messages'],[{'role':'user','content':'private project detail'}])
        self.assertEqual(calls[0][2],17)
        self.assertEqual(len(events),1)
        self.assertEqual(events[0][0],'ollama_call')
        self.assertEqual(events[0][1]['stage_name'],'source adjudication batch 1')
        self.assertEqual(events[0][1]['outcome'],'ok')
        self.assertEqual(events[0][1]['response_contract_mode'],'native_schema')
        self.assertNotIn('private project detail',repr(events))

    def test_transport_failure_preserves_role_and_reports_type_without_prompt(self):
        events=[]
        with patch('ollama_resilience.urllib.request.urlopen',side_effect=TimeoutError('late')):
            with self.assertRaisesRegex(OllamaStageError,'inventory batch 2'):
                request_chat({'messages':[{'content':'private data'}]},stage='inventory batch 2',
                    timeout=1,attempts=1,report=lambda name,**data:events.append((name,data)))
        self.assertEqual(events[0][0],'ollama_call')
        self.assertEqual(events[0][1]['outcome'],'timeout')
        self.assertNotIn('private data',repr(events))

    def test_call_timeout_is_capped_by_case_budget(self):
        calls=[]
        def fake_urlopen(req,timeout):
            calls.append(timeout)
            return io.BytesIO(json.dumps({'message':{'content':'ok'}}).encode())
        with patch('ollama_resilience.urllib.request.urlopen',side_effect=fake_urlopen):
            with request_deadline(8):
                request_chat({'model':'mistral:7b'},stage='bounded case call',
                             timeout=300,attempts=1)
        self.assertEqual(len(calls),1)
        self.assertGreater(calls[0],0)
        self.assertLessEqual(calls[0],8)

    def test_per_call_cap_preserves_time_for_later_pipeline_stages(self):
        calls=[]
        def fake_urlopen(req,timeout):
            calls.append(timeout)
            return io.BytesIO(json.dumps({'message':{'content':'ok'}}).encode())
        with patch('ollama_resilience.urllib.request.urlopen',side_effect=fake_urlopen):
            with request_deadline(360,max_call_seconds=90):
                request_chat({'model':'deepseek-r1:8b'},stage='bounded model call',
                             timeout=300,attempts=1)
        self.assertEqual(len(calls),1)
        self.assertLessEqual(calls[0],90)


    def test_acceptance_timeout_policy_does_not_repeat_expensive_local_inference(self):
        calls=[];events=[]
        def timeout(req,timeout):
            calls.append(timeout)
            raise TimeoutError('model call timed out')
        with patch('ollama_resilience.urllib.request.urlopen',side_effect=timeout):
            with request_deadline(360,max_call_seconds=90,retry_timeouts=False):
                with self.assertRaisesRegex(OllamaStageError,'slow local call'):
                    request_chat({'model':'deepseek-r1:8b'},stage='slow local call',
                                 timeout=300,attempts=2,
                                 report=lambda name,**data:events.append((name,data)))
        self.assertEqual(len(calls),1)
        self.assertEqual(events[0][1]['outcome'],'timeout')
        self.assertEqual(events[0][1]['attempt'],1)

    def test_default_deadline_policy_keeps_existing_timeout_retry_behavior(self):
        calls=[]
        def sometimes_slow(req,timeout):
            calls.append(timeout)
            if len(calls)==1: raise TimeoutError('temporary')
            return io.BytesIO(json.dumps({'message':{'content':'ok'}}).encode())
        with patch('ollama_resilience.urllib.request.urlopen',side_effect=sometimes_slow):
            with request_deadline(360,max_call_seconds=90):
                result=request_chat({'model':'qwen3:8b'},stage='default retry',
                                    timeout=300,attempts=2)
        self.assertEqual(len(calls),2)
        self.assertEqual(result['message']['content'],'ok')


    def test_acceptance_case_stops_all_later_model_calls_after_first_timeout(self):
        calls=[];events=[]
        def timeout(req,timeout):
            calls.append(timeout)
            raise TimeoutError('model call timed out')
        with patch('ollama_resilience.urllib.request.urlopen',side_effect=timeout):
            with request_deadline(360,max_call_seconds=90,retry_timeouts=False,
                                  abort_case_on_timeout=True) as budget:
                with self.assertRaisesRegex(OllamaStageError,'stopped after a model-call timeout'):
                    request_chat({'model':'deepseek-r1:8b'},stage='slow generation',
                                 timeout=300,attempts=2)
                with self.assertRaisesRegex(OllamaStageError,'stopped after a model-call timeout'):
                    request_chat({'model':'deepseek-r1:8b'},stage='later repair',
                                 timeout=300,attempts=2)
        self.assertEqual(len(calls),1)
        self.assertTrue(budget['aborted_on_model_timeout'])
        self.assertFalse(budget['expired'])

    def test_reviewer_timeout_allows_one_random_backup_without_aborting_author_case(self):
        calls=[];events=[]
        def primary_timeout_then_backup(req,timeout):
            calls.append('primary' if len(calls)==0 else 'backup')
            if len(calls)==1:
                raise TimeoutError('primary reviewer timed out')
            return io.BytesIO(json.dumps({'message':{'content':'{"issues":[]}'}}).encode())
        with patch('ollama_resilience.urllib.request.urlopen',side_effect=primary_timeout_then_backup):
            with request_deadline(30,max_call_seconds=10,retry_timeouts=False,
                                  abort_case_on_timeout=True,
                                  reviewer_reserve_seconds=10,
                                  reviewer_backup_reserve_seconds=5) as budget:
                with model_call_role('reviewer'):
                    with self.assertRaises(OllamaStageError):
                        request_chat({'model':'primary:7b'},stage='plan reviewer',attempts=1,
                                     report=lambda name,**data:events.append((name,data)))
                    result=request_chat({'model':'backup:8b'},stage='backup plan reviewer',attempts=1,
                                        report=lambda name,**data:events.append((name,data)))
        self.assertEqual(calls,['primary','backup'])
        self.assertEqual(result['message']['content'],'{"issues":[]}')
        self.assertFalse(budget['aborted_on_model_timeout'])
        self.assertTrue(any(name=='reviewer_model_timeout' for name,_ in events))
        self.assertFalse(any(name=='case_aborted_after_model_timeout' for name,_ in events))

    def test_author_and_primary_reviewer_calls_preserve_bounded_backup_reserve(self):
        timeouts=[]
        def ok(req,timeout):
            timeouts.append(timeout)
            return io.BytesIO(json.dumps({'message':{'content':'ok'}}).encode())
        with patch('ollama_resilience.urllib.request.urlopen',side_effect=ok):
            with request_deadline(20,max_call_seconds=90,retry_timeouts=False,
                                  abort_case_on_timeout=True,
                                  reviewer_reserve_seconds=6,
                                  reviewer_backup_reserve_seconds=4):
                with model_call_role('author'):
                    request_chat({'model':'author:8b'},stage='author',attempts=1)
                with model_call_role('reviewer'):
                    request_chat({'model':'reviewer:8b'},stage='primary reviewer',attempts=1)
                with model_call_role('reviewer_backup'):
                    request_chat({'model':'backup:8b'},stage='backup reviewer',attempts=1)
        self.assertEqual(len(timeouts),3)
        self.assertLessEqual(timeouts[0],10.01)  # preserve six + four seconds
        self.assertLessEqual(timeouts[1],16.01)  # preserve four seconds for backup
        self.assertLessEqual(timeouts[2],20.01)  # backup may consume the remaining case budget

    def test_timeout_abandon_policy_is_opt_in(self):
        calls=[]
        def sometimes_slow(req,timeout):
            calls.append(timeout)
            if len(calls)==1:raise TimeoutError('temporary')
            return io.BytesIO(json.dumps({'message':{'content':'ok'}}).encode())
        with patch('ollama_resilience.urllib.request.urlopen',side_effect=sometimes_slow):
            with request_deadline(360,max_call_seconds=90,retry_timeouts=False):
                with self.assertRaises(OllamaStageError):
                    request_chat({'model':'qwen3:8b'},stage='not case-aborted',
                                 timeout=300,attempts=2)
        self.assertEqual(len(calls),1)

    def test_expired_case_budget_fails_before_another_network_call(self):
        calls=[];events=[]
        with patch('ollama_resilience.urllib.request.urlopen',side_effect=lambda *a,**k:calls.append(1)):
            with request_deadline(0.01) as budget:
                time.sleep(0.02)
                with self.assertRaisesRegex(OllamaStageError,'time budget exceeded'):
                    request_chat({'model':'deepseek-r1:8b'},stage='expired case call',
                                 timeout=300,attempts=2,
                                 report=lambda name,**data:events.append((name,data)))
        self.assertEqual(calls,[])
        self.assertTrue(budget['expired'])
        self.assertEqual(events[0][0],'case_deadline_exceeded')


if __name__=='__main__':
    unittest.main()
