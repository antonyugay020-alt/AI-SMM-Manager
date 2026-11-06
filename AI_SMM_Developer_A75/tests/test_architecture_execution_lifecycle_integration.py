"""Integration across intake -> task -> attempt -> completion, no worker/network."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from application_bootstrap import open_application_database
from application_core import ApplicationRuntime
from dialogue_runtime import DialogueContract
from dialogue_execution import compile_execution
from sqlite_transaction_core import own
from test_r86_semantic_dialogue import decision

class ExecutionGraphIntegration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.db=open_application_database(self.root/'mgr.sqlite',self.root)
        self.addCleanup(self.db.close)
        self.db.executemany("INSERT INTO projects(id,name,created_at,category,description) VALUES(?,?,?,'','')",
            [('alpha','A','now'),('beta','B','now')]);self.db.commit()
        self.app=ApplicationRuntime(self.db,lambda pid:self.root/pid)

    def _plan(self,project,request):
        intake=self.app.conversations.accept(project,request)
        snap=self.app.conversations.snapshot(project,current_message_id=intake.message_id)
        plan=DialogueContract(request,snap,turn_id=intake.dialogue_turn_id).validate(
            decision('create_text',artifact_kind='plans'))
        query,context=compile_execution(self.db,plan,self.root/project)
        self.app.conversations.transition(project,intake.dialogue_turn_id,'resolving')
        return intake,plan,query,context

    def test_single_commit_of_task_attempt_and_dialogue(self):
        intake,plan,req,ctx=self._plan('alpha','First task')
        claim,receipt=self.app.start_dialogue_execution(plan,req,ctx)
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',
            (receipt.task_id,)).fetchone(),('active',))
        self.assertEqual(self.db.execute('SELECT state FROM execution_attempts WHERE attempt_id=?',
            (receipt.attempt.attempt_id,)).fetchone(),('running',))
        self.assertEqual(self.db.execute('SELECT task_id FROM ai_dialogue_task_links WHERE dialogue_turn_id=?',
            (intake.dialogue_turn_id,)).fetchone(),(receipt.task_id,))
        with own(self.db):
            self.app.executions.finish(receipt,req)
        self.assertEqual(self.db.execute('SELECT state FROM ai_task_intake WHERE dialogue_turn_id=?',
            (intake.dialogue_turn_id,)).fetchone(),('resolved',))
        self.assertEqual(self.db.execute('SELECT state FROM execution_attempts WHERE attempt_id=?',
            (receipt.attempt.attempt_id,)).fetchone(),('accepted',))

    def test_failed_attempt_allocation_rolls_back_entire_launch(self):
        intake,plan,req,ctx=self._plan('alpha','Original task')
        self.db.execute("""CREATE TRIGGER reject_attempt BEFORE INSERT ON execution_attempts
            BEGIN SELECT RAISE(ABORT,'injected'); END""");self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):self.app.start_dialogue_execution(plan,req,ctx)
        self.assertEqual(self.db.execute('SELECT count(*) FROM ai_task_registry').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM ai_dialogue_task_links').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM ai_task_contexts').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT state FROM ai_task_intake WHERE dialogue_turn_id=?',
            (intake.dialogue_turn_id,)).fetchone(),('resolving',))

    def test_two_projects_have_independent_worker_attempts(self):
        first=self._plan('alpha','Same text')
        second=self._plan('beta','Same text')
        a=self.app.start_dialogue_execution(first[1],first[2],first[3])[1]
        b=self.app.start_dialogue_execution(second[1],second[2],second[3])[1]
        self.assertNotEqual(a.task_id,b.task_id)
        self.app.executions.authorize_result(a,active_project_id='alpha')
        with self.assertRaises(ValueError):self.app.executions.authorize_result(a,active_project_id='beta')
        with own(self.db):self.app.executions.finish(a,first[2])
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',
            (b.task_id,)).fetchone(),('active',))

    def test_existing_uncommitted_change_must_not_be_committed_by_launch(self):
        intake,plan,req,ctx=self._plan('alpha','Another task')
        self.db.execute("UPDATE projects SET name='uncommitted' WHERE id='beta'")
        with self.assertRaises(RuntimeError):self.app.start_dialogue_execution(plan,req,ctx)
        self.db.rollback()
        self.assertEqual(self.db.execute("SELECT name FROM projects WHERE id='beta'").fetchone()[0],'B')
