"""End-to-end SQLite/filesystem fault injection for document exports and deletion.

No PySide6 needed: production GUI methods are extracted directly from app.py.
"""
from __future__ import annotations
import ast
from pathlib import Path
import os
import sqlite3
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID
from project_deletion import delete_project_data, recover_deletions

PROGRAM = Path(__file__).resolve().parents[1]


def method(name, environment=None):
    syntax=ast.parse((PROGRAM/'app.py').read_text(encoding='utf-8'))
    matches=[n for cls in syntax.body if isinstance(cls,ast.ClassDef)
             for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==name]
    assert len(matches)==1
    env={} if environment is None else dict(environment)
    exec(compile(ast.fix_missing_locations(ast.Module(body=matches,type_ignores=[])),str(PROGRAM/'app.py'),'exec'),env)
    return env[name]


class DocumentsAfterFailure(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.project='b'*32;self.doc='c'*32
        self.folder=self.root/self.project
        self.folder.mkdir()
        self.db=sqlite3.connect(self.root/'test.sqlite3')
        self.addCleanup(self.db.close)
        self.db.executescript('''
            CREATE TABLE projects(id text primary key,name text,category text,description text);
            CREATE TABLE project_locations(project_id text primary key,folder text);
            CREATE TABLE manager_documents(id text primary key,project_id text,kind text,title text,content text,updated_at text);
            CREATE TABLE assets(id text primary key,project_id text);
        ''')
        self.db.execute('insert into projects values(?,?,?,?)',(self.project,'name','',''))
        self.db.execute('insert into project_locations values(?,?)',(self.project,str(self.folder)))
        self.db.execute('insert into manager_documents values(?,?,?,?,?,?)',(self.doc,self.project,'strategy','Title','Old','start'))
        self.db.commit()
        self.txt=self.folder/'strategies'/('Title_'+self.doc[:8]+'.txt')
        self.pdf=self.folder/'pdf'/('Title_'+self.doc[:8]+'.pdf')
        self.txt.parent.mkdir();self.pdf.parent.mkdir()
        self.txt.write_text('Old', encoding='utf-8');self.pdf.write_bytes(b'%PDF-old')

    def test_delete_removes_export_and_database_but_not_neighbors(self):
        neighbor=self.folder/'pdf'/'other.pdf';neighbor.write_bytes(b'%PDF-other')
        self.assertTrue(delete_project_data(self.db,self.project,'documents',self.folder,[self.doc]))
        self.assertFalse(self.txt.exists());self.assertFalse(self.pdf.exists())
        self.assertTrue(neighbor.exists())
        self.assertEqual(self.db.execute('select count(*) from manager_documents').fetchone()[0],0)

    def test_delete_sqlite_failure_restores_files_and_row(self):
        self.db.execute("CREATE TRIGGER no_delete BEFORE DELETE ON manager_documents BEGIN SELECT RAISE(ABORT,'blocked'); END")
        self.db.commit()
        with self.assertRaises(sqlite3.DatabaseError):
            delete_project_data(self.db,self.project,'documents',self.folder,[self.doc])
        self.assertEqual(self.txt.read_text(encoding='utf-8'),'Old')
        self.assertEqual(self.pdf.read_bytes(),b'%PDF-old')
        self.assertEqual(self.db.execute('select count(*) from manager_documents').fetchone()[0],1)

    def test_interrupted_delete_restores_original_files(self):
        stage=self.root/'.ai_smm_delete_staging'/('d'*32)
        (stage/'content'/'strategies').mkdir(parents=True)
        import json
        rel=self.txt.relative_to(self.folder).as_posix()
        (stage/'manifest.json').write_text(json.dumps({'op_id':stage.name,'project_ref':self.project,
            'scope':'documents','project_root':str(self.folder),'entries':[rel]}), encoding='utf-8')
        os.replace(self.txt,stage/'content'/rel)
        recover_deletions(self.db,[self.folder])
        self.assertEqual(self.txt.read_text(encoding='utf-8'),'Old')

    def test_committed_document_cleanup_can_resume(self):
        original=shutil.rmtree
        def fail_once(path,*args,**kw):
            if '.ai_smm_delete_staging' in str(path):raise OSError('postcommit crash')
            return original(path,*args,**kw)
        with patch('project_deletion.shutil.rmtree',side_effect=fail_once):
            delete_project_data(self.db,self.project,'documents',self.folder,[self.doc])
        self.assertFalse(self.txt.exists())
        self.assertEqual(self.db.execute('select count(*) from ai_delete_commit_marks').fetchone()[0],1)
        recover_deletions(self.db,[self.folder])
        self.assertEqual(self.db.execute('select count(*) from ai_delete_commit_marks').fetchone()[0],0)

    def test_unrelated_project_document_remains_untouched(self):
        foreign='d'*32
        self.db.execute('insert into manager_documents values(?,?,?,?,?,?)',(foreign,'foreign','strategy','Title','text','start'))
        self.db.commit()
        with self.assertRaises(ValueError):
            delete_project_data(self.db,self.project,'documents',self.folder,[foreign])
        self.assertTrue(self.txt.exists())
        self.assertEqual(self.db.execute('select count(*) from manager_documents').fetchone()[0],2)

    def test_delete_symlink_export_aborts(self):
        self.pdf.unlink()
        outside=self.root/'outside.pdf';outside.write_bytes(b'%PDF-stay')
        try:
            self.pdf.symlink_to(outside)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows requires symlink privilege for this case (WinError 1314)')
            raise
        with self.assertRaises(ValueError):
            delete_project_data(self.db,self.project,'documents',self.folder,[self.doc])
        self.assertEqual(outside.read_bytes(),b'%PDF-stay')
        self.assertEqual(self.db.execute('select count(*) from manager_documents').fetchone()[0],1)

    def test_symlink_parent_cannot_delete_external_files(self):
        self.txt.unlink()
        self.pdf.unlink()
        (self.folder/'strategies').rmdir()
        outside=self.root/'outside';outside.mkdir()
        (outside/self.txt.name).write_text('foreign original', encoding='utf-8')
        try:
            (self.folder/'strategies').symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows requires symlink privilege for this case (WinError 1314)')
            raise
        with self.assertRaises(ValueError):
            delete_project_data(self.db,self.project,'documents',self.folder,[self.doc])
        self.assertEqual((outside/self.txt.name).read_text(encoding='utf-8'),'foreign original')
        self.assertEqual(self.db.execute('select count(*) from manager_documents').fetchone()[0],1)

    def test_short_uuid_collision_blocks_document_deletion(self):
        collision = self.doc[:8] + 'd'*24
        self.db.execute('insert into manager_documents values(?,?,?,?,?,?)',
                        (collision,self.project,'tasks','Title','Other','now'))
        self.db.commit()
        with self.assertRaises(ValueError):
            delete_project_data(self.db,self.project,'documents',self.folder,[self.doc])
        self.assertTrue(self.pdf.exists())
        self.assertEqual(self.db.execute('select count(*) from manager_documents').fetchone()[0],2)

    def test_manual_export_blocks_on_failed_transaction_without_touching_old(self):
        messages=[]
        view=SimpleNamespace(current_project=self.project,active_document_id=self.doc,db=self.db,
            save_manager_document=lambda:False)
        env={'QMessageBox':SimpleNamespace(warning=lambda *args:messages.append(args)),
             'project_dir':lambda *a:self.folder,'document_folder':lambda k:'strategies',
             'safe_file_label':lambda x:x}
        self.assertFalse(method('export_active_document',env)(view,False))
        self.assertEqual(self.txt.read_text(encoding='utf-8'),'Old')
        self.assertEqual(self.pdf.read_bytes(),b'%PDF-old')
        self.assertEqual(self.db.execute('select content from manager_documents').fetchone()[0],'Old')
        self.assertEqual(len(messages),1)

    def test_manual_export_invokes_guarded_save_even_when_text_unchanged(self):
        called=[]
        view=SimpleNamespace(current_project=self.project,active_document_id=self.doc,db=self.db,
            core=SimpleNamespace(workspace=lambda pid:self.folder,
                data=__import__('project_data_runtime').ProjectDataRuntime(self.db)),
            save_manager_document=lambda:called.append(True) or True)
        env={'project_dir':lambda *a:self.folder,'document_folder':lambda k:'strategies',
             'safe_file_label':lambda x:x,'Path':Path}
        self.assertTrue(method('export_active_document',env)(view,True))
        self.assertEqual(called,[True])

    def test_gui_delete_failure_keeps_active_document_selected(self):
        messages=[]
        view=SimpleNamespace(current_project=self.project,active_document_id=self.doc,db=self.db,
            core=SimpleNamespace(controls=SimpleNamespace(backup_and_delete=lambda *a,**k: (_ for _ in ()).throw(sqlite3.OperationalError('fail')))),
            document_title=SimpleNamespace(text=lambda:'Title'))
        env={'QMessageBox':SimpleNamespace(Yes=1,No=0,question=lambda *a:1,
              warning=lambda *a:messages.append(a)),'project_dir':lambda *_:self.folder}
        method('delete_manager_document',env)(view)
        self.assertEqual(view.active_document_id,self.doc)
        self.assertEqual(self.db.execute('select count(*) from manager_documents').fetchone()[0],1)
        self.assertTrue(messages)


if __name__=='__main__':unittest.main()
