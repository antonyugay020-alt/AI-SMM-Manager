"""Adversarial tests for cross-operation SQLite ownership and filesystem boundaries."""
import ast, hashlib, shutil, sqlite3, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID
from application_bootstrap import open_application_database
from application_core import ApplicationRuntime

APP = Path(__file__).resolve().parents[1] / 'app.py'

def function(name, env):
    root=ast.parse(APP.read_text(encoding='utf-8'))
    nodes=[n for cls in root.body if isinstance(cls,ast.ClassDef) for n in cls.body
           if isinstance(n,ast.FunctionDef) and n.name==name]
    assert len(nodes)==1
    namespace=dict(env)
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),str(APP),'exec'),namespace)
    return namespace[name]

class SqliteOwnershipAndFileSafety(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.db=open_application_database(self.root/'manager.sqlite',self.root)
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE unrelated(value TEXT)')
        self.db.execute("INSERT INTO projects(id,name,created_at) VALUES('p','Sample','now')")
        self.db.commit()
        self.db.execute('INSERT INTO unrelated VALUES(?)',('another operation',))
        self.assertTrue(self.db.in_transaction)
        self.core=ApplicationRuntime(self.db,lambda pid:self.root)
        self.events=[]
        self.view=SimpleNamespace(current_project='p',db=self.db,core=self.core,active_section='strategy',
            statusBar=lambda:SimpleNamespace(showMessage=lambda *x:self.events.append(x)),
            append_chat=lambda *x:None,
            show_section=lambda *x:None,
            document_ids=[],
            working_sources=[],asset_paths=[],
            projects=SimpleNamespace(currentIndex=lambda:0),
            select_project=lambda *x:None,
            add_working_sources=lambda *x:None,
            show_reference=lambda:None,
            reference=SimpleNamespace(show_file=lambda *x:None),
            comparison=SimpleNamespace(setSizes=lambda *x:None))
        self.env={'project_dir':lambda pid:self.root,'document_folder':lambda kind:'strategies',
            'safe_file_label':lambda title:'export', 'now':lambda:'now',
            'uuid4':lambda:UUID('12345678123412341234123412341234'),
            'Path':Path,'hashlib':hashlib,'sqlite3':sqlite3,'shutil':shutil,
            'IMAGE_EXTENSIONS':{'.png'},
            'QMessageBox':SimpleNamespace(warning=lambda *x:self.events.append(x))}

    def check_other_operation(self):
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT value FROM unrelated').fetchall(), [('another operation',)])
        self.db.commit()
        self.assertEqual(self.db.execute('SELECT value FROM unrelated').fetchall(), [('another operation',)])

    def test_ai_save_cannot_export_or_rollback_other_transaction(self):
        def exporter(*args): self.fail('export must be blocked')
        run=function('save_ai_document',{**self.env,'export_document_files':exporter})
        with self.assertRaisesRegex(RuntimeError,'[Tt]ransaction|SQLite|транзакц|занят'):
            run(self.view,'article','text',kind='strategy')
        self.check_other_operation()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM manager_documents').fetchone()[0],0)

    def test_import_asset_cannot_rollback_other_transaction(self):
        src=self.root/'source.png';src.write_bytes(b'source')
        run=function('import_asset_paths',self.env)
        run(self.view,[str(src)])
        self.check_other_operation()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM assets').fetchone()[0],0)
        self.assertEqual(list((self.root/'originals').glob('*')) if (self.root/'originals').exists() else [],[])

    def test_import_reference_cannot_rollback_other_transaction(self):
        src=self.root/'source.png';src.write_bytes(b'source')
        run=function('import_reference_path',self.env)
        run(self.view,str(src))
        self.check_other_operation()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0],0)

    def test_register_ready_result_cannot_commit_other_transaction(self):
        source=self.root/'sample.png';source.write_bytes(b'content')
        run=function('register_ready_result',self.env)
        with self.assertRaisesRegex(RuntimeError,'SQLite'):
            run(self.view,str(source))
        self.check_other_operation()
        self.assertFalse((self.root/'ready').exists())

    def test_add_project_file_cannot_commit_other_transaction(self):
        source=self.root/'sample.png';source.write_bytes(b'content')
        env={**self.env,'QFileDialog':SimpleNamespace(getOpenFileName=lambda *a:(str(source),''))}
        run=function('add_project_file',env)
        self.assertIsNone(run(self.view,'result','processed','Source'))
        self.check_other_operation()
        self.assertFalse((self.root/'processed').exists())

    def test_save_text_cannot_commit_other_transaction(self):
        self.view.text_editor=SimpleNamespace(toPlainText=lambda:'draft')
        run=function('save_text',self.env)
        self.assertFalse(run(self.view))
        self.check_other_operation()
        self.assertFalse((self.root/'texts').exists())

    def test_send_message_cannot_commit_other_transaction(self):
        message=SimpleNamespace(toPlainText=lambda:'new work')
        self.view.message=message
        self.view.ai_status=SimpleNamespace(setText=lambda *a:self.events.append(a))
        run=function('send_message',self.env)
        run(self.view)
        self.check_other_operation()
        self.assertTrue(self.events)

    def test_add_publication_cannot_commit_other_transaction(self):
        run=function('add_media_paths_to_channel',self.env)
        self.assertFalse(run(self.view,['any-file'],'Posts'))
        self.check_other_operation()

    def test_ai_save_rejects_truncated_uuid_collision(self):
        self.db.commit()
        self.db.execute('INSERT INTO manager_documents VALUES(?,?,?,?,?,?,?)',
                        ('12345678'+'f'*24,'p','strategy','Old','Body','now','now'))
        self.db.commit()
        run=function('save_ai_document',{**self.env,'export_document_files':lambda *a:self.fail('never export')})
        with self.assertRaisesRegex(RuntimeError,'Коллизия'):
            run(self.view,'article','text',kind='strategy')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM manager_documents').fetchone()[0],1)

    def test_ai_save_rejects_linked_export_parent(self):
        self.db.commit()
        outside=self.root/'outside'; outside.mkdir()
        try:
            (self.root/'strategies').symlink_to(outside,target_is_directory=True)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows requires symlink privilege for this case (WinError 1314)')
            raise
        run=function('save_ai_document',{**self.env,'export_document_files':lambda *a:self.fail('never export')})
        with self.assertRaises((RuntimeError,ValueError)):
            run(self.view,'article','text',kind='strategy')
        self.assertEqual(list(outside.iterdir()),[])
