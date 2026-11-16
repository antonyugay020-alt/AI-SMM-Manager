"""Actual GUI method behavior under disk/database failure, extracted without Qt."""
from __future__ import annotations
import ast
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from uuid import UUID
from application_bootstrap import open_application_database

SRC=Path(__file__).resolve().parents[1]/'app.py'

def method(name, env):
    tree=ast.parse(SRC.read_text(encoding='utf-8'))
    functions=[node for cls in tree.body if isinstance(cls,ast.ClassDef)
               for node in cls.body if isinstance(node,ast.FunctionDef) and node.name==name]
    assert len(functions)==1
    space=dict(env)
    exec(compile(ast.fix_missing_locations(ast.Module(body=functions,type_ignores=[])),str(SRC),'exec'),space)
    return space[name]

class UserFiles(unittest.TestCase):
    def setUp(self):
        ctx=tempfile.TemporaryDirectory();self.addCleanup(ctx.cleanup)
        self.root=Path(ctx.name);(self.root/'texts').mkdir()
        self.db=open_application_database(self.root/'manager.sqlite',self.root)
        self.addCleanup(self.db.close)
        self.db.execute('INSERT INTO projects(id,name,created_at) VALUES(?,?,?)',('a'*32,'Test','now'))
        self.db.commit()
        self.warnings=[]
        self.source=self.root/'source.png';self.source.write_bytes(b'content')
        self.view=SimpleNamespace(current_project='a'*32, db=self.db,
           text_editor=SimpleNamespace(toPlainText=lambda:'changed text'),
           statusBar=lambda:SimpleNamespace(showMessage=lambda *a:None))
        from application_core import ApplicationRuntime
        self.view.core=ApplicationRuntime(self.db,lambda pid:self.root)
        self.env={'project_dir':lambda *a:self.root,'Path':Path,'shutil':shutil,'sqlite3':sqlite3,
            'uuid4':lambda:UUID('12345678123412341234123412341234'), 'now':lambda:'now',
            'datetime':__import__('datetime').datetime,
            'QMessageBox':SimpleNamespace(warning=lambda *a:self.warnings.append(a)),
            'QFileDialog':SimpleNamespace(getOpenFileName=lambda *a:(str(self.source),'x'))}

    def test_import_failure_rolls_back_and_preserves_user_source(self):
        self.db.execute("CREATE TRIGGER abort_insert BEFORE INSERT ON project_files BEGIN SELECT RAISE(ABORT,'disk'); END")
        self.assertIsNone(method('add_project_file',self.env)(self.view,'result','processed','Choose'))
        self.assertEqual(self.db.execute('select count(*) from project_files').fetchone()[0],0)
        self.assertEqual(list((self.root/'processed').iterdir()),[])
        self.assertEqual(self.source.read_bytes(),b'content')

    def test_import_success_verifies_owned_copy_and_row(self):
        path=method('add_project_file',self.env)(self.view,'result','processed','Choose')
        self.assertTrue(Path(path).is_file())
        self.assertEqual(Path(path).read_bytes(),b'content')
        self.assertEqual(self.db.execute('select kind from project_files').fetchone(),('result',))

    def test_save_text_sqlite_failure_does_not_leave_orphan(self):
        self.db.execute("CREATE TRIGGER abort_insert BEFORE INSERT ON project_files BEGIN SELECT RAISE(ABORT,'disk'); END")
        self.assertFalse(method('save_text',self.env)(self.view))
        self.assertEqual(list((self.root/'texts').iterdir()),[])
        self.assertEqual(self.db.execute('select count(*) from project_files').fetchone()[0],0)

    def test_save_text_success_updates_file_and_sqlite(self):
        self.assertTrue(method('save_text',self.env)(self.view))
        rows=self.db.execute('select path from project_files').fetchall()
        self.assertEqual(len(rows),1)
        self.assertEqual(Path(rows[0][0]).read_text(encoding='utf-8'),'changed text')

    def test_ready_publication_fallback_collision_never_overwrites(self):
        ready=self.root/'ready';ready.mkdir()
        (ready/self.source.name).write_bytes(b'primary')
        protected=ready/'source_12345678.png'
        protected.write_bytes(b'private')
        view=SimpleNamespace(current_project='a'*32,db=self.db,core=self.view.core,
            refresh_publications=lambda:None,
            statusBar=lambda:SimpleNamespace(showMessage=lambda *a:None))
        env={**self.env,'ensure_project_folders':lambda *a:self.root,
             'QMessageBox':SimpleNamespace(warning=lambda *a:None,information=lambda *a:None)}
        self.assertTrue(method('import_ready_publication_files',env)(view,[self.source]))
        self.assertEqual(protected.read_bytes(),b'private')

    def test_ready_result_collision_preserves_original(self):
        ready=self.root/'ready';ready.mkdir()
        protected=ready/('12345678123412341234123412341234_'+self.source.name)
        protected.write_bytes(b'private')
        view=SimpleNamespace(current_project='a'*32,db=self.db,core=self.view.core,active_section='test')
        env={**self.env,'ensure_project_folders':lambda *a:self.root}
        self.assertTrue(method('register_ready_result',env)(view,self.source))
        self.assertEqual(protected.read_bytes(),b'private')
        self.assertEqual(self.db.execute('select count(*) from publication_media_review').fetchone()[0],1)

    def test_existing_import_copy_cannot_be_overwritten(self):
        existing=self.root/'processed'/'12345678123412341234123412341234.png'
        existing.parent.mkdir();existing.write_bytes(b'private')
        self.assertIsNone(method('add_project_file',self.env)(self.view,'result','processed','Choose'))
        self.assertEqual(existing.read_bytes(),b'private')
        self.assertEqual(self.db.execute('select count(*) from project_files').fetchone()[0],0)

    def test_existing_text_cannot_be_overwritten(self):
        from unittest.mock import patch
        filename='12345678123412341234123412341234.txt'
        existing=self.root/'texts'/filename
        existing.parent.mkdir(exist_ok=True);existing.write_text('private', encoding='utf-8')
        with patch('application_core.uuid4',return_value=UUID('12345678123412341234123412341234')):
            self.assertFalse(method('save_text',self.env)(self.view))
        self.assertEqual(existing.read_text(encoding='utf-8'),'private')

if __name__=='__main__':unittest.main()
