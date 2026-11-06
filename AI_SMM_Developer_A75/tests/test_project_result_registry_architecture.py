import sqlite3
import tempfile
import unittest
from pathlib import Path
from project_result_registry import ProjectResultRegistry

class ProjectResultRegistryTests(unittest.TestCase):
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
        self.registry = ProjectResultRegistry(self.db, lambda p:self.root/p)

    def test_messages_are_isolated(self):
        self.registry.admit_message('one','first')
        self.registry.admit_message('two','second')
        self.assertEqual(self.db.execute('SELECT project_id,content FROM messages ORDER BY id').fetchall(),[('one','first'),('two','second')])

    def test_missing_project_cannot_write(self):
        with self.assertRaises(ValueError):self.registry.admit_message('missing','text')
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)

    def test_result_is_idempotent_and_project_scoped(self):
        video=self.root/'one'/'processed'/'clip.mp4'
        video.write_bytes(b'valid data')
        first=self.registry.admit_video('one',video)
        self.assertEqual(first,self.registry.admit_video('one',video))
        self.assertEqual(self.db.execute('SELECT count(*) FROM project_files').fetchone()[0],1)
        with self.assertRaises(ValueError):self.registry.admit_video('two',video)

    def test_reject_outside_or_empty_video(self):
        path=self.root/'one'/'processed'/'empty.mp4'
        path.touch()
        with self.assertRaises(ValueError):self.registry.admit_video('one',path)
        path.write_bytes(b'x')
        with self.assertRaises(ValueError):self.registry.admit_video('two',path)

    def test_outer_transaction_preserved_on_error(self):
        self.db.execute('INSERT INTO messages(project_id,role,content,created_at) VALUES(?,?,?,?)',('one','user','outer','now'))
        with self.assertRaises(ValueError):self.registry.admit_video('one',self.root/'outside.mp4')
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone()[0],1)

    def test_sql_failure_rolls_back_registration_only(self):
        self.db.execute("CREATE TRIGGER deny_message BEFORE INSERT ON messages BEGIN SELECT RAISE(ABORT,'denied'); END")
        with self.assertRaises(sqlite3.DatabaseError):self.registry.admit_message('one','foo')
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)

    def test_publish_commits_durable_message(self):
        self.registry.publish_message('one','saved')
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT content FROM messages').fetchone()[0], 'saved')

    def test_publish_refuses_unrelated_transaction(self):
        self.db.execute('INSERT INTO messages(project_id,role,content,created_at) VALUES(?,?,?,?)',('two','user','pending','now'))
        with self.assertRaises(RuntimeError): self.registry.publish_message('one','foreign')
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT content FROM messages').fetchall(), [('pending',)])
        self.db.rollback()

    def test_publish_rolls_back_when_sqlite_denies(self):
        self.db.execute("CREATE TRIGGER deny_message BEFORE INSERT ON messages BEGIN SELECT RAISE(ABORT,'denied'); END")
        self.db.commit()
        with self.assertRaises(sqlite3.DatabaseError): self.registry.publish_message('one','nope')
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)

    def test_replayed_video_registration_survives_commit(self):
        video=self.root/'one'/'processed'/'clip.mp4';video.write_bytes(b'video')
        a=self.registry.publish_video('one',video)
        b=self.registry.publish_video('one',video)
        self.assertEqual(a,b)
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT count(*) FROM project_files').fetchone()[0],1)

if __name__=='__main__':unittest.main()
