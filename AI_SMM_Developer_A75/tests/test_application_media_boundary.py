import sqlite3
import tempfile
import unittest
from pathlib import Path
from application_core import import_project_media,recover_media_effects

class ApplicationMediaBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE projects(id TEXT PRIMARY KEY);
         CREATE TABLE assets(id TEXT PRIMARY KEY,project_id TEXT,original_name TEXT,stored_path TEXT,sha256 TEXT,imported_at TEXT);
         CREATE TABLE project_files(id TEXT PRIMARY KEY,project_id TEXT,kind TEXT,display_name TEXT,path TEXT,created_at TEXT);''')
        self.ids=('a'*32,'b'*32)
        self.db.executemany('INSERT INTO projects VALUES(?)',[(i,) for i in self.ids]);self.db.commit()
        self.good=self.root/'original.png';self.good.write_bytes(b'valid content')
    def workspace(self,p):return self.root/'work'/p
    def test_batch_partial_failure_and_idempotency(self):
        missing=self.root/'missing.png'
        b=import_project_media(self.db,self.workspace,[self.good,missing],project_id=self.ids[0],kind='asset',allowed_extensions={'.png'})
        self.assertEqual(len(b.receipts),1);self.assertEqual(len(b.failures),1)
        self.assertTrue(b.receipts[0].published)
        again=import_project_media(self.db,self.workspace,[self.good],project_id=self.ids[0],kind='asset')
        self.assertTrue(again.receipts[0].duplicate)
        self.assertEqual(self.db.execute('SELECT count(*) FROM assets').fetchone()[0],1)
    def test_isolated_projects_and_references(self):
        for project in self.ids:
            result=import_project_media(self.db,self.workspace,[self.good],project_id=project,kind='reference')
            self.assertTrue(result.receipts[0].published)
        self.assertEqual(self.db.execute('SELECT count(*) FROM project_files').fetchone()[0],2)
        self.assertEqual(recover_media_effects(self.db,self.workspace)['failed'],0)
    def test_rejected_foreign_transaction_and_unknown_project(self):
        with self.assertRaises(ValueError):import_project_media(self.db,self.workspace,[self.good],project_id='c'*32,kind='asset')
        self.db.execute('UPDATE projects SET id=id')
        with self.assertRaises(RuntimeError):import_project_media(self.db,self.workspace,[self.good],project_id=self.ids[0],kind='asset')
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
    def test_rejects_extension_without_db_side_effects(self):
        b=import_project_media(self.db,self.workspace,[self.good],project_id=self.ids[0],kind='asset',allowed_extensions={'.pdf'})
        self.assertFalse(b.receipts);self.assertEqual(len(b.failures),1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM assets').fetchone()[0],0)

if __name__=='__main__':unittest.main()
