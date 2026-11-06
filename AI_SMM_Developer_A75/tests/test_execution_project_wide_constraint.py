"""Database-level project fencing, including hostile direct inserts."""
import sqlite3
import unittest
from execution_coordinator import ExecutionCoordinator
from task_lifecycle import TaskLifecycle

class ProjectWorkerConstraintTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE ai_pending_tasks(project_id TEXT PRIMARY KEY, request TEXT NOT NULL, question TEXT NOT NULL, revision_id TEXT)')
        self.db.executemany('INSERT INTO projects(id) VALUES(?)',[('a',),('b',)])
        self.db.commit()
        self.tasks=TaskLifecycle(self.db)
        self.core=ExecutionCoordinator(self.db)

    def test_database_prevents_second_running_attempt_same_project(self):
        task=self.tasks.activate('a','first','q')
        attempt=self.core.begin('a',task)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute('INSERT INTO execution_attempts (attempt_id,project_id,task_id,state,started_at) VALUES (?,?,?,?,?)',
                            ('rogue','a',task,'running','now'))
        self.core.verify(attempt)

    def test_recovery_releases_project_slot_and_other_project_unaffected(self):
        a=self.tasks.activate('a','first','q')
        b=self.tasks.activate('b','second','q')
        old=self.core.begin('a',a)
        other=self.core.begin('b',b)
        self.assertEqual(self.core.recover(project_id='a'),1)
        with self.assertRaises(ValueError):
            self.core.verify(old)
        self.core.verify(other)
        fresh=self.core.begin('a',a)
        self.core.verify(fresh)

    def test_failed_attempt_releases_slot_for_retry(self):
        task=self.tasks.activate('a','first','q')
        old=self.core.begin('a',task)
        self.core.settle(old,success=False)
        new=self.core.begin('a',task)
        self.assertNotEqual(old.attempt_id,new.attempt_id)
        self.core.verify(new)
