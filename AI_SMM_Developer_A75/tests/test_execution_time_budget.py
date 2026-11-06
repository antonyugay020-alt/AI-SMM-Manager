"""Production GUI task transport budgets are one deadline, fail closed."""
import importlib
import os
import unittest
from unittest.mock import patch


class WorkerBudgetContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module=importlib.import_module('execution_time_budget')

    def test_budgets_are_bounded_and_have_review_capacity(self):
        dialogue=self.module.worker_budget('dialogue')
        content=self.module.worker_budget('content')
        self.assertLess(dialogue['seconds'],1500)
        self.assertLess(content['seconds'],1500)
        self.assertLess(content['reviewer_reserve_seconds']+content['reviewer_backup_reserve_seconds'],content['seconds'])
        self.assertTrue(content['abort_case_on_timeout'])
        self.assertFalse(content['retry_timeouts'])

    def test_environment_override_is_clamped(self):
        with patch.dict(os.environ,{'AI_SMM_CONTENT_BUDGET_SECONDS':'999999'}):
            self.assertLessEqual(self.module.worker_budget('content')['seconds'],1200)
        with patch.dict(os.environ,{'AI_SMM_CONTENT_BUDGET_SECONDS':'NaN'}):
            self.assertEqual(self.module.worker_budget('content')['seconds'],1140)
        with patch.dict(os.environ,{'AI_SMM_DIALOGUE_BUDGET_SECONDS':'garbage'}):
            self.assertEqual(self.module.worker_budget('dialogue')['seconds'],180)

    def test_budget_context_is_set_and_reset(self):
        import ollama_resilience
        self.assertIsNone(ollama_resilience._CASE_DEADLINE.get())
        with self.module.worker_model_budget('content') as scope:
            self.assertIs(ollama_resilience._CASE_DEADLINE.get(),scope)
        self.assertIsNone(ollama_resilience._CASE_DEADLINE.get())

    def test_expired_deadline_prevents_remote_call(self):
        import time
        import ollama_resilience
        from ollama_resilience import OllamaStageError, request_chat
        with self.module.worker_model_budget('content') as state:
            state['deadline']=time.monotonic()-0.5
            with patch('urllib.request.urlopen') as remote:
                with self.assertRaises(OllamaStageError):
                    request_chat({'model':'qwen3:8b','stream':False,
                                  'messages':[{'role':'user','content':'test'}]},
                                 stage='bounded gui worker',attempts=1)
                remote.assert_not_called()

    def test_unknown_stage_fails(self):
        with self.assertRaises(ValueError):
            self.module.worker_budget('free_form')

    def test_production_qt_workers_enforce_same_transport_boundary(self):
        from pathlib import Path
        src=(Path(__file__).resolve().parents[2]/'AI_SMM_Work_Architecture_A75'/'app.py').read_text(encoding='utf-8')
        self.assertIn("worker_model_budget('dialogue')",src)
        self.assertIn("worker_model_budget('content')",src)
        self.assertIn('run_text_task(original,self.context',src)

if __name__=='__main__':
    unittest.main()
