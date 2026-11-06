"""Integration contract: Qt data access is read-only and project-scoped."""
import ast
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from application_bootstrap import install_schema
from application_core import ApplicationRuntime

class ReadBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db=sqlite3.connect(':memory:'); self.addCleanup(self.db.close)
        install_schema(self.db)
        self.ids=('a'*32,'b'*32)
        for i,pid in enumerate(self.ids):
            self.db.execute('INSERT INTO projects(id,name,created_at,category,description) VALUES (?,?,?,?,?)',
                            (pid,'Project '+str(i),str(i),'',''))
            self.db.execute('INSERT INTO manager_documents(id,project_id,kind,title,content,created_at,updated_at) VALUES (?,?,?,?,?,?,?)',
                            ('doc'+str(i),pid,'plans','Title'+str(i),'Body'+str(i),'t','t'))
            self.db.execute('INSERT INTO ai_pending_tasks(project_id,request,question) VALUES (?,?,?)',
                            (pid,'Request'+str(i),'Question'+str(i)))
        self.db.commit()
        self.app=ApplicationRuntime(self.db,lambda pid:Path(self.tmp.name)/pid)

    def test_project_scoping_across_all_read_facets(self):
        a,b=self.ids
        self.assertEqual(self.app.data.project_name(a),'Project 0')
        self.assertEqual(self.app.data.project_name(b),'Project 1')
        self.assertEqual(self.app.data.document(a,'doc0'),('plans','Title0','Body0'))
        self.assertIsNone(self.app.data.document(a,'doc1'))
        self.assertFalse(self.app.data.document_exists(a,'doc1'))
        self.assertEqual(self.app.data.document_title_content(a,'doc0'),('Title0','Body0'))
        self.assertEqual(self.app.data.document_kind_title(b,'doc1'),('plans','Title1'))
        self.assertEqual(self.app.data.latest_document(a),('doc0','Body0','plans','Title0'))
        self.assertEqual(self.app.data.latest_document(b,kind='plans')[0],'doc1')
        self.assertIsNone(self.app.data.latest_document(b,kind='calendar'))
        self.assertEqual(self.app.data.pending_request(a),'Request0')
        self.assertEqual(self.app.data.pending_request(b),'Request1')

    def test_reads_do_not_commit_or_rollback_a_foreign_transaction(self):
        a,b=self.ids
        self.db.execute('UPDATE manager_documents SET content=? WHERE id=?',('Uncommitted','doc0'))
        self.assertEqual(self.app.data.document(a,'doc0')[-1],'Uncommitted')
        self.assertIsNone(self.app.data.document(b,'doc0'))
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(self.app.data.document(a,'doc0')[-1],'Body0')

    def test_invalid_project_rejected(self):
        with self.assertRaises(ValueError):self.app.data.latest_document('unknown')
        with self.assertRaises(ValueError):self.app.data.pending_request('unknown')

    def test_ui_no_longer_owns_database_io_or_rollback(self):
        source=Path(__file__).with_name('app.py').read_text(encoding='utf-8')
        tree=ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
                if isinstance(node.func.value,ast.Attribute) and isinstance(node.func.value.value,ast.Name):
                    self.assertFalse(node.func.value.value.id=='self' and node.func.value.attr=='db'
                                     and node.func.attr in ('execute','commit','rollback'),
                                     f'UI owns database effect at line {node.lineno}')

if __name__=='__main__':unittest.main()
