"""Actual SQLite project/media integration, including rollback and crash replay."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from conversation_runtime import ConversationRuntime
from result_publication_runtime import ResultPublicationRuntime


class DialogueMediaAtomicTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.db = sqlite3.connect(self.root / 'system.sqlite3')
        self.addCleanup(self.db.close)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY,project_id TEXT,role TEXT,content TEXT,created_at TEXT)')
        self.db.execute('CREATE TABLE project_files(id TEXT PRIMARY KEY,project_id TEXT,kind TEXT,display_name TEXT,path TEXT,created_at TEXT)')
        self.db.executemany('INSERT INTO projects VALUES(?)', [('one',),('two',)])
        self.db.commit()
        for pid in ('one','two'):
            (self.root / pid / 'processed').mkdir(parents=True)
        self.conversations = ConversationRuntime(self.db)
        self.conversations.accept('one','Create a new image',turn_id='turn-one')
        self.conversations.transition('one','turn-one','resolving')
        self.runtime = ResultPublicationRuntime(self.db,lambda pid:self.root / pid)
        self.photo = self.root / 'one' / 'processed' / 'image.png'
        Image.new('RGB',(4,4)).save(self.photo)

    def publish(self,kind='image',path=None,operation_id='one:turn-one:image',project='one',turn='turn-one'):
        return self.runtime.publish(project,kind,path or self.photo,active_project_id=project,
            operation_id=operation_id,dialogue_turn_id=turn)

    def intake_state(self):
        return self.db.execute("SELECT state FROM ai_task_intake WHERE dialogue_turn_id='turn-one'").fetchone()[0]

    def test_image_and_dialogue_commit_together(self):
        receipt=self.publish()
        self.assertEqual('resolved',self.intake_state())
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(('result',receipt.record_id),self.db.execute(
            "SELECT kind,id FROM project_files WHERE project_id='one'").fetchone())

    def test_repeated_callback_does_not_duplicate(self):
        first=self.publish()
        second=self.publish()
        self.assertEqual(first,second)
        self.assertEqual(1,self.db.execute('SELECT count(*) FROM project_files').fetchone()[0])
        self.assertEqual(1,self.db.execute('SELECT count(*) FROM result_publication_events').fetchone()[0])

    def test_failed_intake_commit_rolls_back_artifact_and_event(self):
        self.db.execute("""CREATE TRIGGER deny AFTER UPDATE ON ai_task_intake
            WHEN NEW.state='resolved' BEGIN SELECT RAISE(ABORT,'crash'); END""")
        self.db.commit()
        with self.assertRaises(sqlite3.DatabaseError):self.publish()
        self.assertEqual('resolving',self.intake_state())
        self.assertEqual(0,self.db.execute('SELECT count(*) FROM project_files').fetchone()[0])
        # New outbox schema itself is rolled back when publication fails.
        tables=self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='result_publication_events'").fetchall()
        self.assertEqual([],tables)
        self.assertFalse(self.db.in_transaction)

    def test_corrupt_image_never_closes_receipt(self):
        self.photo.write_bytes(b'not a real image')
        with self.assertRaises(ValueError):self.publish()
        self.assertEqual('resolving',self.intake_state())
        self.assertEqual(0,self.db.execute('SELECT count(*) FROM project_files').fetchone()[0])

    def test_cross_project_attempt_cannot_close_other_turn(self):
        with self.assertRaises(ValueError):self.publish(project='two')
        self.assertEqual('resolving',self.intake_state())
        self.assertEqual(0,self.db.execute('SELECT count(*) FROM project_files').fetchone()[0])

    def test_video_uses_same_transactional_contract(self):
        clip=self.root/'one'/'processed'/'clip.mp4'
        clip.write_bytes(b'synthetic test video bytes')
        result=self.publish(kind='video',path=clip,operation_id='one:turn-one:video')
        self.assertEqual('resolved',self.intake_state())
        self.assertEqual('video',self.db.execute('SELECT kind FROM project_files WHERE id=?',(result.record_id,)).fetchone()[0])

    def test_database_reopen_preserves_idempotent_publication(self):
        first=self.publish()
        again=sqlite3.connect(self.root/'system.sqlite3')
        self.addCleanup(again.close)
        after_restart=ResultPublicationRuntime(again,lambda pid:self.root/pid)
        second=after_restart.publish('one','image',self.photo,active_project_id='one',
            operation_id='one:turn-one:image',dialogue_turn_id='turn-one')
        self.assertEqual(first,second)
        self.assertEqual(1,again.execute('SELECT count(*) FROM project_files').fetchone()[0])

    def test_unauthorized_foreign_transaction_kept(self):
        self.db.execute("INSERT INTO messages VALUES(99,'two','user','unrelated','now')")
        with self.assertRaises(RuntimeError):self.publish()
        self.assertTrue(self.db.in_transaction)
        self.assertEqual('resolving',self.intake_state())
        self.db.rollback()

if __name__=='__main__':unittest.main()
