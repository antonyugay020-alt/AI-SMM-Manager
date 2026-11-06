"""Real database and UI read contract: no Qt-owned SQL or cross-project leakage."""
from __future__ import annotations
import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path
from application_bootstrap import open_application_database
from application_core import ApplicationRuntime


class ProjectReadModelIntegration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.db=open_application_database(self.root/'state.sqlite',self.root)
        self.addCleanup(self.db.close)
        self.a='a'*32;self.b='b'*32
        for ident in (self.a,self.b):
            self.db.execute('INSERT INTO projects(id,name,created_at,category) VALUES(?,?,?,?)',
                            (ident,ident,'now','portfolio'))
            self.db.execute('INSERT INTO manager_documents(id,project_id,kind,title,content,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                            ('doc'+ident,ident,'plans','Plan '+ident,'content '+ident,'now','now'))
            self.db.execute('INSERT INTO messages(project_id,role,content,created_at) VALUES(?,?,?,?)',
                            (ident,'user','question '+ident,'now'))
            self.db.execute('INSERT INTO project_files(id,project_id,kind,display_name,path,created_at) VALUES(?,?,?,?,?,?)',
                            ('res'+ident,ident,'result','result.png',str(self.root/ident/'processed'/'result.png'),'now'))
            self.db.execute('INSERT INTO assets(id,project_id,original_name,stored_path,sha256,imported_at) VALUES(?,?,?,?,?,?)',
                            ('asset'+ident,ident,'source.png',str(self.root/ident/'originals'/'source.png'),'0'*64,'now'))
        self.db.commit()
        self.core=ApplicationRuntime(self.db,lambda pid:self.root/pid)

    def test_complete_project_workspace_never_reads_neighbor(self):
        for pid,foreign in ((self.a,self.b),(self.b,self.a)):
            view=self.core.data.workspace(pid)
            self.assertEqual(view.project_id,pid)
            self.assertEqual(view.name,pid)
            self.assertEqual(len(view.assets),1)
            self.assertEqual(len(view.result_paths),1)
            self.assertEqual(len(view.messages),1)
            self.assertTrue(all(foreign not in str(value) for value in (
                view.assets,view.result_paths,view.messages)))

    def test_documents_read_by_immutable_project_and_id(self):
        self.assertEqual(len(self.core.data.documents(self.a,kind='plans')),1)
        self.assertEqual(len(self.core.data.documents(self.a,kind='strategy')),0)
        self.assertIsNone(self.core.data.document(self.a,'doc'+self.b))
        self.assertEqual(self.core.data.document(self.a,'doc'+self.a)[2],'content '+self.a)
        self.assertEqual(self.core.data.document_for_content(self.a,'content '+self.b),None)
        self.assertEqual(self.core.data.document_for_content(self.a,'content '+self.a)[1],'Plan '+self.a)

    def test_dashboard_and_destructive_previews_are_project_scoped(self):
        self.assertEqual(self.core.data.counts(self.a),(2,1,self.a))
        self.assertEqual(self.core.data.counts(None),(2,0,None))
        self.assertEqual(dict(self.core.data.deletion_counts(self.a,'project'))['manager_documents'],1)
        self.assertEqual(self.core.data.deletion_counts(self.b,'chat'),(('messages',1),))
        with self.assertRaises(ValueError):self.core.data.deletion_counts(self.a,'unknown')

    def test_unknown_project_fails_closed_without_implicit_selection(self):
        for operation in (lambda:self.core.data.workspace('missing'),
                          lambda:self.core.data.documents('missing'),
                          lambda:self.core.data.document('missing','any'),
                          lambda:self.core.data.counts('missing')):
            with self.assertRaises(ValueError):operation()

    def test_reads_cannot_commit_another_operation(self):
        self.db.execute('UPDATE projects SET name=? WHERE id=?',('pending',self.a))
        before=self.core.data.workspace(self.a)
        self.assertEqual(before.name,'pending')
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(self.core.data.workspace(self.a).name,self.a)

    def test_gui_read_paths_use_application_runtime(self):
        app=Path(__file__).resolve().parents[1]/'app.py'
        studio=next(n for n in ast.parse(app.read_text(encoding='utf-8')).body
                    if isinstance(n,ast.ClassDef) and n.name=='Studio')
        methods={m.name:m for m in studio.body if isinstance(m,ast.FunctionDef)}
        for name in ('update_dashboard','load_manager_documents','open_document','handle_project_control'):
            method=methods[name]
            self.assertTrue(any(isinstance(n,ast.Attribute) and n.attr=='data' for n in ast.walk(method)),name)
        self.assertTrue(any(isinstance(n,ast.Attribute) and n.attr=='prepare_project_switch' for n in ast.walk(methods['select_project'])))

if __name__=='__main__':unittest.main()
