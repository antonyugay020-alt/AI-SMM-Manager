"""One runtime graph: all application services share one authority."""
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from application_core import ApplicationRuntime
from execution_runtime import ExecutionRuntime
from result_publication_runtime import ResultPublicationRuntime

class ApplicationServiceGraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db=sqlite3.connect(':memory:'); self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE ai_pending_tasks(project_id TEXT PRIMARY KEY, request TEXT, question TEXT, revision_id TEXT)')
        self.db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, role TEXT, content TEXT, created_at TEXT)')
        self.db.execute('CREATE TABLE project_files(id TEXT PRIMARY KEY, project_id TEXT, kind TEXT, display_name TEXT, path TEXT, created_at TEXT)')
        self.db.execute('INSERT INTO projects VALUES (?)', ('p1',));self.db.commit()
        self.root=Path(self.tmp.name)
    def test_shared_execution_authority(self):
        app=ApplicationRuntime(self.db,lambda project:self.root/project)
        self.assertIs(app.executions,app.publications.execution)
        task=app.executions.activate('p1','request','question')
        receipt=app.executions.begin('p1',task)
        self.db.commit()
        result=app.publish_result('p1','message','completed',receipt=receipt,active_project_id='p1',completion_request='request')
        self.assertEqual(result.project_id,'p1')
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(task,)).fetchone(),('completed',))
    def test_reject_foreign_db_executor(self):
        other=sqlite3.connect(':memory:');self.addCleanup(other.close)
        with self.assertRaises(ValueError):
            ResultPublicationRuntime(self.db,lambda project:self.root/project,execution=ExecutionRuntime(other))
    def test_graph_preserves_unrelated_transaction(self):
        app=ApplicationRuntime(self.db,lambda project:self.root/project)
        self.db.execute("INSERT INTO messages(project_id,role,content) VALUES ('p1','user','pending')")
        with self.assertRaises(RuntimeError):app.publish_result('p1','message','answer')
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
if __name__=='__main__':unittest.main()
