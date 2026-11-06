from substance_fixture import assessed_reader
"""Repair authority and partial failures. Injected replies, not live model QA."""
import copy
import json
import random
import unittest
from unittest.mock import Mock, patch
import generic_text_review as text_review
from artifact_review import ReviewContractError
from deliverable_contract import bind_requirement_findings, review_requirement_bindings
from independent_acceptance_editor import ValidatedEditorialVerdict as Verdict
from independent_quality_gate import final_quality_gate
from ollama_resilience import _MODEL_CALL_ROLE
from review_arbitration import adjudicate_findings, validate_editorial_verdict, FindingReviewUnavailable
from text_task_contract import TextRevisionContract
from test_r62_review_boundaries import with_changes
from test_windows_acceptance_v4 import TaskRecoveryTests


def decision(action='keep_artifact',target='caption'):
    return {'action':action,'remedy_target':target if action=='correct_artifact' else 'none','reason':'The cited allegation has been checked against the complete artifact.',
        'required_change':'Clarify the identified reference.' if action=='correct_artifact' else ''}


class RepairAuthority(unittest.TestCase):
    fields={'strategy':'An unchanged strategic explanation.','calendar':'A concrete calendar.'}
    issue={'document':'strategy','field':'document_narrative','quote':fields['strategy'],
        'type':'quality','reason':'The editor alleges an ambiguous reference.',
        'review_scope':{'stage':'complete_bundle','available_artifacts':['strategy','calendar'],'pending_artifacts':[]}}
    def verdict(self,issues,state='complete'):
        return Verdict({'pass':not issues,'issues':issues,'review_state':state})
    def apply(self,issues,action='keep_artifact',fresh=None,state='complete'):
        calls=[]
        def ask(model,system,user,**kw):
            data=json.loads(user);calls.append((model,_MODEL_CALL_ROLE.get(),data))
            return {'decisions':{row['id']:decision(action,row['local_target']) for row in data['review_findings']}}
        full=Mock(return_value=self.verdict([]) if fresh is None else fresh)
        original=self.verdict(issues,state);snapshot=copy.deepcopy(original)
        out=validate_editorial_verdict(ask,'backup','Prepare strategy and calendar.',self.fields,
            original,full_review=full)
        self.assertEqual(original,snapshot)
        return out,calls,full
    def test_dismissal_requires_a_full_same_artifact_review_and_transmits_stage(self):
        out,calls,full=self.apply([self.issue]);full.assert_called_once_with()
        self.assertTrue(out['pass']);self.assertEqual(calls[0][:2],('backup','reviewer_backup'))
        self.assertEqual(calls[0][2]['artifact_fields'],self.fields)
        self.assertEqual(calls[0][2]['review_findings'][0]['finding']['review_scope'],self.issue['review_scope'])
    def test_confirmed_editorial_finding_cannot_be_overruled_by_bare_pass(self):
        out,_,full=self.apply([self.issue],'correct_artifact');full.assert_not_called()
        self.assertFalse(out['pass']);self.assertEqual(out['issues'][0]['finding_validation']['decision'],'confirmed')
    def test_source_findings_survive_editorial_dismissal(self):
        source={**self.issue,'type':'invented_fact','adjudication':'unsupported'}
        out,calls,full=self.apply([self.issue,source]);full.assert_not_called()
        self.assertEqual(out['issues'],[source]);self.assertEqual(len(calls[0][2]['review_findings']),1)
    def test_new_unchecked_quality_findings_cannot_authorize_repair(self):
        out,_,full=self.apply([self.issue],fresh=self.verdict([{**self.issue,'document':'calendar','quote':self.fields['calendar']}]))
        self.assertEqual(out['review_state'],'unavailable');self.assertEqual(out['issues'][0]['adjudication'],'unavailable')
        full.assert_called_once()
    def test_full_recheck_can_establish_a_new_source_defect(self):
        source={**self.issue,'type':'invented_fact','adjudication':'unsupported'}
        out,_,_=self.apply([self.issue],fresh=self.verdict([source]))
        self.assertEqual(out['issues'],[source]);self.assertEqual(out['review_state'],'complete')
    def test_partial_outage_keeps_completed_source_and_confirmed_quality(self):
        source={**self.issue,'type':'invented_fact','adjudication':'unsupported'}
        diagnostic={'type':'review_unavailable','reason':'Incomplete audit.','adjudication':'unavailable'}
        out,_,full=self.apply([self.issue,source,diagnostic],'correct_artifact',state='unavailable')
        full.assert_not_called();self.assertIn(source,out['issues']);self.assertIn(diagnostic,out['issues'])
        self.assertEqual(out['review_state'],'unavailable')
    def test_arbiter_outage_keeps_source_and_marks_editorial_as_unchecked(self):
        source={**self.issue,'type':'invented_fact','adjudication':'unsupported'};full=Mock()
        def down(*a,**kw):raise TimeoutError('Offline')
        out=validate_editorial_verdict(down,'backup','Prepare.',self.fields,self.verdict([self.issue,source]),full_review=full)
        self.assertIn(source,out['issues']);self.assertEqual(out['issues'][1]['adjudication'],'unavailable')
        full.assert_not_called()
    def test_invalid_full_recheck_never_becomes_approval(self):
        invalid=[{'pass':True,'issues':[self.issue]},{'pass':False,'issues':[]},
                 {'pass':True,'issues':[],'review_state':'unavailable'},{'pass':False,'issues':[None]}]
        for fresh in invalid:
            out,_,_=self.apply([self.issue],fresh=fresh)
            self.assertFalse(out['pass']);self.assertEqual(out['review_state'],'unavailable')
    def test_proof_and_scope_reach_revision_contract_without_aliasing(self):
        out,_,_=self.apply([self.issue],'correct_artifact')
        revision=TextRevisionContract.create(self.fields['strategy'],out['issues'])
        out['issues'][0]['finding_validation']['required_change']='Changed'
        out['issues'][0]['review_scope']['pending_artifacts'].append('Changed')
        self.assertEqual(revision.findings[0]['finding_validation']['required_change'],'Clarify the identified reference.')
        self.assertEqual(revision.findings[0]['review_scope']['pending_artifacts'],[])


class TypedDecisions(unittest.TestCase):
    fields={'caption':'Exact unchanged text.'}
    issue={'field':'caption','quote':fields['caption'],'reason':'A specific alleged defect.'}
    def test_action_and_required_change_must_agree(self):
        for action,count in [('correct_artifact',1),('keep_artifact',0)]:
            confirmed,dismissed=adjudicate_findings(lambda *a,**kw:{'decisions':{'A001':decision(action)}},
                'judge','Write.',self.fields,[self.issue])
            self.assertEqual(len(confirmed),count)
            self.assertEqual((confirmed or dismissed)[0]['finding_validation']['action'],action)
    def test_ambiguous_or_inconsistent_typed_decisions_stay_unavailable(self):
        invalid=[{'decision':'confirmed','reason':'The artifact meets the requirement.'},
            {**decision('correct_artifact'),'required_change':''},
            {**decision(),'required_change':'Change the text.'},
            {**decision('review_unavailable'),'required_change':'Change the text.'},
            {**decision(),'action':[]},{**decision(),'reason':None},{**decision(),'required_change':{}}]
        for value in invalid:
            with self.subTest(value=value),self.assertRaises(FindingReviewUnavailable):
                adjudicate_findings(lambda *a,**kw:{'decisions':{'A001':value}},'judge','Write.',self.fields,[self.issue])
    def test_one_contract_correction_keeps_the_input_and_schema_immutable(self):
        seen=[]
        def ask(model,system,user,*,json_schema):
            seen.append((system,user,json_schema));value=decision('correct_artifact')
            if len(seen)==1:value['required_change']=''
            return {'decisions':{'A001':value}}
        adjudicate_findings(ask,'judge','Write.',self.fields,[self.issue])
        self.assertEqual(len(seen),2);self.assertEqual(seen[0][1:],seen[1][1:])
    def test_legacy_three_argument_offline_adapter_remains_explicit(self):
        def ask(model,system,user):return {'decisions':{'A001':{'decision':'dismissed','reason':'The alleged problem is absent from this artifact.'}}}
        confirmed,dismissed=adjudicate_findings(ask,'offline','Write.',self.fields,[self.issue])
        self.assertFalse(confirmed);self.assertEqual(len(dismissed),1)
    def test_malformed_bindings_stop_before_transport(self):
        for change in ({'field':[]},{'quote':[]},{'quote':'Foreign bytes'},{'requirement_id':[]},{'reason':None}):
            ask=Mock()
            with self.subTest(change=change),self.assertRaises(ValueError):
                adjudicate_findings(ask,'judge','Write.',self.fields,[{**self.issue,**change}])
            ask.assert_not_called()


class RequirementAndProposalBoundary(unittest.TestCase):
    def test_literal_request_does_not_make_other_requirements_verbatim(self):
        bindings=review_requirement_bindings('Audience: new readers. Include the phrase «Known phrase».')
        kept,dismissed=bind_requirement_findings([{'requirement_id':row['id'],'failure_type':'verbatim_missing'} for row in bindings],bindings)
        self.assertEqual([i['requirement_id'] for i in kept],[bindings[1]['id']])
        self.assertEqual([i['requirement_id'] for i in dismissed],[bindings[0]['id']])
    def test_nonliteral_instruction_requires_semantic_reassessment_not_silent_pass(self):
        for request in ('Создай завершённый материал с полезным содержанием.','Write a useful explanation.'):
            binding=review_requirement_bindings(request)[0]
            with self.assertRaises(ValueError):bind_requirement_findings([{'requirement_id':binding['id'],'failure_type':'verbatim_missing'}],[binding])
            kept,dismissed=bind_requirement_findings([{'requirement_id':binding['id'],'failure_type':'required_content_missing'}],[binding])
            self.assertEqual(len(kept),1);self.assertFalse(dismissed)
    def test_real_dimension_corrects_classification_on_same_bytes(self):
        seen=[]
        def chat(payload,**kw):
            data=json.loads(payload['messages'][-1]['content']);seen.append(data)
            issue={'evidence_id':data['review_evidence'][0]['evidence_id'],'reason':'A required explanatory part is missing.',
                'severity':'major','requirement_id':data['requirement_bindings'][0]['id'],
                'failure_type':'verbatim_missing' if len(seen)==1 else 'required_content_missing'}
            return {'message':{'content':json.dumps({'pass':False,'issues':[issue]})}}
        with patch.object(text_review,'request_chat',side_effect=chat):
            result=text_review.review_dimension('Write a useful explanation.','A draft.',{},'requirements','judge')
        self.assertFalse(result['pass']);self.assertEqual(seen[0],seen[1])
        self.assertEqual(result['issues'][0]['failure_type'],'required_content_missing')
    def test_repeated_wrong_classification_never_approves(self):
        def chat(payload,**kw):
            data=json.loads(payload['messages'][-1]['content'])
            return {'message':{'content':json.dumps({'pass':False,'issues':[{'evidence_id':data['review_evidence'][0]['evidence_id'],
                'reason':'Instruction not reproduced word for word.','severity':'major',
                'requirement_id':data['requirement_bindings'][0]['id'],'failure_type':'verbatim_missing'}]})}}
        with patch.object(text_review,'request_chat',side_effect=chat) as wire:
            with self.assertRaises(ReviewContractError):text_review.review_dimension('Write an explanation.','Draft.',{},'requirements','judge')
        self.assertEqual(wire.call_count,2)
    def test_retained_substrings_are_proposals_never_semantic_approval(self):
        rng=random.Random(6801)
        for _ in range(30):
            quote=''.join(rng.choice('abcабв') for _ in range(28));draft=quote+' Original conclusion.'
            contract=TextRevisionContract.create(draft,[{'check':'factual_grounding','quote':quote,'reason':'No supporting source.'}])
            for revised in ('Do not claim «'+quote+'». General explanation.',draft+' Extra text.'):
                evidence=contract.evidence(contract.validate({'revised_text':revised}))
                self.assertTrue(evidence['findings'][0]['original_quote_retained'])
                self.assertEqual(evidence['semantic_resolution'],'requires_independent_review')
            with self.assertRaises(ValueError):contract.validate({'revised_text':draft})
    def test_retained_unsupported_claim_still_fails_the_actual_full_review(self):
        draft='We guarantee the result. Useful explanation.';revised=draft+' Added conclusion.'
        issue={'quote':'We guarantee the result.','reason':'No supporting source.','check':'factual_grounding'}
        def verdict():return {'pass':False,'status':'BLOCKED','issues':[issue],'checks':dict.fromkeys(text_review.CHECKS,False),
            'review_errors':{},'review_state':'complete','quality_model_pool':['writer','reviser','judge_a','judge_b','judge_c','judge_d']}
        def repair(*a,**kw):return TextRevisionContract.create(draft,[issue]).validate({'revised_text':revised})
        reviewer=Mock(side_effect=[verdict(),verdict()])
        out=text_review.run_quality_cycle('Write.',{'action':'write','answer':draft},{},model='writer',reviewer=reviewer,repairer=repair)
        self.assertEqual(reviewer.call_count,2);self.assertEqual(out['action'],'write_blocked');self.assertEqual(out['answer'],revised)
    def test_source_judge_receives_candidate_not_prior_allegation_as_evidence(self):
        from claim_support import adjudicate_factual_findings, public_copy_findings
        findings=public_copy_findings('A general question for the reader?');snapshot=copy.deepcopy(findings)
        def ask(model,system,user,**kw):
            data=json.loads(user);row=data['findings'][0]
            self.assertNotIn('review_reason',row);self.assertNotIn('reason',row);self.assertNotIn('type',row)
            self.assertEqual(row['claim'],findings[0]['quote']);self.assertIn('authorized_sources',data);self.assertIn('unknowns',data)
            return {'decisions':{'F001':with_changes(statement_class='general_information',speech_act='question',
                assertion_subject='reader',source_relation='not_needed',verdict='supported')}}
        out=adjudicate_factual_findings('Write.',findings,ask=ask,require_semantic_contract=True)
        self.assertFalse(out['blocking']);self.assertEqual(findings,snapshot)


class FinalBoundary(unittest.TestCase):
    def test_partial_unconfirmed_quality_is_diagnostic_and_not_repair_authority(self):
        prepared=TaskRecoveryTests.result(True)
        pending={'document':'strategy','type':'quality','quote':prepared['documents'][0]['content'],'reason':'Unchecked allegation.'}
        source={**pending,'type':'invented_fact','adjudication':'unsupported'}
        reviewer=Mock(return_value=Verdict({'pass':False,'issues':[source,pending],'review_state':'unavailable','applicability_review_complete':True}))
        out=final_quality_gate(prepared,'Write.',reviewer);report=out['content_delivery']['independent_editor']
        self.assertEqual(report['issues'],[source]);self.assertIn(pending,report['validation_diagnostics'])
        self.assertEqual(report['review_state'],'unavailable');self.assertEqual(reviewer.call_count,1)
    def test_exhausted_applicability_is_bounded_and_keeps_diagnostics(self):
        prepared=TaskRecoveryTests.result(True)
        pending={'document':'strategy','type':'quality','quote':prepared['documents'][0]['content'],
            'reason':'Unchecked allegation.','adjudication':'unavailable'}
        reviewer=Mock(return_value=Verdict({'pass':False,'issues':[pending],'review_state':'unavailable','applicability_review_complete':True}))
        out=final_quality_gate(prepared,'Write.',reviewer);report=out['content_delivery']['independent_editor']
        self.assertFalse(out['documents'][1]['valid']);self.assertFalse(report['issues'])
        self.assertIn(pending,report['validation_diagnostics']);self.assertEqual(reviewer.call_count,1)
    @patch('independent_acceptance_editor.call_substance_reader',new=assessed_reader)
    def test_native_pipeline_validates_strategy_and_final_findings_before_any_edit(self):
        from production_pipeline import run_document_task
        prepared=TaskRecoveryTests.result(True);seen=[]
        def choose(author,**kw):
            excluded={author,*(kw.get('exclude_models') or [])}
            return next(m for m in ['primary','backup','extra'] if m not in excluded)
        def strategy(req,body,**kw):
            seen.append(('strategy',kw['model'],_MODEL_CALL_ROLE.get()))
            return Verdict({'pass':kw['model']=='backup','issues':[] if kw['model']=='backup' else [{
                'document':'strategy','type':'quality','quote':body,'reason':'The pending calendar is allegedly missing inside this strategy.'}],'review_state':'complete'})
        def final(req,body,calendar,**kw):
            seen.append(('final',kw['model'],_MODEL_CALL_ROLE.get()))
            return Verdict({'pass':kw['model']=='backup','issues':[] if kw['model']=='backup' else [{
                'document':'calendar','type':'quality','quote':calendar,'reason':'An alleged problem in the full calendar.'}],'review_state':'complete'})
        def execute(executor,request,state,**kw):
            self.assertTrue(state['strategy_reviewer'](prepared['documents'][0]['content'])['pass'])
            return copy.deepcopy(prepared)
        def wire(payload,**kw):
            data=json.loads(payload['messages'][-1]['content'])
            self.assertEqual(payload['model'],'backup');self.assertEqual(_MODEL_CALL_ROLE.get(),'reviewer_backup')
            return {'message':{'content':json.dumps({'decisions':{row['id']:decision() for row in data['review_findings']}})}}
        with patch('generic_text_review.select_independent_model',side_effect=choose), \
             patch('generic_text_review.known_quality_model_pool',return_value=['writer','primary','backup','extra']), \
             patch('production_pipeline.run_compound_with_retry',side_effect=execute), \
             patch('production_pipeline.invoke_executor') as repair, \
             patch('independent_acceptance_editor.call_strategy_editor',side_effect=strategy), \
             patch('independent_acceptance_editor.call_editor',side_effect=final), \
             patch('ollama_resilience.request_chat',side_effect=wire):
            out=run_document_task('Prepare documents.',{},model='writer')
        repair.assert_not_called();self.assertEqual(seen,[('strategy','primary','reviewer'),('strategy','backup','reviewer_backup'),
            ('final','primary','reviewer'),('final','backup','reviewer_backup')])
        report=out['content_delivery']['independent_editor'];self.assertEqual(report['status'],'PASS')
        self.assertTrue(report['full_recheck_after_finding_dismissal']);self.assertEqual(len(report['finding_adjudication']['dismissed']),1)


class NeighborFailures(unittest.TestCase):
    def test_explicit_native_self_reviewer_stops_before_generation(self):
        from production_pipeline import run_document_task
        with patch('production_pipeline.run_compound_with_retry') as execute:
            with self.assertRaises(ValueError):
                run_document_task('Prepare documents.',{},model='same-model:cloud',review_model='same-model:8b')
        execute.assert_not_called()
    def test_partial_applicability_retains_already_confirmed_sibling(self):
        fields={'a':'First exact text.','b':'Second exact text.'}
        issues=[{'field':key,'quote':value,'reason':'A concrete alleged defect.'} for key,value in fields.items()]
        def ask(*a,**kw):return {'decisions':{'A001':decision('correct_artifact','a'),'A002':decision('review_unavailable')}}
        out=validate_editorial_verdict(ask,'judge','Write.',fields,
            Verdict({'pass':False,'issues':issues}),full_review=lambda:self.fail('No full recheck on partial uncertainty'))
        self.assertEqual(out['review_state'],'unavailable')
        self.assertEqual(out['issues'][0]['finding_validation']['decision'],'confirmed')
        self.assertNotEqual(out['issues'][0].get('adjudication'),'unavailable')
        self.assertEqual(out['issues'][1]['adjudication'],'unavailable')
    def test_publication_new_allegation_after_dismissal_does_not_trigger_author_repair(self):
        from semantic_editor import audit_post_with_independent_model
        post={'caption':'Exact unchanged copy.'}
        issue={'field':'caption','quote':post['caption'],'reason':'A concrete editorial allegation.'}
        def audit(*a,**kw):return None,[issue]
        def judge(*a,**kw):return [],[issue]
        with self.assertRaises(FindingReviewUnavailable) as error:
            audit_post_with_independent_model(None,'writer',['primary','backup'],'Write.',{}, {},post,
                audit_function=audit,adjudicate_function=judge)
        self.assertEqual(error.exception.findings[0]['adjudication'],'unavailable')
        self.assertEqual(post,{'caption':'Exact unchanged copy.'})
    def test_strategy_partial_source_outage_retains_completed_rejection(self):
        import independent_acceptance_editor as editor
        issues=[{'document':'strategy','type':'invented_fact','quote':quote,'reason':'Source review needed.'}
                for quote in ('First business promise.','Second business promise.')]
        blocking=[{**issues[0],'adjudication':'unsupported'},{**issues[1],'adjudication':'unavailable'}]
        with patch.object(editor,'_call_editor_once',return_value={'pass':False,'issues':issues}):
            with self.assertRaises(editor.IndependentReviewUnavailable) as error:
                editor.call_strategy_editor('Prepare strategy.','First business promise. Second business promise.',
                    claim_adjudicator=lambda *a,**kw:{'blocking':blocking,'supported':[]})
        self.assertEqual(error.exception.findings,blocking)
        self.assertFalse(error.exception.retryable)
    def test_structured_strategy_outage_never_rewrites_the_author(self):
        from strategy_recovery import produce_checked_strategy
        drafts=[];repairs=[]
        with patch('strategy_recovery.strategy_integrity_issues',return_value=[]):
            result=produce_checked_strategy('Prepare strategy.',1,
                lambda *a:drafts.append('Draft.') or 'Draft.',lambda *a:(True,''),attempts=3,
                repair=lambda *a:repairs.append(a),
                review=lambda *a:{'pass':False,'issues':[],'review_state':'unavailable'})
        self.assertFalse(result[1]);self.assertEqual(len(drafts),1);self.assertFalse(repairs)


if __name__=='__main__':unittest.main()
