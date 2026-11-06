"""Cross-layer contract for AI document registration during a result transaction."""
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from application_core import ApplicationRuntime
from sqlite_transaction_core import own
from manual_document_projection import _paths

class GeneratedDocumentBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE projects(id TEXT PRIMARY KEY);
          CREATE TABLE manager_documents(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,
            kind TEXT NOT NULL,title TEXT NOT NULL,content TEXT NOT NULL,
            created_at TEXT,updated_at TEXT);
          INSERT INTO projects VALUES('alpha'); INSERT INTO projects VALUES('beta');''')
        self.db.commit()
        self.root=Path(self.tmp.name).resolve()
        self.app=ApplicationRuntime(self.db,lambda pid:self.root/pid)

    def stage(self,pid='alpha',ident='aaaaaaaa111',title='Plan'):
        return self.app.stage_generated_document(project_id=pid,document_id=ident,
             kind='plans',title=title,content='Draft',created_at='now')

    def test_requires_existing_completion_transaction(self):
        with self.assertRaises(RuntimeError):self.stage()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM manager_documents').fetchone()[0],0)

    def test_registers_only_named_project_and_never_commits(self):
        with own(self.db):
            self.stage('alpha')
            self.assertTrue(self.db.in_transaction)
            self.assertEqual(self.db.execute('SELECT project_id FROM manager_documents').fetchone(),('alpha',))
        with own(self.db):self.stage('beta','bbbbbbbb222')
        self.assertEqual(set(self.db.execute('SELECT project_id FROM manager_documents')),{('alpha',),('beta',)})

    def test_failure_rolls_back_with_parent_operation(self):
        try:
            with own(self.db):
                self.stage()
                raise RuntimeError('worker failed')
        except RuntimeError:pass
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM manager_documents').fetchone()[0],0)

    def test_rejects_collision_without_mutating_parent_transaction(self):
        with own(self.db):
            self.stage()
            with self.assertRaises(ValueError):self.stage(ident='aaaaaaaa222')
            self.assertEqual(self.db.execute('SELECT COUNT(*) FROM manager_documents').fetchone()[0],1)

    def test_rejects_existing_file_and_foreign_project(self):
        path=_paths(self.root/'alpha','aaaaaaaa111','plans','Plan')[0]
        path.parent.mkdir(parents=True);path.write_text('foreign data', encoding='utf-8')
        with own(self.db):
            with self.assertRaises(FileExistsError):self.stage()
            with self.assertRaises(ValueError):self.stage('missing','cccccccc333')
        self.assertEqual(path.read_text(encoding='utf-8'),'foreign data')

    def test_rejects_reserved_orphan_name(self):
        self.db.execute('''CREATE TABLE manager_document_exports
            (document_id TEXT,project_id TEXT,orphan_json TEXT)''')
        self.db.execute('''INSERT INTO manager_document_exports VALUES
            ('aaaaaaaa222','alpha','[["plans", "Plan"]]')''')
        self.db.commit()
        with own(self.db):
            with self.assertRaises(ValueError):self.stage()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM manager_documents').fetchone()[0],0)

if __name__=='__main__':unittest.main()
