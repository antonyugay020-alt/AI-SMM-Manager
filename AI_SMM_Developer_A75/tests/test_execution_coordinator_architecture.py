import sqlite3
import unittest
from execution_coordinator import ExecutionCoordinator
from task_lifecycle import TaskLifecycle

class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE ai_pending_tasks(project_id TEXT PRIMARY KEY, request TEXT NOT NULL, question TEXT NOT NULL, revision_id TEXT)')
        self.db.executemany('INSERT INTO projects VALUES(?)',[('a',),('b',)])
        self.db.commit()
        self.lifecycle=TaskLifecycle(self.db)
        self.core=ExecutionCoordinator(self.db)

    def test_attempt_fencing_project_isolation_and_late_result(self):
        a=self.lifecycle.activate('a','first','q')
        b=self.lifecycle.activate('b','another','q')
        aa=self.core.begin('a',a)
        bb=self.core.begin('b',b)
        self.core.verify(aa)
        self.core.verify(bb)
        self.lifecycle.activate('a','second','q')
        self.assertEqual(self.db.execute('SELECT state FROM execution_attempts WHERE attempt_id=?',(aa.attempt_id,)).fetchone()[0],'interrupted')
        with self.assertRaises(ValueError):self.core.verify(aa)
        self.core.verify(bb)

    def test_recovery_preserves_tasks_but_invalidates_worker(self):
        t=self.lifecycle.activate('a','one','q')
        old=self.core.begin('a',t)
        self.db.commit()
        self.assertEqual(self.core.recover(),1)
        with self.assertRaises(ValueError):self.core.verify(old)
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(t,)).fetchone()[0],'active')
        fresh=self.core.begin('a',t)
        self.core.verify(fresh)
        self.core.settle(fresh,success=True)
        with self.assertRaises(ValueError):self.core.settle(fresh,success=True)

    def test_second_worker_cannot_claim_same_task(self):
        t=self.lifecycle.activate('a','one','q')
        self.core.begin('a',t)
        with self.assertRaises(ValueError):self.core.begin('a',t)
        self.assertEqual(self.db.execute("SELECT count(*) FROM execution_attempts WHERE state='running'").fetchone()[0],1)

    def test_cancel_active_fences_worker_before_new_execution(self):
        task=self.lifecycle.activate('a','one','q')
        attempt=self.core.begin('a',task)
        self.lifecycle.finish('a','one',task_id=task,status='cancelled')
        with self.assertRaises(ValueError): self.core.verify(attempt)
        self.assertEqual(self.db.execute('SELECT state FROM execution_attempts WHERE attempt_id=?',
                                         (attempt.attempt_id,)).fetchone()[0],'interrupted')
        later=self.lifecycle.activate('a','next','q')
        fresh=self.core.begin('a',later)
        self.core.verify(fresh)

    def test_failed_settle_allows_retry_without_closing_task(self):
        t=self.lifecycle.activate('a','one','q')
        a=self.core.begin('a',t)
        self.core.settle(a,success=False)
        b=self.core.begin('a',t)
        self.assertNotEqual(a.attempt_id,b.attempt_id)
        self.core.verify(b)

if __name__=='__main__':unittest.main()
