"""One SQLite owner for replies, clarifications and unfinished work."""
import sqlite3
import unittest
from task_outcome_runtime import TaskOutcomeRuntime
from sqlite_transaction_core import own

class TaskOutcomeTransactionTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE projects(id TEXT PRIMARY KEY);
            CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT,project_id TEXT,role TEXT,content TEXT,created_at TEXT);
            CREATE TABLE ai_pending_tasks(project_id TEXT PRIMARY KEY, request TEXT, question TEXT, revision_id TEXT);
            INSERT INTO projects VALUES ('one'); INSERT INTO projects VALUES ('two');''')
        self.db.commit()
        self.outcomes=TaskOutcomeRuntime(self.db)

    def test_reply_joined_parent_commit(self):
        with own(self.db):
            receipt=self.outcomes.record_answer_in_transaction('one','answer')
            self.assertEqual(receipt.project_id,'one')
            self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT project_id,content FROM messages').fetchall(),[('one','answer')])

    def test_reply_rollback_does_not_leak(self):
        with self.assertRaisesRegex(RuntimeError,'after response'):
            with own(self.db):
                self.outcomes.record_answer_in_transaction('one','answer')
                raise RuntimeError('after response')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)

    def test_independent_reply_rejects_foreign_transaction(self):
        with own(self.db):
            with self.assertRaises(RuntimeError):self.outcomes.record_answer('one','answer')
            with self.assertRaises(RuntimeError):self.outcomes.park_and_reply('one','request','question','answer')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)

    def test_transaction_reply_is_project_scoped(self):
        with own(self.db):
            with self.assertRaises(ValueError):self.outcomes.record_answer_in_transaction('missing','wrong')
            self.outcomes.record_answer_in_transaction('two','right')
        self.assertEqual(self.db.execute('SELECT project_id FROM messages').fetchall(),[('two',)])

    def test_pending_and_reply_requires_parent_transaction(self):
        with self.assertRaises(RuntimeError):
            self.outcomes.park_and_reply_in_transaction('one','request','question','answer')

if __name__=='__main__':unittest.main()
