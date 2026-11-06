"""End-to-end transaction ownership across UI-independent application services."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from sqlite_transaction_core import own, compose
from application_core import ApplicationRuntime
from execution_runtime import ExecutionReceipt


class TransactionArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, role TEXT,content TEXT,created_at TEXT)')
        self.db.execute('CREATE TABLE project_files(id TEXT PRIMARY KEY,project_id TEXT,kind TEXT,display_name TEXT,path TEXT,created_at TEXT)')
        self.db.execute('CREATE TABLE ai_pending_tasks(project_id TEXT PRIMARY KEY,request TEXT,question TEXT,revision_id TEXT)')
        self.db.executemany('INSERT INTO projects VALUES(?)', [('a',), ('b',)])
        self.db.commit()
        self.runtime = ApplicationRuntime(self.db, lambda p: Path(self.temp.name) / p)

    def test_nested_failure_rolls_back_only_nested_work(self):
        with own(self.db):
            self.db.execute("INSERT INTO messages(project_id,role,content) VALUES('a','user','outer')")
            with self.assertRaisesRegex(ValueError, 'failed'):
                with compose(self.db, 'nested'):
                    self.db.execute("INSERT INTO messages(project_id,role,content) VALUES('b','user','inner')")
                    raise ValueError('failed')
            self.db.execute("INSERT INTO messages(project_id,role,content) VALUES('a','user','after')")
        self.assertEqual(self.db.execute('SELECT project_id,content FROM messages').fetchall(), [('a','outer'), ('a','after')])

    def test_cross_project_publication_does_not_rollback_foreign_transaction(self):
        with own(self.db):
            self.db.execute("INSERT INTO messages(project_id,role,content) VALUES('b','user','uncommitted')")
            with self.assertRaises(RuntimeError):
                self.runtime.publish_result('a','message','invalid',active_project_id='a')
            self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT project_id FROM messages').fetchall(), [('b',)])

    def test_publication_completion_one_commit_and_replay(self):
        ident=self.runtime.executions.activate('a','request','question')
        receipt=self.runtime.executions.begin('a',ident)
        self.db.commit()
        published=self.runtime.publish_result('a','message','finished',receipt=receipt,
            active_project_id='a',completion_request='request')
        replay=self.runtime.publish_result('a','message','finished',receipt=receipt,
            active_project_id='a',completion_request='request')
        self.assertEqual(published.record_id,replay.record_id)
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(ident,)).fetchone(),('completed',))
        self.assertEqual(self.db.execute('SELECT state FROM execution_attempts WHERE attempt_id=?',(receipt.attempt.attempt_id,)).fetchone(),('accepted',))

    def test_rollback_covers_task_attempt_and_result(self):
        ident=self.runtime.executions.activate('a','request','question')
        receipt=self.runtime.executions.begin('a',ident)
        self.db.commit()
        with self.assertRaises(ValueError):
            self.runtime.publish_result('a','message','delivered',receipt=receipt,
                active_project_id='a',completion_request='wrong request')
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(ident,)).fetchone(),('active',))
        self.assertEqual(self.db.execute('SELECT state FROM execution_attempts WHERE attempt_id=?',(receipt.attempt.attempt_id,)).fetchone(),('running',))

    def test_foreign_connection_rejected(self):
        for action in (lambda: own(None), lambda: compose('fake')):
            with self.assertRaises(TypeError):
                with action(): pass

if __name__=='__main__':
    unittest.main()
