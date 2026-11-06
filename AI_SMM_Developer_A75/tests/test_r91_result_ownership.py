"""Post-execution state ownership and durable result transactions."""
import json,sqlite3,tempfile,unittest
from pathlib import Path
import test_r86_semantic_dialogue as common
from dialogue_runtime import DialogueContract,dialogue_snapshot
from dialogue_execution import (compile_execution,persist_execution,validate_live_plan,
                               ProjectConnection,result_transaction)

class ResultOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.fixture=common.SemanticDialogueTests();self.fixture.setUp();self.db=self.fixture.db
    def tearDown(self):self.db.close()
    def claim(self):
        c=DialogueContract('New request',dialogue_snapshot(self.db,'a'))
        p=c.validate(common.decision('create_text',artifact_kind='plans'))
        request,state=compile_execution(self.db,p,'/tmp/unused')
        return persist_execution(self.db,p,request,state)
    def test_claim_represents_post_start_state(self):
        c=self.claim();validate_live_plan(self.db,'a',c)
        self.assertEqual(c.snapshot['sources']['pending']['request'],'New request')
        self.assertIsNotNone(c.snapshot['sources']['pending']['execution_context_sha256'])
    def test_new_pending_during_generation_rejects_old_result(self):
        c=self.claim();self.db.execute("UPDATE ai_pending_tasks SET request='New task'");self.db.commit()
        with self.assertRaises(ValueError):validate_live_plan(self.db,'a',c)
    def test_cancelled_pending_rejects_late_result(self):
        c=self.claim();self.db.execute('DELETE FROM ai_pending_tasks');self.db.commit()
        with self.assertRaises(ValueError):validate_live_plan(self.db,'a',c)
    def test_manifest_change_after_start_rejects_result(self):
        c=self.claim();self.db.execute("UPDATE ai_task_contexts SET context_json='{}'");self.db.commit()
        with self.assertRaises(ValueError):validate_live_plan(self.db,'a',c)
    def test_same_request_other_turn_cannot_steal_claim(self):
        c=self.claim();r=self.db.execute('SELECT context_json FROM ai_task_contexts').fetchone()[0]
        m=json.loads(r);m['dialogue_turn_id']='different'
        self.db.execute('UPDATE ai_task_contexts SET context_json=?',(json.dumps(m),));self.db.commit()
        with self.assertRaises(ValueError):validate_live_plan(self.db,'a',c)
    def test_project_changed_during_generation_rejects_result(self):
        c=self.claim();self.db.execute("UPDATE projects SET description='Changed facts' WHERE id='a'");self.db.commit()
        with self.assertRaises(ValueError):validate_live_plan(self.db,'a',c)
    def test_new_question_during_generation_rejects_old_result(self):
        c=self.claim();self.db.execute("INSERT INTO ai_dialogue_questions(project_id,request,question) VALUES('a','Other','Q')");self.db.commit()
        with self.assertRaises(ValueError):validate_live_plan(self.db,'a',c)
    def test_selected_document_changed_during_generation_rejects_result(self):
        from stage57_core import receipt
        kind,body=self.db.execute("SELECT kind,content FROM manager_documents WHERE id='doc'").fetchone()
        receipt(self.db,'a','Original request',kind,body,'saved',json.dumps({'artifact_id':'doc'}))
        snapshot=dialogue_snapshot(self.db,'a')
        plan=DialogueContract('Change the saved material',snapshot).validate(common.decision(
            'revise_saved',artifact_kind=kind,target_ids=['doc'],
            source_id=snapshot['documents']['doc']['source_id'],include_current_instruction=True))
        request,state=compile_execution(self.db,plan,'/tmp/unused')
        claim=persist_execution(self.db,plan,request,state)
        self.db.execute("UPDATE manager_documents SET content='New manual version' WHERE id='doc'");self.db.commit()
        with self.assertRaises(ValueError):validate_live_plan(self.db,'a',claim)
    def test_inner_commit_does_not_make_partial_result_durable(self):
        with tempfile.TemporaryDirectory() as home:
            p=Path(home)/'db';db=sqlite3.connect(p,factory=ProjectConnection);other=sqlite3.connect(p,timeout=.01)
            db.execute('CREATE TABLE t(x TEXT)');db.commit()
            try:
                with result_transaction(db):
                    db.execute("INSERT INTO t VALUES('one')");db.commit()
                    self.assertEqual(other.execute('SELECT COUNT(*) FROM t').fetchone()[0],0)
                self.assertEqual(other.execute('SELECT COUNT(*) FROM t').fetchone()[0],1)
            finally:db.close();other.close()
    def test_later_failure_rolls_back_all_helpers_despite_commits(self):
        db=sqlite3.connect(':memory:',factory=ProjectConnection);db.execute('CREATE TABLE t(x TEXT)');db.commit()
        try:
            with self.assertRaises(RuntimeError):
                with result_transaction(db):
                    db.execute("INSERT INTO t VALUES('one')");db.commit()
                    db.execute("INSERT INTO t VALUES('two')");db.commit();raise RuntimeError('Late failure')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM t').fetchone()[0],0)
            self.assertEqual(db._result_depth,0)
        finally:db.close()
    def test_write_lock_covers_the_whole_acceptance(self):
        with tempfile.TemporaryDirectory() as home:
            p=Path(home)/'db';db=sqlite3.connect(p,factory=ProjectConnection);other=sqlite3.connect(p,timeout=.01)
            db.execute('CREATE TABLE t(x TEXT)');db.commit()
            try:
                with result_transaction(db):
                    db.execute("INSERT INTO t VALUES('one')");db.commit()
                    with self.assertRaises(sqlite3.OperationalError):other.execute("INSERT INTO t VALUES('two')")
                    other.rollback()
                other.execute("INSERT INTO t VALUES('two')");other.commit()
                self.assertEqual(db.execute('SELECT COUNT(*) FROM t').fetchone()[0],2)
            finally:db.close();other.close()
    def test_actual_commit_error_rolls_back_result(self):
        db=sqlite3.connect(':memory:',factory=ProjectConnection);db.execute('PRAGMA foreign_keys=ON')
        db.executescript('CREATE TABLE parent(id INTEGER PRIMARY KEY); CREATE TABLE child(id INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED);')
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                with result_transaction(db):db.execute('INSERT INTO child VALUES(999)');db.commit()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM child').fetchone()[0],0)
            self.assertFalse(db.in_transaction);self.assertEqual(db._result_depth,0)
        finally:db.close()
    def test_ambient_uncommitted_state_cannot_be_reported_as_durable(self):
        db=sqlite3.connect(':memory:',factory=ProjectConnection);db.execute('CREATE TABLE t(x)');db.execute('INSERT INTO t VALUES(1)')
        try:
            with self.assertRaises(ValueError):
                with result_transaction(db):pass
            self.assertTrue(db.in_transaction)
        finally:db.close()

if __name__=='__main__':unittest.main()
