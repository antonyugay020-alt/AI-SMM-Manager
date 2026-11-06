import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import compound_tasks

class RecoveryTests(unittest.TestCase):
    def test_topic_planning_diagnostics_accept_keyword_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            def fake_ask(model, system, user, *, stage='x', report=None):
                if report:
                    report('ollama_call', stage_name=stage, attempt=1, duration_s=0.1, outcome='ok')
                return 'ok'
            def fake_plan(ask, model, original, strategy, brief, weeks, **kwargs):
                ask(model, 'system', 'user')
                raise RuntimeError('expected stop after diagnostics')
            with patch.object(compound_tasks, 'analyze', return_value={'business':'test','channels':'Instagram','channels_basis':'recommendation'}), \
                 patch.object(compound_tasks, '_ask', side_effect=fake_ask), \
                 patch.object(compound_tasks, 'validate_document', return_value=(True,'')), \
                 patch.object(compound_tasks, 'plan_slots', side_effect=fake_plan):
                result = compound_tasks.execute_compound('Создай стратегию и контент-план на 13 недель',
                                                          {'plan_reviewer_model':'independent-reviewer', **({'project_workspace':folder})})
            self.assertIn('expected stop after diagnostics', result['documents'][-1]['reason'])
            log = next((Path(folder)/'drafts').glob('publication_diagnostics_*.jsonl'))
            events = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
            self.assertTrue(any(e.get('stage_name') == 'планирование тем' for e in events))
            self.assertFalse(any('emit() takes no keyword arguments' in str(e) for e in events))

    def test_early_failure_produces_monitor_files(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(compound_tasks, 'analyze', side_effect=TimeoutError('test timeout')):
                result=compound_tasks.execute_compound('Создай стратегию и контент-план на 13 недель',{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':folder})})
            self.assertIn('Не удалось проанализировать',result['error'])
            progress=list((Path(folder)/'drafts').glob('publication_progress_*.json'))
            logs=list((Path(folder)/'drafts').glob('publication_diagnostics_*.jsonl'))
            self.assertEqual(len(progress),1)
            self.assertEqual(len(logs),1)
            self.assertEqual(json.loads(progress[0].read_text(encoding="utf-8"))['completed'],0)
            events=[json.loads(line) for line in logs[0].read_text(encoding="utf-8").splitlines()]
            self.assertEqual([x['stage'] for x in events],['task_started','task_failed'])
            self.assertEqual(events[-1]['step'],'semantic_analysis')

if __name__=='__main__': unittest.main()
