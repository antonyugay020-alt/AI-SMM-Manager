import sqlite3
import tempfile
import unittest
from pathlib import Path
from project_runtime import ProjectRuntime

class RuntimeArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,created_at TEXT,category TEXT,description TEXT)')
        self.db.execute('CREATE TABLE project_locations(project_id TEXT PRIMARY KEY,folder TEXT NOT NULL)');self.db.commit()
        self.runtime=ProjectRuntime(self.db)
    def create(self,name):
        return self.runtime.create(self.temp.name,name,'category','desc','now')
    def test_list_does_not_create_directories(self):
        ident='a'*32
        self.db.execute('INSERT INTO projects VALUES(?,?,?,?,?)',(ident,'lost','now','',''))
        self.db.execute('INSERT INTO project_locations VALUES(?,?)',(ident,str(Path(self.temp.name)/ident)))
        self.db.commit()
        self.assertEqual(len(self.runtime.list_projects()),1)
        self.assertFalse((Path(self.temp.name)/ident).exists())
    def test_activation_reconciles_missing_workspace(self):
        ident=self.create('alpha');root=Path(self.temp.name)/ident
        (root/'texts').rmdir()
        self.runtime.activate(ident)
        self.assertTrue((root/'texts').is_dir())
    def test_list_is_deterministic_without_reconciliation(self):
        self.create('one');self.create('two')
        self.assertEqual({r.name for r in self.runtime.list_projects()},{'one','two'})
    def test_recovery_is_bounded_and_resumable(self):
        for i in range(9): self.create(str(i))
        ids=[];cursor=None
        while True:
            batch=self.runtime.recover_batch(cursor=cursor,batch_size=2)
            ids.extend(batch.succeeded)
            self.assertLessEqual(len(batch.succeeded),2)
            if not batch.next_cursor: break
            cursor=batch.next_cursor
        self.assertEqual(len(ids),9);self.assertEqual(len(set(ids)),9)
    def test_missing_project_fails_before_active_switch(self):
        with self.assertRaises(ValueError): self.runtime.activate('f'*32)
    def test_batch_does_not_stop_on_one_project_failure(self):
        ids=[self.create(str(i)) for i in range(3)]
        broken=sorted(ids)[0];path=Path(self.temp.name)/broken/'texts'
        path.rmdir();path.write_text('occupied', encoding='utf-8')
        result=self.runtime.recover_batch(batch_size=32)
        self.assertIn(broken,result.errors)
        self.assertEqual(len(result.succeeded),2)
    def test_transaction_guard(self):
        self.db.execute('BEGIN IMMEDIATE')
        with self.assertRaises(RuntimeError): self.runtime.recover_batch()
        self.db.rollback()
    def test_invalid_batch_size(self):
        with self.assertRaises(ValueError): self.runtime.recover_batch(batch_size=100000)
if __name__=='__main__':unittest.main()
