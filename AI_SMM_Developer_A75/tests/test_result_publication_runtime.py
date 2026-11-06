import sqlite3
import tempfile
import unittest
from pathlib import Path
from result_publication_runtime import ResultPublicationRuntime

class ResultPublicationRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, role TEXT, content TEXT, created_at TEXT)')
        self.db.execute('CREATE TABLE project_files(id TEXT PRIMARY KEY, project_id TEXT, kind TEXT, display_name TEXT, path TEXT, created_at TEXT)')
        self.db.executemany('INSERT INTO projects VALUES(?)',[('one',),('two',)])
        self.db.commit()
        for pid in ('one','two'):
            (self.root/pid/'processed').mkdir(parents=True)
        self.runtime = ResultPublicationRuntime(self.db, lambda pid:self.root/pid)

    def test_publish_message(self):
        receipt=self.runtime.publish('one','message','hello',active_project_id='one')
        self.assertEqual((receipt.project_id,receipt.kind),('one','message'))
        self.assertFalse(self.db.in_transaction)

    def test_publish_video_idempotent(self):
        video=self.root/'one'/'processed'/'clip.mp4';video.write_bytes(b'video')
        a=self.runtime.publish('one','video',video,active_project_id='one')
        b=self.runtime.publish('one','video',video,active_project_id='one')
        self.assertEqual(a.record_id,b.record_id)

    def test_reject_cross_project(self):
        with self.assertRaises(ValueError):
            self.runtime.publish('two','message','foreign',active_project_id='one')
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)

    def test_reject_foreign_transaction_preserving_writes(self):
        self.db.execute("INSERT INTO messages(project_id,role,content) VALUES ('one','user','previous')")
        with self.assertRaises(RuntimeError):self.runtime.publish('one','message','later',active_project_id='one')
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone()[0],1)
        self.db.rollback()

    def test_failed_sql_rolls_back(self):
        self.db.execute("CREATE TRIGGER deny BEFORE INSERT ON messages BEGIN SELECT RAISE(ABORT,'deny'); END")
        self.db.commit()
        with self.assertRaises(sqlite3.DatabaseError):self.runtime.publish('one','message','oops',active_project_id='one')
        self.assertFalse(self.db.in_transaction)

    def test_unsupported_kind_leaves_no_transaction(self):
        with self.assertRaises(ValueError):self.runtime.publish('one','unrecognized','x')
        self.assertFalse(self.db.in_transaction)

    def test_invalid_receipt_rejected_before_write(self):
        with self.assertRaises(ValueError):self.runtime.publish('one','message','x',receipt='invalid',active_project_id='one')
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)

if __name__ == '__main__': unittest.main()

class ResultPublicationReplayTests(ResultPublicationRuntimeTests):
    def test_message_replay_does_not_duplicate(self):
        a=self.runtime.publish('one','message','x',operation_id='op-1')
        b=self.runtime.publish('one','message','x',operation_id='op-1')
        self.assertEqual(str(a.record_id),str(b.record_id))
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone()[0],1)

    def test_reuse_op_for_different_payload_fails(self):
        self.runtime.publish('one','message','x',operation_id='op-1')
        with self.assertRaises(ValueError):self.runtime.publish('one','message','y',operation_id='op-1')
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone()[0],1)

    def test_reuse_op_for_different_project_fails(self):
        self.runtime.publish('one','message','x',operation_id='op-1')
        with self.assertRaises(ValueError):self.runtime.publish('two','message','x',operation_id='op-1')
