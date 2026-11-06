"""Architecture-level transactional tests for publication plus task completion."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from execution_runtime import ExecutionRuntime
from result_publication_runtime import ResultPublicationRuntime

class PublicationCompletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = sqlite3.connect(':memory:')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE ai_pending_tasks(project_id TEXT PRIMARY KEY, request TEXT, question TEXT, revision_id TEXT)')
        self.db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, role TEXT, content TEXT, created_at TEXT)')
        self.db.execute('CREATE TABLE project_files(id TEXT PRIMARY KEY, project_id TEXT, kind TEXT, display_name TEXT, path TEXT, created_at TEXT)')
        self.db.executemany('INSERT INTO projects VALUES(?)', [('a',),('b',)])
        self.db.commit()
        self.core = ExecutionRuntime(self.db)
        self.pub = ResultPublicationRuntime(self.db, lambda p: Path(self.tmp.name)/p)
        self.task = self.core.activate('a','request','question')
        self.receipt = self.core.begin('a',self.task)
        self.db.commit()

    def test_completion_and_publication_atomic(self):
        self.pub.publish('a','message','result',receipt=self.receipt,active_project_id='a',completion_request='request')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(self.task,)).fetchone()[0],'completed')
        self.assertEqual(self.db.execute('SELECT state FROM execution_attempts WHERE attempt_id=?',(self.receipt.attempt.attempt_id,)).fetchone()[0],'accepted')

    def test_completion_failure_rolls_back_result(self):
        self.db.execute("CREATE TRIGGER deny BEFORE UPDATE ON execution_attempts WHEN NEW.state='accepted' BEGIN SELECT RAISE(ABORT,'deny'); END")
        self.db.commit()
        with self.assertRaises(sqlite3.DatabaseError):
            self.pub.publish('a','message','result',receipt=self.receipt,active_project_id='a',completion_request='request')
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)
        self.assertFalse(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='result_publication_events'").fetchone())
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(self.task,)).fetchone()[0],'active')

    def test_replay_identical_completion_safe(self):
        first=self.pub.publish('a','message','result',receipt=self.receipt,active_project_id='a',completion_request='request')
        # Replay must not add a second message after commit.
        second=self.pub.publish('a','message','result',receipt=self.receipt,active_project_id='a',completion_request='request')
        self.assertEqual(str(first.record_id),str(second.record_id))
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone()[0],1)

    def test_replaced_task_fenced(self):
        self.core.activate('a','next','question',force_new=True)
        self.db.commit()
        with self.assertRaises(ValueError):
            self.pub.publish('a','message','stale',receipt=self.receipt,active_project_id='a',completion_request='request')
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)

if __name__ == '__main__': unittest.main()
