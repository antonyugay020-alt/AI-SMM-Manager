"""Adversarial ownership checks shared by imports, library removal, and results."""
import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID
from application_bootstrap import open_application_database
import shutil

APP=Path(__file__).resolve().parents[1]/'app.py'
from project_file_boundary import require_managed_folder, require_managed_file


def method(name, env):
    root=ast.parse(APP.read_text(encoding='utf-8'))
    found=[node for cls in root.body if isinstance(cls,ast.ClassDef)
           for node in cls.body if isinstance(node,ast.FunctionDef) and node.name==name]
    assert len(found)==1
    scope=dict(env)
    exec(compile(ast.fix_missing_locations(ast.Module(body=found,type_ignores=[])),str(APP),'exec'),scope)
    return scope[name]


class ProjectMediaBoundaries(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)/'project'; self.root.mkdir()
        self.outside=Path(tmp.name)/'outside';self.outside.mkdir()
        self.data=self.outside/'private.png';self.data.write_bytes(b'PRIVATE')
        self.db=open_application_database(Path(tmp.name)/'manager.sqlite',Path(tmp.name))
        self.addCleanup(self.db.close)
        self.db.execute("INSERT INTO projects(id,name,created_at) VALUES('p','P','now')")
        self.db.commit()
        self.errors=[]
        self.message=SimpleNamespace(Yes=1,No=2,question=lambda *a:1,warning=lambda *a:self.errors.append(a),information=lambda *a:self.errors.append(a))
        self.view=SimpleNamespace(current_project='p',db=self.db, active_section='test',
            statusBar=lambda:SimpleNamespace(showMessage=lambda *x:None),
            refresh_publications=lambda:None,
            ready_media=SimpleNamespace(selectedItems=lambda:[]),
            result_list=SimpleNamespace(selectedItems=lambda:[]),
            result_versions=[],
            append_chat=lambda *a:None)
        from application_core import ApplicationRuntime
        self.view.core=ApplicationRuntime(self.db,lambda pid:self.root)
        self.env={'Path':Path,'QMessageBox':self.message,'project_dir':lambda *a:self.root,
                  'ensure_project_folders':lambda *a:self.root,
                  'uuid4':lambda:UUID('12345678123412341234123412341234'),
                  'shutil':shutil,'os':__import__('os'),'sqlite3':sqlite3,'now':lambda:'now',
                  'Qt':SimpleNamespace(UserRole=32),'IMAGE_EXTENSIONS':{'.png'},
                  'QFileDialog':SimpleNamespace(getOpenFileName=lambda *a:(str(self.data),'png'))}

    def link(self,folder):
        link=self.root/folder
        try:link.symlink_to(self.outside,target_is_directory=True)
        except (OSError,NotImplementedError):self.skipTest('symlinks not supported')
        return link

    def test_valid_managed_file(self):
        f=self.root/'ready'/'normal.png';f.parent.mkdir();f.write_bytes(b'ok')
        self.assertEqual(require_managed_file(self.root,f,('ready',)),f)

    def test_reject_symlink_parent_even_if_resolves_with_allowed_path(self):
        link=self.link('ready')
        with self.assertRaises(ValueError):require_managed_file(self.root,link/'private.png',('ready',))
        self.assertEqual(self.data.read_bytes(),b'PRIVATE')

    def test_reject_nested_linked_trash(self):
        (self.root/'.trash').mkdir()
        try:
            (self.root/'.trash'/'results').symlink_to(self.outside,target_is_directory=True)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows requires symlink privilege for this case (WinError 1314)')
            raise
        with self.assertRaises(ValueError):require_managed_folder(self.root,'.trash/results')

    def test_remove_workspace_results_cannot_move_external_file_via_link(self):
        src=self.link('processed')/'private.png'
        item=SimpleNamespace()
        self.view.result_list.selectedItems=lambda:[item]
        self.view.result_list.row=lambda *_:0
        self.view.result_versions=[str(src)]
        run=method('remove_workspace_results',self.env)
        self.assertFalse(run(self.view))
        self.assertEqual(self.data.read_bytes(),b'PRIVATE')
        self.assertFalse((self.root/'.trash'/'results').exists())

    def test_remove_ready_media_cannot_move_external_file_via_link(self):
        src=self.link('ready')/'private.png'
        self.view.ready_media.selectedItems=lambda:[SimpleNamespace(data=lambda *_:str(src))]
        run=method('remove_ready_media',self.env)
        self.assertFalse(run(self.view))
        self.assertEqual(self.data.read_bytes(),b'PRIVATE')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM publication_jobs').fetchone()[0],0)

    def test_register_ready_result_rejects_linked_destination(self):
        self.link('ready')
        run=method('register_ready_result',self.env)
        with self.assertRaises(ValueError):run(self.view,str(self.data))
        self.assertEqual(self.data.read_bytes(),b'PRIVATE')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM publication_media_review').fetchone()[0],0)

    def test_import_ready_media_rejects_linked_destination(self):
        self.link('ready')
        run=method('import_ready_publication_files',self.env)
        self.assertFalse(run(self.view,[self.data]))
        self.assertEqual(self.data.read_bytes(),b'PRIVATE')

    def test_import_asset_rejects_linked_originals(self):
        self.link('originals')
        run=method('import_asset_paths',self.env)
        self.assertIsNone(run(self.view,[self.data]))
        self.assertEqual(self.data.read_bytes(),b'PRIVATE')

    def test_add_project_file_rejects_linked_processed(self):
        self.link('processed')
        run=method('add_project_file',self.env)
        self.assertIsNone(run(self.view,'result','processed','Choose'))
        self.assertEqual(self.data.read_bytes(),b'PRIVATE')

    def test_save_text_rejects_linked_folder(self):
        self.link('texts')
        self.view.text_editor=SimpleNamespace(toPlainText=lambda:'draft')
        self.env['datetime']=__import__('datetime').datetime
        run=method('save_text',self.env)
        self.assertFalse(run(self.view))
        self.assertEqual(self.data.read_bytes(),b'PRIVATE')

    def test_reference_import_rejects_linked_directory(self):
        self.link('references')
        run=method('import_reference_path',self.env)
        self.assertIsNone(run(self.view,str(self.data)))
        self.assertEqual(self.data.read_bytes(),b'PRIVATE')


if __name__=='__main__':unittest.main()
