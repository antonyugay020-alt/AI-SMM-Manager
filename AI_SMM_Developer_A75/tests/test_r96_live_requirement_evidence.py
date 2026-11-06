"""Received Windows model evidence; no word-overlap adjudication in live review."""
import copy,json,random,unittest
from pathlib import Path
from unittest.mock import patch
import independent_acceptance_editor as editor
from dialogue_runtime import DialogueContract,understand_dialogue
from text_workflow import run_text_task
from test_r86_semantic_dialogue import decision

FIXTURE=json.loads((Path(__file__).with_name('r96_received_windows.json')).read_text(encoding='utf-8'))

class LiveRequirementEvidenceTests(unittest.TestCase):
    def replay(self,index=0):
        row=FIXTURE['reviews'][index];self.calls=[]
        def wire(payload,**kw):
            self.calls.append(payload);return {'message':{'content':json.dumps(row['reply'],ensure_ascii=False)}}
        with patch('artifact_review.substance_controls',return_value=row['controls']),patch.object(editor,'request_chat',side_effect=wire):
            return editor._call_editor_once(FIXTURE['request'],FIXTURE['strategy'],'',strategy_only=True,
                model=row['model'],_segments=editor._inventory_segments(FIXTURE['strategy'],''))
    def test_exact_received_primary_verdict_retains_all_five_findings_in_one_call(self):
        out=self.replay();self.assertFalse(out['pass']);self.assertEqual(len(out['issues']),5);self.assertEqual(len(self.calls),1)
        self.assertEqual(sum(x['type']=='missing_required_content' for x in out['issues']),4)
        self.assertTrue(out['substance_review']['complete']);self.assertFalse(out['substance_review']['pass'])
    def test_malformed_recorded_retry_does_not_approve(self):
        with self.assertRaisesRegex(ValueError,'критерием'):self.replay(1)
    def test_failed_reserve_control_still_cannot_approve(self):
        from artifact_review import SubstanceControlFailure
        with self.assertRaises(SubstanceControlFailure):self.replay(2)
    def test_live_parser_never_calls_lexical_requirement_applicability(self):
        with patch('requirement_grounding.requirement_quote_applies',side_effect=AssertionError('No word-based meaning')):
            self.assertEqual(len(self.replay()['issues']),5)
    def test_short_requirement_and_different_vocabulary_stay_pending_for_AI(self):
        # No claim that the finding is semantically correct: exact provenance
        # survives so an independent model can decide applicability.
        for requirement,reason in [('X','The deliverable needs a distinct strategic choice.'),
                                   ('Ориентир','Не обозначены критерии выбора и последовательность решений.')]:
            body='A source fragment.';segments=editor._inventory_segments(body,'')
            raw={'pass':False,'issues':[{'document':'strategy','type':'missing_required_content',
                'evidence_id':segments[0]['evidence_id'],'reason':reason,'requirement_quote':requirement}]}
            out=editor.parse_verdict(json.dumps(raw),strategy=body,calendar='',request=requirement,
                require_claim=True,host_evidence_only=True,_segments=segments)
            self.assertFalse(out['pass']);self.assertEqual(len(out['issues']),1)
    def test_unrelated_or_prohibitive_quote_is_not_silently_approved_by_host(self):
        body='A source fragment.';segments=editor._inventory_segments(body,'')
        for request in ('Do not invent unknown prices.','A project description.'):
            raw={'pass':False,'issues':[{'document':'strategy','type':'missing_required_content',
                'evidence_id':segments[0]['evidence_id'],'reason':'A hypothetical missing feature claimed by a reviewer.',
                'requirement_quote':request}]}
            out=editor.parse_verdict(json.dumps(raw),strategy=body,calendar='',request=request,
                require_claim=True,host_evidence_only=True,_segments=segments)
            self.assertFalse(out['pass']);self.assertEqual(out['issues'][0]['requirement_quote'],request)
    def test_unknown_or_empty_requirement_quote_is_unavailable(self):
        body='A source fragment.';segments=editor._inventory_segments(body,'')
        for quote in ('A foreign instruction.','',None):
            raw={'pass':False,'issues':[{'document':'strategy','type':'missing_required_content',
                'evidence_id':segments[0]['evidence_id'],'reason':'A concrete missing requirement.', 'requirement_quote':quote}]}
            with self.assertRaises(ValueError):editor.parse_verdict(json.dumps(raw),strategy=body,calendar='',request='Known instruction.',
                require_claim=True,host_evidence_only=True,_segments=segments)
    def test_unknown_evidence_id_still_unavailable(self):
        row=copy.deepcopy(FIXTURE['reviews'][0]);row['reply']['issues'][0]['evidence_id']='strategy:9999'
        with patch.dict(FIXTURE['reviews'][0],row):
            with self.assertRaises(ValueError):self.replay()
    def test_existing_legacy_parser_still_does_not_grant_unknown_requirements(self):
        raw={'pass':False,'issues':[{'document':'strategy','type':'missing_required_content','quote':'Draft.',
            'reason':'Add a named expert to the content.','requirement_quote':'Do not invent unknown facts.'}]}
        self.assertTrue(editor.parse_verdict(json.dumps(raw),strategy='Draft.',calendar='',request='Do not invent unknown facts.')['pass'])
    def test_native_strategy_stage_preserves_findings_for_AI_applicability(self):
        row=FIXTURE['reviews'][0]
        with patch('artifact_review.substance_controls',return_value=row['controls']), \
             patch.object(editor,'request_chat',return_value={'message':{'content':json.dumps(row['reply'])}}), \
             patch.object(editor,'_review_artifact_form',return_value=[]), \
             patch.object(editor,'_inventory_business_assertions',return_value=[]), \
             patch.object(editor,'_nonblocking_editor_false_positive',side_effect=AssertionError('No lexical semantics')), \
             patch.object(editor,'_nonblocking_scope_issue',side_effect=AssertionError('No lexical semantics')):
            out=editor.call_strategy_editor(FIXTURE['request'],FIXTURE['strategy'],model=row['model'],require_inventory=True,
                claim_adjudicator=lambda req,issues,**kw:{'blocking':issues,'supported':[]})
        self.assertFalse(out['pass']);self.assertEqual(len(out['issues']),5)
        self.assertEqual({x.get('criterion_id') for x in out['issues'] if x['type']=='missing_required_content'},
            {k for k,v in row['reply']['assessments'].items() if v['status']=='defect'})
    def test_native_bundle_editor_keeps_bound_findings_until_independent_AI_review(self):
        row=FIXTURE['reviews'][0]
        with patch('artifact_review.substance_controls',return_value=row['controls']), \
             patch.object(editor,'request_chat',return_value={'message':{'content':json.dumps(row['reply'])}}), \
             patch.object(editor,'_nonblocking_editor_false_positive',side_effect=AssertionError('No lexical semantics')), \
             patch.object(editor,'_nonblocking_scope_issue',side_effect=AssertionError('No lexical semantics')):
            out=editor.call_editor(FIXTURE['request'],FIXTURE['strategy'],'',allow_partial=True,model=row['model'],
                claim_adjudicator=lambda req,issues,**kw:{'blocking':issues,'supported':[]})
        self.assertFalse(out['pass']);self.assertEqual(out['review_state'],'complete')
        self.assertTrue(out['substance_review']['complete']);self.assertEqual(len(out['issues']),5)
    def test_recorded_media_flag_is_rejected_before_text_execution(self):
        c=DialogueContract(FIXTURE['request'],FIXTURE['snapshot'])
        with self.assertRaisesRegex(ValueError,'медиафайлы'):c.validate(FIXTURE['dialogue_reply'])
    def test_schema_explains_output_kind_without_host_keyword_matching(self):
        schema=DialogueContract(FIXTURE['request'],FIXTURE['snapshot']).schema()
        self.assertIn('description',schema['properties']['requested_media'])
    def test_arbitrary_utterances_obey_the_same_typed_capability_guard(self):
        snapshot=copy.deepcopy(FIXTURE['snapshot']);snapshot['current_message_id']=None
        for text in ('?!','Make a detailed scene plan','Иначе','Take the next step'):
            c=DialogueContract(text,snapshot)
            with self.assertRaises(ValueError):c.validate(decision('create_bundle',schedule={'weeks':1,'per_week':1,'total':1},requested_media=True))
            self.assertFalse(c.validate(decision('create_bundle',schedule={'weeks':1,'per_week':1,'total':1})).action['requested_media'])
    def test_AI_can_reconsider_false_media_flag_with_one_contract_correction(self):
        first=copy.deepcopy(FIXTURE['dialogue_reply']);second=copy.deepcopy(first);second['requested_media']=False
        calls=[]
        def wire(payload,**kw):
            calls.append(payload);return {'message':{'content':json.dumps(first if len(calls)==1 else second)}}
        with patch('ollama_resilience.request_chat',side_effect=wire):
            out=understand_dialogue(FIXTURE['request'],FIXTURE['snapshot'],model='injected_dialogue')
        self.assertEqual(len(calls),2);self.assertFalse(out.action['requested_media'])
        self.assertEqual(out.action['schedule']['total'],16)
        feedback=json.loads(calls[1]['messages'][-1]['content']);self.assertEqual(feedback['origin'],'application_validation')
    def test_AI_can_choose_clarification_instead_of_impossible_execution(self):
        first=copy.deepcopy(FIXTURE['dialogue_reply']);second=decision('clarify',reply='Which deliverable is needed?')
        calls=[]
        def wire(payload,**kw):
            calls.append(payload);return {'message':{'content':json.dumps(first if len(calls)==1 else second)}}
        with patch('ollama_resilience.request_chat',side_effect=wire):out=understand_dialogue(FIXTURE['request'],FIXTURE['snapshot'],model='injected_dialogue')
        self.assertEqual(out.action['operation'],'clarify')
    def test_direct_text_service_blocks_media_before_planning_or_generation(self):
        context={'semantic_authorized':True,'semantic_execution_kind':'bundle','semantic_schedule':{'weeks':1,'per_week':1,'total':1},'semantic_requested_media':True}
        with self.assertRaisesRegex(ValueError,'медиафайлы'):
            run_text_task('Arbitrary task',context,compound_runner=lambda *a,**kw:self.fail('No generation before capability check'))

if __name__=='__main__':unittest.main()
