import sqlite3
import unittest
from conversation_runtime import ConversationRuntime

class DialoguePublicationAtomicTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''CREATE TABLE projects(id TEXT PRIMARY KEY);
        CREATE TABLE messages(id INTEGER PRIMARY KEY,project_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,created_at TEXT NOT NULL);
        INSERT INTO projects VALUES('a'); INSERT INTO projects VALUES('b');''')
        self.core=ConversationRuntime(self.db)
        self.receipt=self.core.accept('a','instruction',turn_id='turn-a')
        self.core.transition('a','turn-a','resolving')

    def tearDown(self): self.db.close()

    def test_atomic_and_retry(self):
        identifier=self.core.publish_reply('a','turn-a','answer')
        self.assertEqual(identifier,self.core.publish_reply('a','turn-a','answer'))
        self.assertEqual(1,self.db.execute("SELECT count(*) FROM messages WHERE role='assistant'").fetchone()[0])
        self.assertEqual('resolved',self.db.execute("SELECT state FROM ai_task_intake WHERE dialogue_turn_id='turn-a'").fetchone()[0])

    def test_failed_transition_rolls_back_response(self):
        self.db.execute('''CREATE TRIGGER prevent_completion BEFORE UPDATE ON ai_task_intake
        WHEN NEW.state='resolved' BEGIN SELECT RAISE(ABORT,'failure'); END;''')
        with self.assertRaises(sqlite3.DatabaseError):self.core.publish_reply('a','turn-a','answer')
        self.assertEqual(0,self.db.execute("SELECT count(*) FROM messages WHERE role='assistant'").fetchone()[0])
        self.assertEqual('resolving',self.db.execute("SELECT state FROM ai_task_intake WHERE dialogue_turn_id='turn-a'").fetchone()[0])

    def test_cross_project_rejected(self):
        with self.assertRaises(ValueError):self.core.publish_reply('b','turn-a','answer')
        self.assertEqual(0,self.db.execute("SELECT count(*) FROM messages WHERE role='assistant'").fetchone()[0])

    def test_conflicting_retry_rejected(self):
        self.core.publish_reply('a','turn-a','answer')
        with self.assertRaises(ValueError):self.core.publish_reply('a','turn-a','different')
        self.assertEqual(1,self.db.execute("SELECT count(*) FROM messages WHERE role='assistant'").fetchone()[0])

    def test_clarification_is_atomic_with_response(self):
        self.db.execute('''CREATE TABLE ai_dialogue_questions(project_id TEXT PRIMARY KEY,request TEXT NOT NULL,
            question TEXT NOT NULL,plan_json TEXT)''')
        self.core.publish_reply('a','turn-a','What channel?',clarification=('original','{"operation":"clarify"}'))
        self.assertEqual(('original','What channel?'),self.db.execute(
            "SELECT request,question FROM ai_dialogue_questions WHERE project_id='a'").fetchone())
        self.assertEqual('resolved',self.db.execute(
            "SELECT state FROM ai_task_intake WHERE dialogue_turn_id='turn-a'").fetchone()[0])

    def test_clarification_rollback_on_commit_failure(self):
        self.db.execute('''CREATE TABLE ai_dialogue_questions(project_id TEXT PRIMARY KEY,request TEXT NOT NULL,
            question TEXT NOT NULL,plan_json TEXT)''')
        self.db.execute('''CREATE TRIGGER prevent_completion BEFORE UPDATE ON ai_task_intake
            WHEN NEW.state='resolved' BEGIN SELECT RAISE(ABORT,'failure'); END;''')
        with self.assertRaises(sqlite3.DatabaseError):
            self.core.publish_reply('a','turn-a','What channel?',clarification=('original','{}'))
        self.assertIsNone(self.db.execute('SELECT * FROM ai_dialogue_questions').fetchone())
        self.assertEqual(0,self.db.execute("SELECT count(*) FROM messages WHERE role='assistant'").fetchone()[0])

    def test_cannot_publish_before_resolving(self):
        self.core.accept('b','later',turn_id='turn-b')
        with self.assertRaises(ValueError):self.core.publish_reply('b','turn-b','answer')

if __name__=='__main__':unittest.main()
