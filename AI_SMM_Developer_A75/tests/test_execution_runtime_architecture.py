import sqlite3
import unittest
from execution_runtime import ExecutionRuntime, ExecutionReceipt

class RuntimeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE ai_pending_tasks(project_id TEXT PRIMARY KEY,request TEXT NOT NULL,question TEXT NOT NULL,revision_id TEXT)')
        self.db.executemany('INSERT INTO projects VALUES(?)',[('a',),('b',)])
        self.db.commit()
        self.runtime=ExecutionRuntime(self.db)

    def test_atomic_completion_and_separate_projects(self):
        a=self.runtime.activate('a','request','q');b=self.runtime.activate('b','request','q')
        ar=self.runtime.begin('a',a);br=self.runtime.begin('b',b)
        self.runtime.finish(ar,'request')
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(a,)).fetchone()[0],'completed')
        self.assertEqual(self.db.execute('SELECT state FROM execution_attempts WHERE attempt_id=?',(ar.attempt.attempt_id,)).fetchone()[0],'accepted')
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(b,)).fetchone()[0],'active')
        self.runtime.finish(br,'request')

    def test_late_worker_cannot_close_new_task(self):
        t=self.runtime.activate('a','same','q');old=self.runtime.begin('a',t)
        self.runtime.activate('a','same','q',force_new=True)
        with self.assertRaises(ValueError):self.runtime.finish(old,'same')
        self.assertEqual(self.db.execute("SELECT count(*) FROM ai_task_registry WHERE project_id='a' AND status='active'").fetchone()[0],1)

    def test_rollback_if_ledger_fails(self):
        t=self.runtime.activate('a','one','q');r=self.runtime.begin('a',t)
        self.db.execute("CREATE TRIGGER reject_done BEFORE UPDATE ON execution_attempts WHEN NEW.state='accepted' BEGIN SELECT RAISE(ABORT,'blocked'); END")
        with self.assertRaises(sqlite3.DatabaseError):self.runtime.finish(r,'one')
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(t,)).fetchone()[0],'active')
        self.assertEqual(self.db.execute('SELECT state FROM execution_attempts WHERE attempt_id=?',(r.attempt.attempt_id,)).fetchone()[0],'running')

    def test_cancel_fences_attempt(self):
        t=self.runtime.activate('a','one','q');r=self.runtime.begin('a',t)
        self.runtime.cancel('a','one',task_id=t)
        with self.assertRaises(ValueError):self.runtime.finish(r,'one')
