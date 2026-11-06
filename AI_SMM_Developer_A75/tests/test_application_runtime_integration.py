"""Application boundary contract: the UI has one core for durable project effects."""
import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path
from application_core import ApplicationRuntime

class UnifiedApplicationRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.db=sqlite3.connect(':memory:'); self.addCleanup(self.db.close)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT,project_id TEXT,role TEXT,content TEXT,created_at TEXT)')
        self.db.execute('CREATE TABLE project_files(id TEXT PRIMARY KEY,project_id TEXT,kind TEXT,display_name TEXT,path TEXT,created_at TEXT)')
        self.db.execute('CREATE TABLE ai_pending_tasks(project_id TEXT PRIMARY KEY,request TEXT,question TEXT,revision_id TEXT)')
        self.db.executemany('INSERT INTO projects VALUES(?)',[('alpha',),('beta',)])
        self.db.commit()
        self.app=ApplicationRuntime(self.db,lambda pid:self.root/pid)

    def test_all_services_share_one_connection(self):
        self.assertIs(self.app.projects.db,self.db)
        self.assertIs(self.app.executions.db,self.db)
        self.assertIs(self.app.publications.db,self.db)
        self.assertIs(self.app.publications.registry.db,self.db)

    def test_fenced_completion_across_application_boundary(self):
        task_id=self.app.executions.activate('alpha','request','question')
        receipt=self.app.executions.begin('alpha',task_id)
        self.db.commit()
        self.app.publish_result('alpha','message','delivered',receipt=receipt,
            active_project_id='alpha',completion_request='request')
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(task_id,)).fetchone(),('completed',))
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone(),(1,))

    def test_foreign_project_rejected_with_no_side_effect(self):
        with self.assertRaises(ValueError):
            self.app.publish_result('alpha','message','foreign',active_project_id='beta')
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone(),(0,))

    def test_busy_transaction_not_committed_or_rolled_back(self):
        self.db.execute('INSERT INTO messages(project_id,role,content) VALUES(?,?,?)',('beta','user','unsaved'))
        with self.assertRaises(RuntimeError):
            self.app.publish_result('alpha','message','new',active_project_id='alpha')
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone(),(1,))

    def test_gui_domain_routes_use_one_application_service(self):
        app=Path(__file__).with_name('app.py')
        tree=ast.parse(app.read_text(encoding='utf-8'))
        studio=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Studio')
        expected={'refresh_projects','add_project','select_project','save_text',
            'vision_done','video_done','ai_done','import_asset_paths','import_reference_path',
            '_recover_committed_document_exports','_finalize_ai_document_effects'}
        methods={n.name:n for n in studio.body if isinstance(n,ast.FunctionDef)}
        self.assertFalse(expected-set(methods))
        for method in expected:
            tokens=[n for n in ast.walk(methods[method]) if isinstance(n,ast.Attribute)]
            self.assertTrue(any(n.attr=='core' for n in tokens),method)

if __name__=='__main__': unittest.main()
