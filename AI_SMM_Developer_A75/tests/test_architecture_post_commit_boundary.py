"""A diagnostic side effect cannot retroactively fail a committed AI result."""
import sqlite3
import unittest
from uuid import uuid4

from dialogue_execution import ProjectConnection
from result_application import apply_project_result


class DummyView:
    def __init__(self):
        self.db = sqlite3.connect(':memory:',factory=ProjectConnection)
        self.db.execute('CREATE TABLE writes(project_id TEXT, result TEXT)')
        self.db.execute('CREATE TABLE ai_pending_tasks(project_id TEXT,request TEXT,question TEXT)')
        self.db.commit()
        self.current_project = self.task_project_id = uuid4().hex
        self.task_instruction = 'Задание проекта'
        self.task_execution_claim = None
        self.task_semantic_kind = None
        self.effects_called = []
        self.diagnostics = []
        self.raise_during_body = False
        self.raise_callback = False

    def _apply_task_result_body(self, task):
        self.db.execute('INSERT INTO writes VALUES(?,?)', (self.current_project, task['action']))
        def first():
            self.effects_called.append('first')
            if self.raise_callback:raise OSError('disk full after successful commit')
        def second():self.effects_called.append('second')
        self._result_effects.extend([first,second])
        if self.raise_during_body:raise RuntimeError('failure before commit')

    def record_project_diagnostic(self,*args):self.diagnostics.append(args)
    def load_manager_documents(self):pass


class PostCommitArchitecture(unittest.TestCase):
    def setUp(self):
        self.view=DummyView()
        self.addCleanup(self.view.db.close)
        self.task={'instruction':self.view.task_instruction,'action':'respond'}

    def test_diagnostic_failure_does_not_undo_committed_result(self):
        self.view.raise_callback=True
        apply_project_result(self.view,self.task)
        self.assertEqual(self.view.db.execute('SELECT count(*) FROM writes').fetchone()[0],1)
        self.assertEqual(self.view.effects_called,['first','second'])
        self.assertEqual(len(self.view._result_post_commit_errors),1)
        self.assertEqual(self.view.diagnostics[0][0],'post_commit_effects_failed')
        self.assertFalse(self.view.db.in_transaction)

    def test_regular_effects_unchanged(self):
        apply_project_result(self.view,self.task)
        self.assertEqual(self.view.effects_called,['first','second'])
        self.assertEqual(self.view._result_post_commit_errors,[])

    def test_precommit_failure_rolls_back_and_never_runs_effects(self):
        self.view.raise_during_body=True
        with self.assertRaises(RuntimeError):apply_project_result(self.view,self.task)
        self.assertEqual(self.view.db.execute('SELECT count(*) FROM writes').fetchone()[0],0)
        self.assertEqual(self.view.effects_called,[])

    def test_foreign_transaction_is_not_committed(self):
        self.view.db.execute('INSERT INTO writes VALUES(?,?)', ('external','uncommitted'))
        with self.assertRaisesRegex(ValueError,'предыдущей транзакции'):
            apply_project_result(self.view,self.task)
        self.assertTrue(self.view.db.in_transaction)
        self.view.db.rollback()
        self.assertEqual(self.view.db.execute('SELECT count(*) FROM writes').fetchone()[0],0)

if __name__=='__main__':unittest.main()
