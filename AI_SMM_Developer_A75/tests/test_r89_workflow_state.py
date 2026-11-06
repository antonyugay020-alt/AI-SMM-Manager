"""Amended revisions and early capability checks: no user phrase routing."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import test_r86_semantic_dialogue as common
from dialogue_runtime import DialogueContract,dialogue_snapshot
from dialogue_execution import compile_execution
from independent_acceptance_editor import validate_editor_request,EDITOR_REQUEST_CHARACTER_BUDGET,EditorialContextError


class WorkflowStateTests(unittest.TestCase):
    def setUp(self):
        self.fixture=common.SemanticDialogueTests('test_selected_project_isolation');self.fixture.setUp();self.db=self.fixture.db
        self.tmp=tempfile.TemporaryDirectory();self.workspace=Path(self.tmp.name);(self.workspace/'drafts').mkdir()
        self.original='Original task';self.revision_id='a'*32
        self.seed={'project_id':'a','request':self.original,'strategy':'Old strategy','posts':{'1/Ср':{'caption':'Old text'}},
            'slots':[{'week':1,'day':'Ср','platform':'Instagram','topic':'Old topic'}],'platform':'Instagram',
            'scope':{'weeks':1,'per_week':1,'total':1}}
        (self.workspace/'drafts'/('revision_input_'+self.revision_id+'.json')).write_text(json.dumps(self.seed))
        self.db.execute('INSERT INTO ai_pending_tasks VALUES(?,?,?,?)',('a',self.original,'Which requirements now?',self.revision_id))
        self.db.execute('CREATE TABLE ai_semantic_tasks(project_id TEXT,request TEXT,context_json TEXT)')
        manifest={'semantic_authorized':True,'semantic_execution_kind':'bundle','semantic_schedule':self.seed['scope'],'semantic_requested_media':False}
        self.db.execute('INSERT INTO ai_semantic_tasks VALUES(?,?,?)',('a',self.original,json.dumps(manifest)));self.db.commit()
    def tearDown(self):self.tmp.cleanup();self.fixture.tearDown()
    def compile(self,scope,reuse=False,operation='answer_clarification'):
        c=DialogueContract('New exact user conditions',dialogue_snapshot(self.db,'a'))
        plan=c.validate(common.decision(operation,artifact_kind='bundle',source_id='pending',schedule=scope,reuse_structure=reuse))
        return compile_execution(self.db,plan,self.workspace)
    def test_changed_structure_does_not_force_old_platform_slots_or_strategy(self):
        request,state=self.compile({'weeks':2,'per_week':1,'total':2})
        self.assertNotEqual(request,self.original);self.assertNotEqual(state['revision_id'],self.revision_id)
        self.assertEqual(state['revision_seed']['request'],request)
        for key in ('posts','slots','strategy'):self.assertFalse(state['revision_seed'][key])
        self.assertEqual(json.loads((self.workspace/'drafts'/('revision_input_'+self.revision_id+'.json')).read_text()),self.seed)
    def test_explicit_unchanged_structure_reuses_only_an_unapproved_seed(self):
        _,state=self.compile(self.seed['scope'],True)
        self.assertEqual(state['revision_seed']['slots'],self.seed['slots'])
        self.assertEqual(state['revision_seed']['posts'],self.seed['posts'])
        self.assertNotIn('resume_documents',state)
    def test_inconsistent_structure_reuse_rejected_before_starting_work(self):
        with self.assertRaises(ValueError):self.compile({'weeks':2,'per_week':1,'total':2},True)
        self.assertEqual(self.db.execute('SELECT request FROM ai_pending_tasks').fetchone()[0],self.original)
    def test_resume_without_amendment_keeps_original_seed_and_identity(self):
        request,state=self.compile(self.seed['scope'],False,'resume')
        self.assertEqual(request,self.original);self.assertEqual(state['revision_id'],self.revision_id)
        self.assertEqual(state['revision_seed'],self.seed)
    def test_execution_replans_instead_of_overriding_new_semantic_platform(self):
        _,state=self.compile({'weeks':2,'per_week':1,'total':2})
        from task_semantics import semantic_task
        from compound_tasks import execute_compound
        class ObservedPlatform(BaseException):pass
        events=[]
        brief={'channels':'Telegram','channels_basis':'explicit','critical_questions':[], 'goals':[],'unknowns':[]}
        def stop(*args,**kw):raise ObservedPlatform()
        with semantic_task(state),patch('compound_tasks.analyze',return_value=brief),patch('compound_tasks.produce_checked_strategy',side_effect=stop):
            with self.assertRaises(ObservedPlatform):execute_compound(state['resolved_task_request'],state,progress=events.append)
        self.assertEqual(brief['channels'],'Telegram')
    def test_corrupt_pending_cannot_be_resumed_through_clarification_action(self):
        self.db.execute("UPDATE ai_semantic_tasks SET context_json='{bad_json'")
        snapshot=dialogue_snapshot(self.db,'a');contract=DialogueContract('Arbitrary',snapshot)
        self.assertNotIn('resume',contract.available_operations());self.assertNotIn('answer_clarification',contract.available_operations())
        self.assertIn('cancel_pending',contract.available_operations())
        snapshot['sources']['clarification']={'request':'Separate valid request','question':'Q'}
        contract=DialogueContract('Arbitrary',snapshot)
        self.assertIn('answer_clarification',contract.available_operations())
        with self.assertRaises(ValueError):contract.validate(common.decision('answer_clarification',source_id='pending',artifact_kind='bundle',schedule=self.seed['scope']))
    def test_manifest_without_authorized_execution_is_not_treated_as_resumable(self):
        self.db.execute('UPDATE ai_semantic_tasks SET context_json=?',(json.dumps({'semantic_execution_kind':'bundle','semantic_schedule':self.seed['scope']}),))
        snapshot=dialogue_snapshot(self.db,'a')
        self.assertIn('execution_contract_error',snapshot['sources']['pending'])
        self.assertNotIn('resume',DialogueContract('Arbitrary',snapshot).available_operations())
    def test_long_brief_is_blocked_before_model_selection_or_author_executor(self):
        from production_pipeline import run_document_task
        request='x'*(EDITOR_REQUEST_CHARACTER_BUDGET+1)
        with patch('generic_text_review.select_independent_model',side_effect=AssertionError('No reviewer request allowed')) as selection,\
             patch('compound_tasks.execute_compound',side_effect=AssertionError('No author work allowed')) as author:
            with self.assertRaises(EditorialContextError):run_document_task(request,{})
        self.assertEqual(selection.call_count,0);self.assertEqual(author.call_count,0)
    def test_editor_request_limit_accepts_exact_boundary(self):
        self.assertIsNone(validate_editor_request('x'*EDITOR_REQUEST_CHARACTER_BUDGET))
        for value in ('',None,{},'x'*(EDITOR_REQUEST_CHARACTER_BUDGET+1)):
            with self.assertRaises(EditorialContextError):validate_editor_request(value)
    def test_typed_and_legacy_strategy_review_share_limit(self):
        from independent_acceptance_editor import _typed_editor_windows,_review_windows,call_strategy_editor
        value='x'*(EDITOR_REQUEST_CHARACTER_BUDGET+1)
        for invoke in (lambda:_typed_editor_windows(value,'Strategy','Calendar'),lambda:_review_windows(value,'Strategy','Calendar'),
                       lambda:call_strategy_editor(value,'Strategy')):
            with self.assertRaises(ValueError):invoke()

if __name__=='__main__':unittest.main()
