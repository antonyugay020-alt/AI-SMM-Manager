"""Cross-service lifecycle checks: no model decision is a completed assignment."""
import sqlite3
import unittest
from conversation_runtime import ConversationRuntime
from task_lifecycle import TaskLifecycle
from sqlite_transaction_core import own


class DialogueTaskCommitIntegration(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE projects(id TEXT PRIMARY KEY);
            CREATE TABLE messages(id INTEGER PRIMARY KEY,project_id TEXT,role TEXT,content TEXT,created_at TEXT);
            CREATE TABLE ai_pending_tasks(project_id TEXT PRIMARY KEY,request TEXT,question TEXT,revision_id TEXT);
            INSERT INTO projects VALUES ('one');
            INSERT INTO projects VALUES ('two');
        ''')
        self.conversations=ConversationRuntime(self.db)
        self.tasks=TaskLifecycle(self.db)

    def tearDown(self): self.db.close()

    def start(self,project='one',turn='turn-1',request='same instructions',task_id=None):
        self.conversations.accept(project,request,turn_id=turn)
        self.conversations.transition(project,turn,'resolving')
        with own(self.db):
            ident=self.tasks.activate(project,request,'Running',task_id=task_id)
            self.conversations.link_execution_in_transaction(project,turn,ident)
        return ident

    def state(self,project='one',turn='turn-1'):
        return self.db.execute('''SELECT state FROM ai_task_intake
            WHERE project_id=? AND dialogue_turn_id=?''',(project,turn)).fetchone()[0]

    def test_launch_does_not_complete_receipt(self):
        self.start()
        self.assertEqual('resolving',self.state())
        self.assertEqual(1,len(self.conversations.unresolved(project_id='one')))

    def test_verified_task_completion_closes_both_in_one_commit(self):
        task=self.start()
        with own(self.db): self.tasks.finish('one','same instructions',task_id=task)
        self.assertEqual('resolved',self.state())
        self.assertEqual((),self.conversations.unresolved(project_id='one'))

    def test_multiple_followups_refer_to_one_task(self):
        task=self.start()
        self.start(turn='turn-2',task_id=task)
        self.assertEqual('resolving',self.state(turn='turn-2'))
        with own(self.db): self.tasks.finish('one','same instructions',task_id=task)
        self.assertEqual('resolved',self.state())
        self.assertEqual('resolved',self.state(turn='turn-2'))

    def test_cancel_active_is_terminal_and_scoped(self):
        task=self.start()
        other=self.start(project='two',turn='foreign-turn')
        with own(self.db):self.tasks.finish('one','same instructions',task_id=task,status='cancelled')
        self.assertEqual('resolved',self.state())
        self.assertEqual('resolving',self.state('two','foreign-turn'))
        self.assertNotEqual(task,other)

    def test_cancel_paused_closes_only_old_turn(self):
        first=self.start()
        second=self.start(turn='turn-2',request='new instructions')
        self.assertNotEqual(first,second)
        with own(self.db):self.tasks.cancel_paused('one',first,'same instructions')
        self.assertEqual('resolved',self.state())
        self.assertEqual('resolving',self.state(turn='turn-2'))

    def test_duplicate_binding_idempotent_and_cross_task_fails(self):
        task=self.start()
        with own(self.db):
            self.assertFalse(self.conversations.link_execution_in_transaction('one','turn-1',task))
        self.start(turn='turn-2',request='new instructions')
        second=self.db.execute("SELECT task_id FROM ai_task_registry WHERE request='new instructions'").fetchone()[0]
        with self.assertRaises(ValueError):
            with own(self.db):
                self.conversations.link_execution_in_transaction('one','turn-1',second)
        self.assertEqual('resolving',self.state())

    def test_foreign_project_and_turn_rejected(self):
        task=self.start()
        with self.assertRaises(ValueError):
            with own(self.db):
                self.conversations.link_execution_in_transaction('two','turn-1',task)
        with self.assertRaises(ValueError):
            with own(self.db):
                self.conversations.link_execution_in_transaction('one','not-a-turn',task)

    def test_transition_failure_rolls_back_task_completion(self):
        task=self.start()
        self.db.execute('''CREATE TRIGGER deny_completion BEFORE UPDATE ON ai_task_intake
            WHEN NEW.state='resolved' BEGIN SELECT RAISE(ABORT,'intake unavailable'); END''')
        self.db.commit()
        with self.assertRaises(sqlite3.DatabaseError):
            with own(self.db):self.tasks.finish('one','same instructions',task_id=task)
        self.assertEqual('resolving',self.state())
        self.assertEqual('active',self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(task,)).fetchone()[0])
        self.assertEqual(('same instructions',),self.db.execute('SELECT request FROM ai_pending_tasks WHERE project_id=?',('one',)).fetchone())

    def test_reopen_keeps_resolution_link(self):
        task=self.start()
        script='\n'.join(self.db.iterdump())
        reopened=sqlite3.connect(':memory:')
        try:
            reopened.executescript(script)
            with own(reopened):TaskLifecycle(reopened).finish('one','same instructions',task_id=task)
            self.assertEqual(('resolved',),reopened.execute("SELECT state FROM ai_task_intake WHERE dialogue_turn_id='turn-1'").fetchone())
        finally:reopened.close()


if __name__=='__main__':unittest.main()
