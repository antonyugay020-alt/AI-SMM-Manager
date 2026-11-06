"""Fault injection against real shipped GUI methods without requiring a Qt host."""
import ast
import hashlib
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

APP = Path(__file__).resolve().parents[1] / 'app.py'


def extract_method(name, env):
    root = ast.parse(APP.read_text(encoding='utf-8'))
    methods = [node for cls in root.body if isinstance(cls, ast.ClassDef)
               for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name]
    assert len(methods) == 1, name
    namespace = dict(env)
    exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])), str(APP), 'exec'), namespace)
    implementation=namespace[name]
    if name!='save_manager_document' or 'export_document_files' not in env:
        return implementation
    def invoke(view,*args,**kwargs):
        # The Qt method now uses its own project-scoped export adapter.
        view._export_document_files=env['export_document_files']
        return implementation(view,*args,**kwargs)
    return invoke


class TransactionalEdit(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.id = 'd' * 32
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.execute('INSERT INTO projects VALUES(?)',('p',))
        self.db.execute('CREATE TABLE manager_documents(id TEXT PRIMARY KEY,project_id TEXT,kind TEXT,title TEXT,content TEXT,updated_at TEXT)')
        self.db.execute('INSERT INTO manager_documents VALUES(?,?,?,?,?,?)',
                        (self.id, 'p', 'strategy', 'Old', 'Original', 'oldtime'))
        self.db.commit()
        self.oldpaths = (self.root/'strategies' / ('Old_' + self.id[:8] + '.txt'),
                         self.root/'pdf' / ('Old_' + self.id[:8] + '.pdf'))
        for p, content in zip(self.oldpaths, (b'original text', b'%PDF-original')):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
        self.status = []
        self.view = SimpleNamespace(current_project='p', active_document_id=self.id, db=self.db,
            document_title=SimpleNamespace(text=lambda:'Old'),
            document_editor=SimpleNamespace(toPlainText=lambda:'Changed'),
            document_views=SimpleNamespace(currentIndex=lambda:0),
            statusBar=lambda:SimpleNamespace(showMessage=lambda *a:self.status.append(a)))
        from application_core import ApplicationRuntime
        self.view.core = ApplicationRuntime(self.db, lambda pid:self.root)
        self.env = {'QMessageBox':SimpleNamespace(information=lambda *a:None),
            'project_dir':lambda pid:self.root, 'document_folder':lambda kind: {'tasks':'texts'}.get(kind,'strategies'),
            'safe_file_label':lambda title:title, 'Path':Path, 'shutil':shutil, 'os':os,
            'now':lambda:'newtime', 'uuid4':lambda:UUID('12345678123412341234123412341234')}

    def run_edit(self, exporter):
        return extract_method('save_manager_document', {**self.env, 'export_document_files': exporter})(self.view)

    def original_unchanged(self):
        self.assertEqual(self.db.execute('SELECT title,content FROM manager_documents').fetchone(),('Old','Original'))
        self.assertEqual([p.read_bytes() for p in self.oldpaths],[b'original text',b'%PDF-original'])

    def test_failed_pdf_restores_existing_exports_and_database(self):
        def broken(*args,**kwargs):
            raise OSError('disk full')
        self.assertFalse(self.run_edit(broken))
        self.assertEqual(self.db.execute('SELECT title,content FROM manager_documents').fetchone(),('Old','Changed'))
        self.assertEqual([p.read_bytes() for p in self.oldpaths],[b'original text',b'%PDF-original'])
        self.assertEqual(self.db.execute('SELECT state FROM manager_document_exports').fetchone()[0],'pending')

    def test_failed_sql_update_restores_existing_exports(self):
        self.db.execute("CREATE TRIGGER no_write BEFORE UPDATE ON manager_documents BEGIN SELECT RAISE(ABORT,'readonly'); END")
        def valid(*args,**kwargs):
            self.oldpaths[0].write_text('Changed')
            self.oldpaths[1].write_bytes(b'%PDF-updated-document-payload')
        self.assertFalse(self.run_edit(valid))
        self.original_unchanged()

    def test_success_updates_files_and_database(self):
        def valid(*args,**kwargs):
            self.oldpaths[0].write_text('Changed')
            self.oldpaths[1].write_bytes(b'%PDF-updated-document-payload')
        self.assertTrue(self.run_edit(valid))
        self.assertEqual(self.db.execute('SELECT content FROM manager_documents').fetchone(),('Changed',))
        self.assertEqual(self.oldpaths[0].read_text(),'Changed')

    def test_failed_relocation_keeps_kind_and_old_export(self):
        self.db.execute("CREATE TRIGGER no_move BEFORE UPDATE ON manager_documents BEGIN SELECT RAISE(ABORT,'readonly'); END")
        other=self.root/'texts' / ('Old_'+self.id[:8]+'.txt')
        def valid(*args,**kwargs):
            other.parent.mkdir(parents=True,exist_ok=True);other.write_text('Changed')
            self.oldpaths[1].write_bytes(b'%PDF-changed-document-payload')
        self.assertFalse(extract_method('save_manager_document', {**self.env,
            'export_document_files': valid})(self.view,kind_override='tasks'))
        self.original_unchanged()
        self.assertFalse(other.exists())
        self.assertEqual(self.db.execute('SELECT kind FROM manager_documents').fetchone(),('strategy',))

    def test_successful_rename_updates_title_and_pdf(self):
        renamed=self.root/'strategies'/('New_'+self.id[:8]+'.txt')
        newpdf=self.root/'pdf'/('New_'+self.id[:8]+'.pdf')
        def valid(*args,**kwargs):
            renamed.write_text('Changed')
            newpdf.write_bytes(b'%PDF-new-document-payload')
        self.assertTrue(extract_method('save_manager_document', {**self.env,
            'export_document_files': valid})(self.view,title_override='New'))
        self.assertEqual(self.db.execute('SELECT title FROM manager_documents').fetchone(),('New',))
        self.assertEqual(renamed.read_text(),'Changed')

    def test_rename_never_overwrites_existing_unowned_file(self):
        destination=self.root/'strategies'/('New_'+self.id[:8]+'.txt')
        destination.write_bytes(b'user-file-must-survive')
        def would_overwrite(*args,**kwargs):
            self.fail('export must never run on a collision')
        self.assertFalse(extract_method('save_manager_document', {**self.env,
            'export_document_files': would_overwrite})(self.view,title_override='New'))
        self.assertEqual(destination.read_bytes(),b'user-file-must-survive')
        self.original_unchanged()

    def test_relocation_rejects_linked_parent_directory(self):
        outside=self.root/'outside';outside.mkdir()
        (outside/('Old_'+self.id[:8]+'.txt')).write_bytes(b'foreign')
        try:
            (self.root/'texts').symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows requires symlink privilege for this case (WinError 1314)')
            raise
        def would_overwrite(*args,**kwargs):
            self.fail('linked parent must be blocked before exporter')
        self.assertFalse(extract_method('save_manager_document', {**self.env,
            'export_document_files': would_overwrite})(self.view,kind_override='tasks'))
        self.assertEqual((outside/('Old_'+self.id[:8]+'.txt')).read_bytes(),b'foreign')
        self.original_unchanged()

    def test_short_uuid_collision_blocks_any_export(self):
        other=self.id[:8]+'e'*24
        self.db.execute('INSERT INTO manager_documents VALUES(?,?,?,?,?,?)',
                        (other,'p','strategy','Old','other','oldtime'))
        self.db.commit()
        def would_overwrite(*args,**kwargs):
            self.fail('ambiguous export name must be blocked')
        self.assertFalse(extract_method('save_manager_document', {**self.env,
            'export_document_files': would_overwrite})(self.view))
        self.original_unchanged()

    def test_busy_sqlite_transaction_is_never_rolled_back_by_export(self):
        self.db.execute('CREATE TABLE unrelated_state(value TEXT)')
        self.db.execute('INSERT INTO unrelated_state VALUES(?)',('pending user edit',))
        self.assertTrue(self.db.in_transaction)
        def would_export(*args):
            self.fail('busy transaction must block export before any IO')
        self.assertFalse(self.run_edit(would_export))
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT value FROM unrelated_state').fetchone()[0],
                         'pending user edit')
        self.db.commit()
        self.original_unchanged()

    def test_failed_edit_prevents_document_navigation(self):
        view = SimpleNamespace(_loading_document=False,current_project='p',active_document_id=self.id,
            db=self.db, core=self.view.core, document_title=SimpleNamespace(text=lambda:'Old'),
            document_editor=SimpleNamespace(toPlainText=lambda:'Changed'),
            save_manager_document=lambda:False, document_ids=[self.id,'new'], save_document_if_changed=lambda:False)
        self.assertFalse(extract_method('save_document_if_changed', {})(view))
        # open_document only needs more GUI members if validation succeeded.
        extract_method('open_document', {})(view,1)
        self.assertEqual(view.active_document_id,self.id)


class AssetImportTransaction(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.pid='f'*32
        self.root=Path(folder.name)/self.pid
        self.root.mkdir()
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE assets(id TEXT PRIMARY KEY,project_id TEXT,original_name TEXT,stored_path TEXT,sha256 TEXT,imported_at TEXT)')
        self.db.execute('CREATE TABLE project_files(id TEXT PRIMARY KEY,project_id TEXT,kind TEXT,display_name TEXT,path TEXT,created_at TEXT)')
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.execute('INSERT INTO projects VALUES(?)',(self.pid,))
        self.db.commit()
        self.source=self.root/'sample.png';self.source.write_bytes(b'example')
        self.warnings=[]
        self.view=SimpleNamespace(current_project=self.pid,db=self.db,working_sources=[],asset_paths=[],
            select_project=lambda row:None, projects=SimpleNamespace(currentIndex=lambda:0),
            statusBar=lambda:SimpleNamespace(showMessage=lambda *args:None),
            append_chat=lambda *args:None,
            show_reference=lambda:None,
            reference=SimpleNamespace(show_file=lambda *args:None),
            comparison=SimpleNamespace(setSizes=lambda *args:None))
        from application_core import ApplicationRuntime
        self.view.core = ApplicationRuntime(self.db, lambda pid:self.root)
        self.env={'project_dir':lambda pid:self.root,'Path':Path,'uuid4':lambda:UUID('12345678123412341234123412341234'),
            'IMAGE_EXTENSIONS':{'.png'},'hashlib':hashlib, 'now':lambda:'now',
            'QMessageBox':SimpleNamespace(warning=lambda *args:self.warnings.append(args)),
            'shutil':shutil,'sqlite3':sqlite3}

    def test_asset_failed_insert_rolls_back_deleted_disk_file(self):
        self.db.execute("CREATE TRIGGER fail_asset BEFORE INSERT ON assets BEGIN SELECT RAISE(ABORT,'disk'); END")
        self.db.commit()
        extract_method('import_asset_paths',self.env)(self.view,[self.source])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM assets').fetchone()[0],0)
        self.assertFalse((self.root/'originals').exists())
        self.assertTrue(self.warnings)

    def test_asset_filename_collision_preserves_existing_bytes(self):
        protected=self.root/'originals'/'12345678123412341234123412341234.png'
        protected.parent.mkdir();protected.write_bytes(b'untouchable')
        with patch('media_ingest_core.uuid4',return_value=UUID('12345678123412341234123412341234')):
            extract_method('import_asset_paths',self.env)(self.view,[self.source])
        self.assertEqual(protected.read_bytes(),b'untouchable')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM assets').fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT state FROM media_ingest_jobs").fetchone()[0],'pending')

    def test_reference_filename_collision_preserves_existing_bytes(self):
        protected=self.root/'references'/'12345678123412341234123412341234.png'
        protected.parent.mkdir();protected.write_bytes(b'untouchable')
        with patch('media_ingest_core.uuid4',return_value=UUID('12345678123412341234123412341234')):
            extract_method('import_reference_path',self.env)(self.view,str(self.source))
        self.assertEqual(protected.read_bytes(),b'untouchable')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT state FROM media_ingest_jobs").fetchone()[0],'pending')

    def test_reference_failed_insert_leaves_no_database_or_file(self):
        self.db.execute("CREATE TRIGGER fail_ref BEFORE INSERT ON project_files BEGIN SELECT RAISE(ABORT,'disk'); END")
        self.db.commit()
        extract_method('import_reference_path',self.env)(self.view,str(self.source))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0],0)
        self.assertFalse((self.root/'references').exists())
        self.assertTrue(self.warnings)


if __name__=='__main__': unittest.main()
