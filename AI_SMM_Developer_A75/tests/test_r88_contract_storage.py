"""Cross-component invariants found by source review, independent of user wording."""
import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
import test_r86_semantic_dialogue as common
from dialogue_runtime import DialogueContract,dialogue_snapshot,digest
from dialogue_execution import compile_execution,cancel_pending
from evidence_context import build_evidence_context,coerce_evidence_context
from text_task_contract import TextTaskContract


class ContractStorageTests(unittest.TestCase):
    def setUp(self):
        self.fixture=common.SemanticDialogueTests('test_selected_project_isolation');self.fixture.setUp();self.db=self.fixture.db
    def tearDown(self):self.fixture.tearDown()
    def plan(self,utterance,**kw):return DialogueContract(utterance,dialogue_snapshot(self.db,'a')).validate(common.decision(**kw))
    def pending(self,context=None):
        self.db.execute('INSERT INTO ai_pending_tasks VALUES(?,?,?,?)',('a','Original task','Question from AI',None))
        if context is not None:
            self.db.execute('CREATE TABLE ai_semantic_tasks(project_id TEXT,request TEXT,context_json TEXT)')
            self.db.execute('INSERT INTO ai_semantic_tasks VALUES(?,?,?)',('a','Original task',json.dumps(context)))
        self.db.commit()
    def test_assistant_context_never_becomes_user_request_or_fact_source(self):
        plan=self.plan('Use the previous idea',operation='create_text',artifact_kind='plans',message_ids=['message:1','message:2'])
        request,context=compile_execution(self.db,plan,'/tmp/unused')
        evidence=build_evidence_context(request,context)
        self.assertNotIn('Derived proposal',request)
        self.assertFalse(any('Derived proposal' in s.content for s in evidence.sources))
        self.assertTrue(any(r.content=='Derived proposal' for r in evidence.references))
        self.assertIn('Derived proposal',evidence.render())
        self.assertFalse({r.source_id for r in evidence.references}&set(evidence.source_index()))
    def test_clarification_question_remains_nonfactual_reference(self):
        self.db.execute('CREATE TABLE IF NOT EXISTS ai_dialogue_questions(project_id TEXT,request TEXT,question TEXT)')
        self.db.execute('INSERT INTO ai_dialogue_questions(project_id,request,question) VALUES(?,?,?)',('a','Original user task','Invented company service?'))
        plan=self.plan('Please use only verified facts',operation='answer_clarification',artifact_kind='plans',source_id='clarification')
        request,context=compile_execution(self.db,plan,'/tmp/unused');evidence=build_evidence_context(request,context)
        self.assertNotIn('Invented company service?',request)
        self.assertTrue(any(s.content=='Invented company service?' for s in evidence.references))
        self.assertFalse(any('Invented company service?' in s.content for s in evidence.sources))
    def test_nonfactual_provenance_survives_serialization_and_rebinding(self):
        context={'dialogue_references':[{'id':'ref','role':'assistant','content':'AI idea',
                   'authority':'derived_dialogue_context_not_factual_evidence'}]}
        evidence=build_evidence_context('Actual user task',context)
        restored=coerce_evidence_context(json.loads(json.dumps(evidence.to_dict())),'Actual user task')
        rebound=build_evidence_context('Actual user task',{'evidence_context':restored.to_dict()})
        self.assertEqual(rebound.references,evidence.references)
        self.assertEqual(rebound.source_index(),evidence.source_index())
    def test_nonfactual_reference_cannot_be_promoted_to_source_ledger(self):
        evidence=build_evidence_context('Actual task').to_dict()
        evidence['sources'].append({'source_id':'dialogue_context:fake','type':'assistant_context','label':'AI','content':'Invented fact'})
        with self.assertRaises(ValueError):coerce_evidence_context(evidence,'Actual task')
    def test_full_material_is_preserved_beyond_old_20000_character_cut(self):
        material='x'*20001+' IMPORTANT_RESTRICTION'
        e=build_evidence_context('Task',{'verified_project_materials':[{'verified':True,'content':material}]})
        self.assertEqual(e.sources[-1].content,material);self.assertIn('IMPORTANT_RESTRICTION',e.render(max_chars=25000))
    def test_oversized_evidence_is_explicitly_blocked_before_execution(self):
        with self.assertRaises(ValueError):TextTaskContract.create('Task',{'verified_project_materials':[{'verified':True,'content':'x'*25000+' FINAL_RESTRICTION'}]})
    def test_unknowns_are_not_silently_cut(self):
        text='x'*1100+' Important final condition'
        e=build_evidence_context('Task',{'unknowns':[text]});self.assertEqual(e.unknowns,(text,))
    def test_execution_kind_and_schedule_participate_in_task_identity(self):
        base=dict(semantic_authorized=True,semantic_execution_kind='bundle',semantic_schedule=dict(weeks=1,per_week=2,total=2))
        first=TextTaskContract.create('Same exact request',base)
        other=copy.deepcopy(base);other['semantic_schedule']=dict(weeks=2,per_week=2,total=4)
        self.assertNotEqual(first.signature,TextTaskContract.create('Same exact request',other).signature)
        other=copy.deepcopy(base);other['semantic_execution_kind']='calendar'
        self.assertNotEqual(first.signature,TextTaskContract.create('Same exact request',other).signature)
        self.assertEqual(first.metadata()['execution_parameters']['schedule'],base['semantic_schedule'])
        with self.assertRaises(ValueError):first.bind(other)
    def test_evidence_object_and_serialized_form_reject_wrong_request_equally(self):
        ledger=build_evidence_context('Task A')
        for value in (ledger,ledger.to_dict()):
            with self.assertRaises(ValueError):coerce_evidence_context(value,'Task B')
    def test_malformed_ledger_cannot_silently_discard_sources(self):
        for value in ({'schema_version':999},[],42):
            with self.assertRaises(ValueError):coerce_evidence_context(value,'Task')
    def test_entry_uses_same_fact_budget_as_downstream_reviewers(self):
        context={'verified_project_materials':[{'verified':True,'content':'x'*20100}]}
        with self.assertRaises(ValueError):TextTaskContract.create('Task',context)
    def test_stored_document_kind_is_derived_from_selected_resource(self):
        plan=self.plan('Arbitrary revision wording',operation='revise_saved',artifact_kind='strategy',target_ids=['doc'])
        self.assertEqual(plan.action['artifact_kind'],'plans');self.assertTrue(plan.normalizations)
        _,context=compile_execution(self.db,plan,'/tmp/unused');self.assertEqual(context['semantic_execution_kind'],'plans')
    def test_resume_parameters_are_restored_from_immutable_manifest(self):
        context=dict(semantic_authorized=True,semantic_execution_kind='plans',semantic_schedule=None,semantic_requested_media=False)
        self.pending(context)
        plan=self.plan('Another wording',operation='resume',artifact_kind='bundle',source_id='pending',
            schedule=dict(weeks=2,per_week=2,total=4),requested_media=True)
        self.assertEqual(plan.action['artifact_kind'],'plans');self.assertIsNone(plan.action['schedule']);self.assertFalse(plan.action['requested_media'])
        self.assertEqual(len(plan.normalizations),3)
        request,resumed=compile_execution(self.db,plan,'/tmp/unused');self.assertEqual(request,'Original task');self.assertEqual(resumed['task_mode'],'resume')
    def test_clarification_retains_original_sources_and_draft_memory(self):
        prior={'semantic_authorized':True,'semantic_execution_kind':'plans','semantic_schedule':None,'semantic_requested_media':False,
            'verified_project_materials':[{'verified':True,'content':'Original user source'}],
            'existing_documents':[{'content':'Previous unapproved draft'}],
            'revision_document':{'id':'doc','content':'Actual selected draft'},
            'recent_messages':[],'dialogue_references':[]}
        self.pending(prior)
        plan=self.plan('Answer',operation='answer_clarification',artifact_kind='plans',source_id='pending')
        _,context=compile_execution(self.db,plan,'/tmp/unused')
        self.assertIn(prior['verified_project_materials'][0],context['verified_project_materials'])
        self.assertIn(prior['existing_documents'][0],context['existing_documents'])
        self.assertEqual(context['revision_document'],prior['revision_document'])
    def test_cancel_archives_task_without_touching_documents_or_other_project(self):
        self.pending({'semantic_authorized':True,'semantic_execution_kind':'plans'})
        self.db.execute('INSERT INTO ai_pending_tasks VALUES(?,?,?,?)',('b','Other task','Other question',None));self.db.commit()
        before=self.db.execute('SELECT * FROM manager_documents ORDER BY id').fetchall()
        plan=self.plan('No special words',operation='cancel_pending',source_id='pending')
        result=cancel_pending(self.db,plan)
        self.assertTrue(result['archived']);self.assertFalse(self.db.execute("SELECT 1 FROM ai_pending_tasks WHERE project_id='a'").fetchone())
        self.assertTrue(self.db.execute("SELECT 1 FROM ai_pending_tasks WHERE project_id='b'").fetchone())
        archived=json.loads(self.db.execute('SELECT state_json FROM ai_cancelled_tasks WHERE id=?',(plan.turn_id,)).fetchone()[0])
        self.assertEqual(archived['request'],'Original task');self.assertIn('execution_context',archived)
        self.assertEqual(self.db.execute('SELECT * FROM manager_documents ORDER BY id').fetchall(),before)
        self.assertIn('create_text',DialogueContract('Next task',dialogue_snapshot(self.db,'a')).available_operations())
    def test_failed_cancellation_rolls_back_archive_and_pending_change(self):
        self.pending();self.db.execute("CREATE TRIGGER block_pending_delete BEFORE DELETE ON ai_pending_tasks BEGIN SELECT RAISE(ABORT,'fixture reject'); END;")
        plan=self.plan('Cancel',operation='cancel_pending',source_id='pending')
        with self.assertRaises(sqlite3.Error):cancel_pending(self.db,plan)
        self.assertTrue(self.db.execute('SELECT 1 FROM ai_pending_tasks').fetchone())
        exists=self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_cancelled_tasks'").fetchone()
        self.assertEqual(self.db.execute('SELECT count(*) FROM ai_cancelled_tasks').fetchone()[0] if exists else 0,0)
    def test_cancellation_rejects_stale_pending(self):
        self.pending();plan=self.plan('Cancel',operation='cancel_pending',source_id='pending')
        self.db.execute("UPDATE ai_pending_tasks SET question='Changed'")
        with self.assertRaises(ValueError):cancel_pending(self.db,plan)
        self.assertTrue(self.db.execute('SELECT 1 FROM ai_pending_tasks').fetchone())
    def test_corrupt_manifest_keeps_conversation_and_cancellation_available(self):
        self.pending();self.db.execute('CREATE TABLE ai_semantic_tasks(project_id TEXT,request TEXT,context_json TEXT)')
        self.db.execute("INSERT INTO ai_semantic_tasks VALUES('a','Original task','{bad_json')");self.db.commit()
        snap=dialogue_snapshot(self.db,'a');c=DialogueContract('Arbitrary',snap)
        self.assertIn('execution_contract_error',snap['sources']['pending'])
        self.assertNotIn('resume',c.available_operations());self.assertIn('cancel_pending',c.available_operations())
        self.assertTrue({'respond','clarify'}<=set(c.available_operations()))
        cancel_pending(self.db,c.validate(common.decision('cancel_pending',source_id='pending')))
        state=json.loads(self.db.execute('SELECT state_json FROM ai_cancelled_tasks').fetchone()[0])
        self.assertEqual(state['raw_execution_context'],'{bad_json')

if __name__=='__main__':unittest.main()
