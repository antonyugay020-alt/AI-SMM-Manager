"""AI worker result, clarification and generated document share one commit."""
import unittest
import tempfile
from pathlib import Path
from application_bootstrap import open_application_database
from application_core import ApplicationRuntime
from dialogue_execution import result_transaction

class ResultOutcomeSharedTransaction(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.db=open_application_database(self.base/'studio.db',self.base)
        self.addCleanup(self.db.close)
        self.db.executemany('INSERT INTO projects(id,name,created_at) VALUES(?,?,?)',
                            [('a','Project A','now'),('b','Project B','now')])
        self.db.commit()
        self.core=ApplicationRuntime(self.db,lambda p:self.base/p)

    def test_clarification_commits_with_result(self):
        with result_transaction(self.db):
            outcome=self.core.outcomes.park_and_reply_in_transaction(
                'a','unfinished assignment','need input','draft reply')
            self.assertEqual(outcome.state,'pending')
            self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT request FROM ai_pending_tasks WHERE project_id=?',('a',)).fetchone(),('unfinished assignment',))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages WHERE project_id=?',('b',)).fetchone()[0],0)

    def test_rollback_of_result_keeps_no_fake_reply_or_task(self):
        with self.assertRaises(RuntimeError):
            with result_transaction(self.db):
                self.core.outcomes.park_and_reply_in_transaction('a','request','question','reply')
                raise RuntimeError('crash before commit')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ai_pending_tasks').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)

    def test_partial_work_in_one_project_does_not_replace_other(self):
        with result_transaction(self.db):
            first=self.core.outcomes.park_in_transaction('a','long assignment A','continue A')
            other=self.core.outcomes.park_in_transaction('b','long assignment B','continue B')
            self.assertNotEqual(first,other)
        with result_transaction(self.db):
            newer=self.core.outcomes.park_in_transaction('a','another A','continue newer A')
            self.assertNotEqual(newer,first)
        self.assertEqual(set(self.db.execute(
            'SELECT project_id,request FROM ai_pending_tasks')),
            {('a','another A'),('b','long assignment B')})
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',
                            (first,)).fetchone(),('paused',))
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',
                            (other,)).fetchone(),('active',))

    def test_failure_settlement_and_explanation_are_one_commit(self):
        with result_transaction(self.db):
            task_id=self.core.executions.activate('a','original request','working')
            receipt=self.core.executions.begin('a',task_id)
        self.core.record_execution_failure(project_id='a',attempt=receipt.attempt,
            response='Task failed safely')
        self.assertEqual(self.db.execute('SELECT project_id,content FROM messages').fetchall(),
            [('a','Task failed safely')])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages WHERE project_id=?',('b',)).fetchone()[0],0)

    def test_failure_rejects_foreign_origin_without_settling(self):
        with result_transaction(self.db):
            task_id=self.core.executions.activate('a','original request','working')
            receipt=self.core.executions.begin('a',task_id)
        with self.assertRaises(ValueError):
            self.core.record_execution_failure(project_id='b',attempt=receipt.attempt,
                response='wrong project')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)
        self.core.record_execution_failure(project_id='a',attempt=receipt.attempt,response='correct project')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],1)

    def test_generated_document_and_response_rollback_together(self):
        with self.assertRaises(RuntimeError):
            with result_transaction(self.db):
                self.core.stage_generated_document(project_id='a',document_id='cccccccc111',
                    kind='plans',title='Plan',content='Draft',created_at='now')
                self.core.outcomes.record_answer_in_transaction('a','Created a draft')
                raise RuntimeError('publication failed')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM manager_documents').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)

if __name__=='__main__':unittest.main()
