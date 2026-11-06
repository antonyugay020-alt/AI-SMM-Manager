import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from project_text_projection import register, drain

class TextProjectionContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE project_files (id TEXT, project_id TEXT,kind TEXT,display_name TEXT,path TEXT,created_at TEXT)')
        self.db.commit()
        self.pid=uuid.uuid4().hex
        self.fid=uuid.uuid4().hex
        self.root=Path(self.tmp.name)/self.pid
        self.root.mkdir()
        self.target=self.root/'texts'/(self.fid+'.txt')

    def registered(self):
        self.db.execute('BEGIN IMMEDIATE')
        register(self.db,file_id=self.fid,project_id=self.pid,filename=self.fid+'.txt',
                 content='durable content',full_path=self.target,timestamp='now')
        self.db.commit()

    def replay(self):
        return drain(self.db,lambda project: self.root if project==self.pid else self.fail('other project'),project_id=self.pid)

    def test_recover_after_commit_and_idempotence(self):
        self.registered()
        self.assertFalse(self.target.exists())
        self.assertEqual(self.replay()['completed'],1)
        self.assertEqual(self.target.read_text(encoding='utf-8'),'durable content')
        self.assertEqual(self.replay()['completed'],0)

    def test_rollback_cancels_both_records(self):
        self.db.execute('BEGIN IMMEDIATE')
        register(self.db,file_id=self.fid,project_id=self.pid,filename=self.fid+'.txt',
                 content='durable content',full_path=self.target,timestamp='now')
        self.db.rollback()
        self.assertEqual(self.replay()['pending'],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM project_files').fetchone()[0],0)

    def test_conflict_remains_pending_without_overwrite(self):
        self.registered()
        self.target.parent.mkdir()
        self.target.write_text('unrelated', encoding='utf-8')
        self.assertEqual(self.replay()['failed'],1)
        self.assertEqual(self.target.read_text(encoding='utf-8'),'unrelated')
        self.assertEqual(self.replay()['pending'],1)

    def test_tampered_sql_payload_does_not_publish(self):
        self.registered()
        self.db.execute("UPDATE project_text_projections SET content='tampered'")
        self.db.commit()
        self.assertEqual(self.replay()['failed'],1)
        self.assertFalse(self.target.exists())

if __name__=='__main__':unittest.main()
