import sqlite3
import unittest
from task_lifecycle import TaskLifecycle

class TestTransitions(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE ai_pending_tasks(project_id TEXT PRIMARY KEY,request TEXT NOT NULL,question TEXT NOT NULL,revision_id TEXT)')
        self.db.executemany('INSERT INTO projects VALUES(?)',[('a',),('b',)])
        self.db.commit()
        self.api=TaskLifecycle(self.db)

    def test_two_projects_and_paused_are_retained(self):
        a=self.api.activate('a','one','q')
        b=self.api.activate('b','other','q')
        later=self.api.activate('a','two','q')
        self.assertNotEqual(a,later)
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(a,)).fetchone()[0],'paused')
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(b,)).fetchone()[0],'active')
        self.assertEqual(self.db.execute('SELECT count(*) FROM ai_task_transitions').fetchone()[0],4)

    def test_failure_rolls_back_transition_without_external_commit(self):
        self.db.execute('BEGIN')
        self.db.execute('INSERT INTO projects VALUES(?)',('c',))
        self.db.execute('CREATE TABLE ai_task_transitions(event_id TEXT PRIMARY KEY)')
        with self.assertRaises(sqlite3.OperationalError):
            self.api.activate('a','first','q')
        self.assertTrue(self.db.in_transaction)
        self.assertIsNone(self.db.execute('SELECT 1 FROM ai_pending_tasks WHERE project_id=?',('a',)).fetchone())
        self.assertIsNotNone(self.db.execute('SELECT 1 FROM projects WHERE id=?',('c',)).fetchone())
        self.db.rollback()
        self.assertIsNone(self.db.execute('SELECT 1 FROM projects WHERE id=?',('c',)).fetchone())

    def test_finish_is_id_bound_and_atomic(self):
        old=self.api.activate('a','same','q')
        new=self.api.activate('a','same','q',force_new=True)
        with self.assertRaises(ValueError):
            self.api.finish('a','same',task_id=old)
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(new,)).fetchone()[0],'active')
        self.api.finish('a','same',task_id=new)
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(new,)).fetchone()[0],'completed')
        self.assertIsNone(self.db.execute('SELECT 1 FROM ai_pending_tasks WHERE project_id=?',('a',)).fetchone())

if __name__=='__main__': unittest.main()
