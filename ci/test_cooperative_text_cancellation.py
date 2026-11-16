"""Cancellation at real transport boundaries and persisted attempt fencing."""
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch,Mock
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'AI_SMM_Work_Architecture_A75'))
from cooperative_cancellation import cancellation_scope,TaskCancelled,worker_check
from ollama_resilience import request_chat
from execution_coordinator import Attempt


class CooperativeCancellationTests(unittest.TestCase):
    def test_stop_after_inflight_response_prevents_next_call_and_result(self):
        stopped=threading.Event()
        def check():
            if stopped.is_set():raise TaskCancelled('stopped')
        def http(*args,**kwargs):
            stopped.set()
            return io.BytesIO(json.dumps({'message':{'content':'late result'}}).encode())
        with patch('urllib.request.urlopen',side_effect=http) as network:
            with self.assertRaises(TaskCancelled):
                with cancellation_scope(check):
                    request_chat({'model':'local-fixture','messages':[]},stage='cancel-fixture',attempts=3)
        network.assert_called_once()

    def test_already_cancelled_scope_never_dispatches(self):
        def check():raise TaskCancelled('stopped')
        with patch('urllib.request.urlopen') as network:
            with self.assertRaises(TaskCancelled):
                with cancellation_scope(check):
                    request_chat({'model':'local-fixture','messages':[]},stage='cancel-fixture')
        network.assert_not_called()

    def test_native_task_worker_reads_fenced_attempt_on_own_sqlite_connection(self):
        from app import TaskWorker
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'state.sqlite'
            db=sqlite3.connect(path)
            db.execute('CREATE TABLE execution_attempts(attempt_id TEXT,project_id TEXT,task_id TEXT,state TEXT)')
            db.execute('CREATE TABLE ai_task_registry(task_id TEXT,project_id TEXT,status TEXT)')
            db.execute("INSERT INTO execution_attempts VALUES('attempt','owner','task','running')")
            db.execute("INSERT INTO ai_task_registry VALUES('task','owner','active')");db.commit()
            worker=TaskWorker('Project task',{})
            worker.execution_attempt=Attempt('attempt','owner','task');worker.execution_database_path=str(path)
            check=worker_check(worker);check()
            db.execute("UPDATE execution_attempts SET state='interrupted'");db.commit();db.close()
            with self.assertRaises(TaskCancelled):check()
            successes=[];failures=[]
            worker.done.connect(successes.append);worker.failed.connect(failures.append)
            with patch('text_workflow.run_text_task') as generate:worker.run()
            generate.assert_not_called();self.assertEqual(successes,[]);self.assertEqual(len(failures),1)

    def test_dialogue_routes_all_wording_to_local_without_keyword_classifier(self):
        import central_dialogue_transport as central
        self.assertFalse(hasattr(central,'_unambiguously_casual'))
        for utterance in ('Привет','Создай видео','продолжай','Use the previous document'):
            with patch.dict(os.environ,{'AI_SMM_DIALOGUE_MODE':'hybrid'}), \
                 patch('ollama_resilience.request_chat',return_value={'message':{'content':'{}'}}) as local, \
                 patch('text_providers.call_text') as cloud:
                central.request_central_dialogue({'model':'openai/fixture','messages':[{'role':'user','content':utterance}]},stage='dialogue')
            cloud.assert_not_called();self.assertEqual(local.call_args.args[0]['model'],'qwen3:8b')

    def test_schema_error_never_switches_provider(self):
        import central_dialogue_transport as central
        with patch.dict(os.environ,{'AI_SMM_DIALOGUE_MODE':'hybrid'}), \
             patch('ollama_resilience.request_chat',side_effect=ValueError('invalid schema')), \
             patch('text_providers.call_text') as cloud:
            with self.assertRaises(ValueError):central.request_central_dialogue({'model':'openai/fixture','messages':[]},stage='dialogue')
        cloud.assert_not_called()

    def test_explicit_cloud_schema_error_never_falls_back_to_another_model(self):
        import central_dialogue_transport as central
        with patch.dict(os.environ,{'AI_SMM_DIALOGUE_MODE':'gpt'}), \
             patch('text_providers.call_text',side_effect=ValueError('malformed JSON')), \
             patch('ollama_resilience.request_chat') as local:
            with self.assertRaises(ValueError):central.request_central_dialogue({'model':'openai/fixture','messages':[]},stage='dialogue')
        local.assert_not_called()

if __name__=='__main__':unittest.main()
