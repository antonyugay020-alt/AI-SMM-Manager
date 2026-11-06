"""Boundary checks for cloud result ownership without importing Qt."""
import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace, MethodType
from unittest.mock import patch
from uuid import uuid4

SOURCE = Path(__file__).with_name('app.py')

def method(name, globals_):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    studio = next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='Studio')
    node=next(n for n in studio.body if isinstance(n,ast.FunctionDef) and n.name==name)
    fragment=ast.Module(body=[node],type_ignores=[])
    ast.fix_missing_locations(fragment)
    namespace=dict(globals_)
    exec(compile(fragment,str(SOURCE),'exec'),namespace)
    return namespace[name]

class CloudOwnership(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE project_files(id TEXT,project_id TEXT,kind TEXT,display_name TEXT,path TEXT,created_at TEXT)')
        self.db.executemany('INSERT INTO projects VALUES(?)',[('first',),('second',)])
        # Fixture setup must be committed before invoking a real GUI callback.
        # Otherwise it simulates an unrelated pending transaction.
        self.db.commit()
        self.p1=self.root/'first'/'processed';self.p2=self.root/'second'/'processed'
        self.p1.mkdir(parents=True);self.p2.mkdir(parents=True)
        self.view=SimpleNamespace(db=self.db,current_project='second',add_result_version=lambda *a:None,
                                  register_ready_result=lambda *a:None,append_chat=lambda *a:None)
        from application_core import ApplicationRuntime
        self.view.core=ApplicationRuntime(self.db,lambda pid:self.root/pid)
        self.view._semantic_media_publication=MethodType(method('_semantic_media_publication',{}),self.view)
        self.view._semantic_media_accepted=MethodType(method('_semantic_media_accepted',{}),self.view)
        self.cloud_done=method('cloud_done',{'Path':Path,'uuid4':uuid4,'now':lambda:'now',
                          'project_dir':lambda pid:self.root/pid,'Image':__import__('PIL.Image',fromlist=['Image'])})
    def test_background_result_belongs_to_launch_project_even_after_switch(self):
        from PIL import Image
        f=self.p1/'output.png';Image.new('RGB',(8,8)).save(f)
        self.cloud_done(self.view,str(f),project_id='first')
        rows=self.db.execute('SELECT project_id,path FROM project_files').fetchall()
        self.assertEqual(rows,[('first',str(f))])
    def test_other_project_file_is_rejected(self):
        from PIL import Image
        f=self.p2/'other.png';Image.new('RGB',(8,8)).save(f)
        with self.assertRaises(ValueError):self.cloud_done(self.view,str(f),project_id='first')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0],0)
    def test_corrupt_image_is_rejected(self):
        f=self.p1/'broken.png';f.write_bytes(b'not a PNG')
        with self.assertRaises(Exception):self.cloud_done(self.view,str(f),project_id='first')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0],0)
    def test_symlink_output_rejected(self):
        from PIL import Image
        f=self.p2/'real.png';Image.new('RGB',(8,8)).save(f)
        alias=self.p1/'alias.png'
        try:alias.symlink_to(f)
        except (NotImplementedError,OSError):self.skipTest('symlinks unsupported')
        with self.assertRaises(ValueError):self.cloud_done(self.view,str(alias),project_id='first')

if __name__=='__main__':unittest.main()

class DocumentExportTruth(unittest.TestCase):
    """Existing documents must not be accepted unless exports also succeed."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        from application_bootstrap import install_schema
        install_schema(self.db)
        self.db.execute('INSERT INTO projects(id,name,created_at) VALUES(?,?,?)', ('project','Sample','before'))
        self.db.execute('INSERT INTO manager_documents(id,project_id,kind,title,content,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                        ('doc','project','strategy','old','old','before','before'))
        self.db.commit()
        self.messages=[]
        self.view=SimpleNamespace(current_project='project',active_document_id='doc',db=self.db,
               document_title=SimpleNamespace(text=lambda:'new'),
               document_editor=SimpleNamespace(toPlainText=lambda:'new text'),
               document_views=SimpleNamespace(currentIndex=lambda:0),
               statusBar=lambda:SimpleNamespace(showMessage=lambda message,*args:self.messages.append(message)))

        from application_core import ApplicationRuntime
        self.view.core=ApplicationRuntime(self.db,lambda pid:self.root)

    def make_save(self,exporter):
        self.view._export_document_files=exporter
        import os,shutil
        from uuid import uuid4
        return method('save_manager_document',{'now':lambda:'after', 'project_dir':lambda pid:self.root,
            'document_folder':lambda kind:'strategies','safe_file_label':lambda value:value,
            'Path':Path,'shutil':shutil,'os':os,'uuid4':uuid4,'export_document_files':exporter})

    def test_export_failure_never_reports_export_success(self):
        def reject(*args): raise OSError('printer refused PDF')
        self.assertFalse(self.make_save(reject)(self.view))
        self.assertIn('ожидает восстановления',self.messages[-1])
        self.assertEqual(self.db.execute('SELECT content FROM manager_documents').fetchone()[0],'new text')
        self.assertEqual(self.db.execute('SELECT state FROM manager_document_exports').fetchone()[0],'pending')

    def test_export_success_reports_success(self):
        def export(*args,**kwargs):
            txt=self.root/'strategies'/'new_doc.txt';pdf=self.root/'pdf'/'new_doc.pdf'
            txt.parent.mkdir(parents=True,exist_ok=True);pdf.parent.mkdir(parents=True,exist_ok=True)
            txt.write_text('new text');pdf.write_bytes(b'%PDF-1.4 valid mock PDF document')
        self.assertTrue(self.make_save(export)(self.view))
        self.assertIn('сохранены',self.messages[-1])
        self.assertEqual(self.db.execute('SELECT content FROM manager_documents').fetchone()[0],'new text')
