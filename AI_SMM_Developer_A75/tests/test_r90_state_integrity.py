"""Independent source/state invariants for the ten-pass audit."""
import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import test_r86_semantic_dialogue as common
from dialogue_runtime import DialogueContract,dialogue_snapshot,understand_dialogue,digest
from dialogue_execution import compile_execution,persist_execution,validate_live_plan,cancel_pending
from evidence_context import build_evidence_context,coerce_evidence_context
from text_task_contract import TextTaskContract
from task_artifacts import verified_document,original_document_request
from stage57_core import receipt


class StateIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.f=common.SemanticDialogueTests('test_selected_project_isolation');self.f.setUp();self.db=self.f.db
        self.db.commit()
    def tearDown(self):self.f.tearDown()
    def plan(self,**kw):return DialogueContract('Current user task',dialogue_snapshot(self.db,'a')).validate(common.decision(**kw))
    def test_duplicate_source_ids_rejected_even_if_one_matches_request(self):
        value=build_evidence_context('Task').to_dict();other=copy.deepcopy(value['sources'][0]);other['content']='Different';value['sources'].append(other)
        with self.assertRaises(ValueError):coerce_evidence_context(value,'Task')
    def test_multiple_request_authorities_rejected(self):
        value=build_evidence_context('Task').to_dict();value['sources'].append({'source_id':'user_request:other','type':'user_request','label':'Other','content':'Unrelated request'})
        with self.assertRaises(ValueError):coerce_evidence_context(value,'Task')
    def test_duplicate_nonfactual_reference_ids_rejected(self):
        value=build_evidence_context('Task',{'dialogue_references':[{'role':'assistant','id':'one','content':'AI idea','authority':'derived_dialogue_context_not_factual_evidence'}]}).to_dict()
        value['nonfactual_references'].append(copy.deepcopy(value['nonfactual_references'][0]))
        with self.assertRaises(ValueError):coerce_evidence_context(value,'Task')
    def test_reserved_source_roles_cannot_be_injected_as_verified_material(self):
        for role in ('user_request','assistant_context'):
            with self.assertRaises(ValueError):build_evidence_context('Task',{'verified_project_materials':[{'verified':True,'content':'Other','source_type':role}]})
    def test_contract_binding_rejects_changed_source_and_detaches_mutable_context(self):
        context={'verified_project_materials':[{'verified':True,'content':'Original'}]}
        contract=TextTaskContract.create('Task',context);bound=contract.bind(context)
        context['verified_project_materials'][0]['content']='Changed'
        with self.assertRaises(ValueError):contract.bind(context)
        self.assertEqual(bound['verified_project_materials'][0]['content'],'Original')
    def test_changed_selected_message_is_rejected(self):
        plan=self.plan(operation='create_text',artifact_kind='plans',message_ids=['message:1'])
        self.db.execute("UPDATE messages SET content='Edited' WHERE id=1")
        with self.assertRaises(ValueError):compile_execution(self.db,plan,'/tmp/unused')
    def test_deleted_source_message_is_rejected_even_without_message_ids(self):
        plan=self.plan(operation='create_text',artifact_kind='plans',source_id='message:1')
        self.db.execute('DELETE FROM messages WHERE id=1')
        with self.assertRaises(ValueError):compile_execution(self.db,plan,'/tmp/unused')
    def test_current_user_message_is_bound_to_the_ui_turn(self):
        snapshot=dialogue_snapshot(self.db,'a',current_message_id=1)
        plan=DialogueContract('Original task',snapshot).validate(common.decision('create_text',artifact_kind='plans'))
        self.db.execute('DELETE FROM messages WHERE id=1')
        with self.assertRaises(ValueError):validate_live_plan(self.db,'a',plan)
        with self.assertRaises(ValueError):DialogueContract('Different current text',snapshot)
    def test_pending_created_after_snapshot_is_not_overwritten(self):
        plan=self.plan(operation='create_text',artifact_kind='plans')
        request,context=compile_execution(self.db,plan,'/tmp/unused')
        self.db.execute("INSERT INTO ai_pending_tasks VALUES('a','Other task','Q',NULL)");self.db.commit()
        with self.assertRaises(ValueError):persist_execution(self.db,plan,request,context)
        self.assertEqual(self.db.execute("SELECT request FROM ai_pending_tasks WHERE project_id='a'").fetchone()[0],'Other task')
    def test_manifest_change_with_same_pending_tuple_is_rejected(self):
        self.db.execute("INSERT INTO ai_pending_tasks VALUES('a','Original task','Q',NULL)")
        self.db.execute('CREATE TABLE ai_semantic_tasks(project_id TEXT,request TEXT,context_json TEXT)')
        original={'semantic_authorized':True,'semantic_execution_kind':'plans'}
        self.db.execute('INSERT INTO ai_semantic_tasks VALUES(?,?,?)',('a','Original task',json.dumps(original)));self.db.commit()
        c=DialogueContract('Continue',dialogue_snapshot(self.db,'a'));plan=c.validate(common.decision('resume',source_id='pending',artifact_kind='plans'))
        changed={**original,'verified_project_materials':[{'verified':True,'content':'Different facts'}]}
        self.db.execute('UPDATE ai_task_contexts SET context_json=?',(json.dumps(changed),));self.db.commit()
        with self.assertRaises(ValueError):compile_execution(self.db,plan,'/tmp/unused')
        with self.assertRaises(ValueError):cancel_pending(self.db,c.validate(common.decision('cancel_pending',source_id='pending')))
    def test_clarification_link_changed_without_text_change_is_rejected(self):
        self.db.execute('INSERT INTO ai_dialogue_questions(project_id,request,question,plan_json) VALUES(?,?,?,?)',('a','Task','Q','{"target_ids":[]}'));self.db.commit()
        plan=self.plan(operation='answer_clarification',source_id='clarification',artifact_kind='plans')
        self.db.execute('UPDATE ai_dialogue_questions SET plan_json=?',('{"target_ids":["doc"]}',));self.db.commit()
        with self.assertRaises(ValueError):compile_execution(self.db,plan,'/tmp/unused')
    def test_pending_manifest_and_question_write_roll_back_together(self):
        self.db.execute('CREATE TABLE ai_semantic_tasks(project_id TEXT,request TEXT,context_json TEXT,PRIMARY KEY(project_id,request))')
        self.db.execute("CREATE TRIGGER reject_manifest BEFORE INSERT ON ai_semantic_tasks BEGIN SELECT RAISE(ABORT,'INJECTED_MANIFEST_FAILURE'); END;")
        self.db.execute('INSERT INTO ai_dialogue_questions(project_id,request,question) VALUES(?,?,?)',('a','Earlier','Q'));self.db.commit()
        plan=self.plan(operation='create_text',artifact_kind='plans');request,context=compile_execution(self.db,plan,'/tmp/unused')
        with self.assertRaises(sqlite3.Error):persist_execution(self.db,plan,request,context)
        self.assertFalse(self.db.execute("SELECT 1 FROM ai_pending_tasks WHERE project_id='a'").fetchone())
        self.assertEqual(self.db.execute('SELECT request FROM ai_dialogue_questions').fetchone()[0],'Earlier')
    def test_two_connections_compare_and_set_before_pending_write(self):
        with tempfile.TemporaryDirectory() as home:
            path=Path(home)/'state.db';one=sqlite3.connect(path);self.db.backup(one)
            one.execute('DROP TABLE ai_pending_tasks');one.execute('CREATE TABLE ai_pending_tasks(project_id TEXT PRIMARY KEY,request TEXT,question TEXT,revision_id TEXT)');one.commit()
            two=sqlite3.connect(path)
            try:
                plan=DialogueContract('New A',dialogue_snapshot(one,'a')).validate(common.decision('create_text',artifact_kind='plans'))
                request,context=compile_execution(one,plan,home)
                two.execute("INSERT INTO ai_pending_tasks VALUES('a','Task B','Q',NULL)");two.commit()
                with self.assertRaises(ValueError):persist_execution(one,plan,request,context)
                self.assertEqual(two.execute('SELECT request FROM ai_pending_tasks').fetchone()[0],'Task B')
            finally:one.close();two.close()
    def test_cancel_readback_failure_rolls_back_under_the_same_write_lock(self):
        self.db.execute("INSERT INTO ai_pending_tasks VALUES('a','Old task','Q',NULL)");self.db.commit()
        self.db.execute("CREATE TRIGGER put_back_pending AFTER DELETE ON ai_pending_tasks BEGIN INSERT INTO ai_pending_tasks VALUES(OLD.project_id,OLD.request,OLD.question,OLD.revision_id); END;")
        c=DialogueContract('Cancel',dialogue_snapshot(self.db,'a'));plan=c.validate(common.decision('cancel_pending',source_id='pending'))
        with self.assertRaises(ValueError):cancel_pending(self.db,plan)
        self.assertEqual(self.db.execute('SELECT request FROM ai_pending_tasks').fetchone()[0],'Old task')
        exists=self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_cancelled_tasks'").fetchone()
        self.assertEqual(self.db.execute('SELECT count(*) FROM ai_cancelled_tasks').fetchone()[0] if exists else 0,0)
    def test_identical_body_from_another_task_cannot_authorize_reuse(self):
        self.db.execute("UPDATE manager_documents SET kind='strategy' WHERE id='doc'")
        receipt(self.db,'a','Task A','strategy','Original body','saved',json.dumps({'artifact_id':'doc'}))
        self.db.execute("INSERT INTO manager_documents VALUES('newdoc','a','strategy','Other','Original body','2030')")
        receipt(self.db,'a','Task B','strategy','Original body','saved',json.dumps({'artifact_id':'newdoc'}))
        self.assertEqual(original_document_request(self.db,'a','newdoc'),'Task B')
        self.assertIsNone(verified_document(self.db,'a','Task A','strategy'))
        self.assertEqual(verified_document(self.db,'a','Task B','strategy'),'Original body')
    def test_empty_resource_is_readable_and_marked_complete(self):
        self.db.execute("UPDATE manager_documents SET content='' WHERE id='doc'")
        c=DialogueContract('Read',dialogue_snapshot(self.db,'a'));plan=c.validate(common.decision('inspect',target_ids=['doc']))
        row=c.inspect(plan)[0];self.assertEqual(row['text'],'');self.assertTrue(row['complete'])
    def test_invalid_read_range_receives_bounded_contract_feedback(self):
        replies=[common.decision('inspect',target_ids=['doc'],offset=999),common.decision('respond',reply='Чтение уточнено.')];calls=[]
        def transport(payload,**kw):calls.append(copy.deepcopy(payload));return {'message':{'content':json.dumps(replies.pop(0))}}
        with patch('ollama_resilience.request_chat',side_effect=transport):plan=understand_dialogue('Any request',dialogue_snapshot(self.db,'a'),model='fixture')
        self.assertEqual(plan.action['operation'],'respond');self.assertEqual(len(calls),2)
        self.assertEqual(json.loads(calls[1]['messages'][-1]['content'])['origin'],'application_validation')

if __name__=='__main__':unittest.main()
