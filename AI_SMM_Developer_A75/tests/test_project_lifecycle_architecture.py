import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from project_lifecycle import register_project,reconcile_project,reconcile_projects,PROJECT_FOLDERS

class ProjectLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,created_at TEXT,category TEXT,description TEXT)')
        self.db.execute('CREATE TABLE project_locations(project_id TEXT PRIMARY KEY,folder TEXT)');self.db.commit()
    def create(self,**kwargs):
        return register_project(self.db,Path(self.tmp.name),'Name','General','Description','date',**kwargs)
    def test_recovery_replays_every_subfolder(self):
        pid=self.create();base=Path(self.tmp.name)/pid
        self.assertFalse(base.exists());self.assertEqual(reconcile_project(self.db,pid),base)
        self.assertTrue(all((base/x).is_dir() for x in PROJECT_FOLDERS))
        self.assertEqual(reconcile_project(self.db,pid),base)
    def test_existing_files_are_never_deleted(self):
        pid=self.create();base=reconcile_project(self.db,pid);(base/'texts'/'owned.txt').write_text('important')
        reconcile_projects(self.db);self.assertEqual((base/'texts'/'owned.txt').read_text(),'important')
    def test_registration_rolls_back_on_duplicate(self):
        pid=self.create();self.assertRaises(sqlite3.IntegrityError,self.create,project_id=pid)
        self.assertFalse(self.db.in_transaction)
    def test_projects_are_isolated(self):
        a=self.create();b=self.create();self.assertNotEqual(a,b)
        self.assertEqual(reconcile_projects(self.db)['completed'],2)
    def test_occupied_directory_is_rejected_before_registration(self):
        pid='a'*32;(Path(self.tmp.name)/pid).mkdir()
        self.assertRaises(FileExistsError,self.create,project_id=pid)
        self.assertEqual(self.db.execute('SELECT count(*) FROM projects').fetchone()[0],0)
    def test_non_registered_project_rejected(self):
        self.assertRaises(ValueError,reconcile_project,self.db,'f'*32)
if __name__=='__main__':unittest.main()
