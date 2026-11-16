"""Cross-boundary fault injection: GUI methods without a Qt installation.

Extract actual shipped method bodies, so these exercises verify the production
implementation rather than a copied approximation of it.
"""
import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

APP = Path(__file__).resolve().parents[1] / 'app.py'


def method(name, symbols):
    root = ast.parse(APP.read_text(encoding='utf-8'))
    candidates = [n for cls in root.body if isinstance(cls, ast.ClassDef)
                  for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    assert len(candidates) == 1, name
    node = candidates[0]
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    namespace = dict(symbols)
    exec(compile(module, str(APP), 'exec'), namespace)
    return namespace[name]


class PersistedDocumentFaultInjection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name) / 'project'
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(lambda: self.db.close())
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT)')
        self.db.execute('INSERT INTO projects VALUES(?,?)',('project','Тест'))
        self.db.execute('''CREATE TABLE manager_documents (id TEXT PRIMARY KEY,project_id TEXT,
            kind TEXT,title TEXT,content TEXT,created_at TEXT,updated_at TEXT)''')
        self.db.execute('INSERT INTO manager_documents VALUES(?,?,?,?,?,?,?)',
                        ('existing','project','strategy','Проверенная','Текст 1','now','now'))
        self.db.commit()
        self.messages=[]
        self.view=SimpleNamespace(current_project='project',db=self.db,document_ids=[],
            show_section=lambda section:None,
            statusBar=lambda: SimpleNamespace(showMessage=lambda *args:self.messages.append(args)))
        from application_core import ApplicationRuntime
        self.view.core=ApplicationRuntime(self.db,lambda pid:self.folder)
        self.env = {'uuid4':lambda:UUID('12345678123412341234123412341234'),
            'project_dir':lambda project_id: self.folder,
            'document_folder':lambda kind: 'strategies',
            'safe_file_label':lambda title: __import__('re').sub(r'[<>:"/\\|?*\x00-\x1f]','_',str(title)).strip(' .')[:75] or 'document',
            'now':lambda:'now'}

    @property
    def paths(self):
        stem='Стратегия — Тест_12345678'
        return (self.folder/'strategies'/(stem+'.txt'),
                self.folder/'pdf'/(stem+'.pdf'),
                self.folder/'diagrams'/(stem+'.png'))

    def test_partial_pdf_export_never_creates_accepted_row(self):
        def broken(*args):
            first, second, _=self.paths
            first.parent.mkdir(parents=True,exist_ok=True)
            second.parent.mkdir(parents=True,exist_ok=True)
            first.write_text('partial',encoding='utf-8')
            second.write_bytes(b'%PDF-corrupt')
            raise OSError('printer unavailable')
        self.view._export_document_files=broken
        run=method('save_ai_document',self.env)
        with self.assertRaisesRegex(RuntimeError,'Экспорт документа не подтверждён'):
            run(self.view, 'Пиши', 'Новая статья',kind='strategy')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM manager_documents').fetchone(),(2,))
        self.assertEqual(self.db.execute('SELECT state FROM manager_document_exports').fetchone(),('pending',))
        # The intentionally misbehaving exporter left an unverified partial file;
        # it is not accepted as a finished projection and can be overwritten on replay.

    def test_database_failure_removes_export_without_touching_old_document(self):
        self.db.execute("CREATE TRIGGER deny_document BEFORE INSERT ON manager_documents BEGIN SELECT RAISE(ABORT,'disk failure'); END")
        def exporter(*args,**kwargs):
            a,b,_=self.paths
            for p in (a,b):p.parent.mkdir(parents=True,exist_ok=True)
            a.write_text('new',encoding='utf-8');b.write_bytes(b'%PDF-test%%EOF')
        self.view._export_document_files=exporter
        run=method('save_ai_document',self.env)
        with self.assertRaises(sqlite3.DatabaseError):
            run(self.view, 'Пиши', 'Новая статья',kind='strategy')
        self.assertEqual(self.db.execute('SELECT content FROM manager_documents').fetchall(),[('Текст 1',)])
        self.assertTrue(all(not p.exists() for p in self.paths))

    def test_existing_export_path_not_overwritten(self):
        first=self.paths[0];first.parent.mkdir(parents=True);first.write_text('user file', encoding='utf-8')
        self.view._export_document_files=lambda *args: self.fail('overwrite')
        run=method('save_ai_document',self.env)
        with self.assertRaises(FileExistsError):run(self.view,'Пиши','Статья',kind='strategy')
        self.assertEqual(first.read_text(encoding='utf-8'),'user file')

    def test_success_commits_both_document_and_exports(self):
        def exporter(*args,**kwargs):
            a,b,_=self.paths
            for p in (a,b):p.parent.mkdir(parents=True,exist_ok=True)
            a.write_text('Новая статья',encoding='utf-8');b.write_bytes(b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n')
        self.view._export_document_files=exporter
        run=method('save_ai_document',self.env)
        run(self.view,'Пиши','Новая статья',kind='strategy')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM manager_documents').fetchone()[0],2)
        self.assertTrue(self.paths[0].exists() and self.paths[1].exists())


class NewProjectFaultInjection(unittest.TestCase):
    """Integrated GUI invocation must preserve SQLite state across storage failures."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.pid='12345678123412341234123412341234'
        self.folder=Path(self.tmp.name)/self.pid
        self.db=sqlite3.connect(':memory:');self.addCleanup(lambda: self.db.close())
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,created_at TEXT,category TEXT,description TEXT)')
        self.db.execute('CREATE TABLE project_locations(project_id TEXT PRIMARY KEY,folder TEXT)');self.db.commit()
        self.selections=[];self.warnings=[]
        self.view=SimpleNamespace(db=self.db,refresh_projects=self.selections.append)
        from application_core import ApplicationRuntime
        self.view.core=ApplicationRuntime(self.db,lambda pid:Path(self.tmp.name)/pid)

    def run_create(self):
        from project_operation_guard import project_operation_busy
        class ProjectDialog:
            def __init__(self,owner):pass
            def exec(self):return 1
            name=SimpleNamespace(text=lambda:'New')
            category=SimpleNamespace(currentText=lambda:'Other')
            description=SimpleNamespace(toPlainText=lambda:'Description')
        fn=method('add_project',{'project_operation_busy':project_operation_busy,
            'ProjectDialog':ProjectDialog,'QDialog':SimpleNamespace(Accepted=1),
            'uuid4':lambda:UUID(self.pid),'storage_base':lambda:Path(self.tmp.name),
            'now':lambda:'now','sqlite3':sqlite3,
            'QMessageBox':SimpleNamespace(warning=lambda *args:self.warnings.append(args))})
        return fn(self.view)

    def test_db_failure_leaves_no_unregistered_folder(self):
        self.db.execute("CREATE TRIGGER deny_project BEFORE INSERT ON projects BEGIN SELECT RAISE(ABORT,'db unavailable'); END")
        self.run_create()
        self.assertTrue(self.warnings);self.assertFalse(self.folder.exists())
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM projects').fetchone()[0],0)

    def test_filesystem_failure_keeps_registered_project_for_recovery(self):
        original_mkdir=Path.mkdir
        def failing_mkdir(path,*args,**kwargs):
            if path==self.folder/'texts':raise OSError('volume unavailable')
            return original_mkdir(path,*args,**kwargs)
        with patch.object(Path,'mkdir',failing_mkdir):self.run_create()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM projects').fetchone()[0],1)
        self.assertTrue(self.warnings)
        from project_lifecycle import reconcile_project
        reconcile_project(self.db,self.pid)
        self.assertTrue((self.folder/'texts').is_dir())

    def test_preexisting_folder_is_never_modified_on_collision(self):
        self.folder.mkdir();(self.folder/'original.txt').write_text('preserve', encoding='utf-8')
        self.run_create()
        self.assertEqual((self.folder/'original.txt').read_text(encoding='utf-8'),'preserve')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM projects').fetchone()[0],0)

    def test_success_creates_full_project_and_selects_it(self):
        from project_lifecycle import PROJECT_FOLDERS
        self.run_create()
        self.assertTrue(all((self.folder/name).is_dir() for name in PROJECT_FOLDERS))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM projects').fetchone()[0],1)
        self.assertEqual(self.selections,[self.pid])

if __name__ == '__main__': unittest.main()
