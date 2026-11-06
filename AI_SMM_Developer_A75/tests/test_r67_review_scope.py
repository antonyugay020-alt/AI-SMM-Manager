"""Criterion/stage isolation and independent finding applicability; injected decisions."""
import copy
import json
import unittest
from unittest.mock import patch
import generic_text_review as review
import independent_acceptance_editor as editor
from artifact_review import ArtifactReviewContract, ReviewContractError
from claim_interpretation import bind_interpretation
from claim_support import adjudicate_factual_findings, public_copy_findings
from deliverable_contract import editorial_form_bindings
from strategy_recovery import produce_checked_strategy
from test_r62_review_boundaries import with_changes


class ReviewScope(unittest.TestCase):
    request='Подготовь стратегию и календарь. Для каждой публикации укажи текст, CTA и метрику.'

    def test_same_artifact_ids_cannot_cross_stage_or_criterion(self):
        first=ArtifactReviewContract.create('Request',{'text':'Exact text.'},scope={'stage':'preflight'})
        for scope in ({'stage':'final'},{'dimension':'requirements'}):
            other=ArtifactReviewContract.create('Request',{'text':'Exact text.'},scope=scope)
            with self.assertRaises(ValueError):other.resolve_evidence(first.spans[0].ident)

    def test_form_schema_excludes_content_obligations_and_carries_stage(self):
        def chat(payload,**kwargs):
            user=json.loads(payload['messages'][-1]['content']);system=payload['messages'][0]['content']
            self.assertEqual(user['review_scope']['available_artifacts'],['strategy'])
            self.assertEqual(user['review_scope']['pending_artifacts'],['calendar'])
            self.assertEqual(user['review_scope']['stage'],'strategy_preflight')
            self.assertEqual(user['review_scope']['dimension'],'format_and_tone')
            props=payload['format']['properties']['issues']['items']['properties']
            self.assertTrue(all(ref.startswith('G_') for ref in props['requirement_id']['enum']))
            self.assertEqual(props['failure_type']['enum'],['style_mismatch'])
            self.assertNotIn('В проверке полноты требуй',system)
            self.assertEqual(kwargs['endpoint'],'http://node.example/api/chat')
            return {'message':{'content':'{"pass":true,"issues":[]}'}}
        with patch.object(review,'request_chat',side_effect=chat):
            issues=editor._review_artifact_form(self.request,'# Стратегия\nПрактический план.','',model='judge',
                review_stage='strategy_preflight',endpoint='http://node.example/api/chat')
        self.assertFalse(issues)

    def test_out_of_scope_calendar_finding_cannot_trigger_strategy_rewrite(self):
        calls=[];repairs=[]
        def chat(payload,**kw):
            data=json.loads(payload['messages'][-1]['content']);calls.append(data)
            if len(calls)>1:return {'message':{'content':'{"pass":true,"issues":[]}'}}
            value={'pass':False,'issues':[{'evidence_id':data['review_evidence'][0]['evidence_id'],
                'reason':'The pending calendar is absent from this strategy.', 'severity':'major',
                'requirement_id':'R0002_not_issued','failure_type':'required_content_missing'}]}
            return {'message':{'content':json.dumps(value)}}
        with patch.object(review,'request_chat',side_effect=chat), \
             patch.object(editor,'_call_editor_once',return_value={'pass':True,'issues':[]}), \
             patch.object(editor,'_inventory_business_assertions',return_value=[]), \
             patch('strategy_recovery.strategy_integrity_issues',return_value=[]):
            result=produce_checked_strategy(self.request,1,lambda *a:'Стратегия. Практический план.',
                lambda *a:(True,''),attempts=1,repair=lambda *a:repairs.append(a),
                review=lambda body:editor.call_strategy_editor(self.request,body,require_inventory=True))
        self.assertTrue(result[1]);self.assertEqual(repairs,[])
        self.assertEqual(len(calls),2);self.assertEqual(calls[0],calls[1])

    def test_repeated_out_of_scope_findings_remain_unavailable_and_never_become_content_edits(self):
        def chat(payload,**kw):
            data=json.loads(payload['messages'][-1]['content'])
            issue={'evidence_id':data['review_evidence'][0]['evidence_id'],'reason':'The calendar is missing.',
                'severity':'major','requirement_id':'G_requested_form','failure_type':'required_content_missing'}
            return {'message':{'content':json.dumps({'pass':False,'issues':[issue]})}}
        with patch.object(review,'request_chat',side_effect=chat) as call:
            with self.assertRaises(ReviewContractError):review.review_dimension(self.request,'Стратегия.',{},
                'format_and_tone','judge',review_scope={'available_artifacts':['strategy'],'pending_artifacts':['calendar']})
        self.assertEqual(call.call_count,2)

    def test_real_language_finding_stays_actionable_in_strategy_scope(self):
        def chat(payload,**kw):
            data=json.loads(payload['messages'][-1]['content']);row=data['review_evidence'][0]
            issue={'evidence_id':row['evidence_id'],'reason':'The requested Russian body is written in English.',
                'severity':'major','requirement_id':'G_language','failure_type':'style_mismatch'}
            return {'message':{'content':json.dumps({'pass':True,'issues':[issue]})}}
        with patch.object(review,'request_chat',side_effect=chat):
            issues=editor._review_artifact_form(self.request,'An English strategic explanation.','',model='judge',
                review_stage='strategy_preflight')
        self.assertEqual(issues[0]['document'],'strategy');self.assertEqual(issues[0]['requirement_id'],'G_language')

    def test_mixed_reader_communication_is_canonical_but_business_and_service_are_not_exempt(self):
        for act in ('advice','question','social_expression'):
            decision=with_changes(speech_act=act,assertion_subject='reader',source_relation='not_needed',
                invitation_kind='communication',verdict='supported')
            value=bind_interpretation(decision,require=True)
            self.assertNotIn(value['statement_class'],('business_fact','service_outcome'))
            self.assertEqual(value['invitation_kind'],'none')
        for subject,act,kind in [('business','assertion','communication'),('reader','advice','service_access')]:
            value=bind_interpretation(with_changes(speech_act=act,assertion_subject=subject,
                invitation_kind=kind,source_relation='not_needed',verdict='supported'),require=True)
            self.assertEqual(value['statement_class'],'business_fact');self.assertEqual(value['verdict'],'unsupported')

    def test_unresolved_source_decision_keeps_exact_role_diagnostics(self):
        decision=with_changes(speech_act='invitation',assertion_subject='reader',invitation_kind='none',
            source_relation='not_needed',verdict='supported')
        out=adjudicate_factual_findings('Write.',public_copy_findings('An invitation to the reader.'),
            ask=lambda *a,**k:{'decisions':{'F001':decision}},require_semantic_contract=True)
        self.assertEqual(out['blocking'][0]['adjudication'],'unavailable')
        self.assertEqual(out['blocking'][0]['decision_diagnostics']['invitation_kind'],'none')


class StandaloneApplicability(unittest.TestCase):
    draft='An unchanged complete reader-facing explanation.'
    assignments={'requirements':'judge_a','completeness':'judge_b','format_and_tone':'judge_c',
                 'factual_grounding':'source:cloud'}
    def verdict(self,issues):
        return {'pass':not issues,'status':'BLOCKED' if issues else 'PASS','issues':issues,
            'checks':dict.fromkeys(review.CHECKS,not issues),'review_errors':{},'review_state':'complete',
            'reviewer_models':dict(self.assignments),
            'quality_model_pool':['author','source:cloud','writer:cloud','judge_a','judge_b','judge_c']}
    def finding(self):
        policy=next(b for b in editorial_form_bindings() if b['id']=='G_clarity')
        return {'quote':self.draft,'reason':'The editor alleges a concrete clarity defect.',
            'check':'format_and_tone','field':'standalone','requirement_id':policy['id'],
            'requirement_quote':policy['quote'],'requirement_mode':'quality_policy'}
    def test_dismissed_finding_requires_full_new_review_and_never_rewrites_the_draft(self):
        issue=self.finding();initial=self.verdict([issue]);fresh=self.verdict([])
        def chat(body,**kw):
            data=json.loads(body['messages'][-1]['content'])
            self.assertEqual(data['artifact_fields']['standalone'],self.draft)
            self.assertEqual(body['model'],'source:cloud')
            return {'message':{'content':json.dumps({'decisions':{
                row['id']:{'action':'keep_artifact','remedy_target':'none','required_change':'','reason':'The explanation is coherent; the alleged problem is absent.'}
                for row in data['review_findings']}})}}
        with patch.object(review,'review_text',side_effect=[initial,fresh]) as reviewer, \
             patch.object(review,'repair_text') as repairer, \
             patch.object(review,'request_chat',side_effect=chat):
            result=review.run_quality_cycle('Write.',{'action':'write','answer':self.draft},{},model='author',
                reviewer=reviewer,repairer=repairer)
        self.assertEqual(result['action'],'write');repairer.assert_not_called()
        self.assertEqual(result['answer'],self.draft);self.assertEqual(reviewer.call_count,2)
        self.assertEqual(reviewer.call_args.kwargs['previous_reviewers'],self.assignments)
        self.assertTrue(result['quality_review']['full_recheck_after_finding_dismissal'])
        self.assertEqual(len(result['quality_review']['finding_adjudication']['dismissed']),1)

    def test_confirmed_findings_reach_a_different_writer_and_completed_source_findings_are_preserved(self):
        issue=self.finding();fact={'quote':self.draft,'reason':'A business promise is not sourced.',
            'check':'factual_grounding'}
        def chat(body,**kw):
            data=json.loads(body['messages'][-1]['content'])
            self.assertEqual(len(data['review_findings']),1)
            return {'message':{'content':json.dumps({'decisions':{
                row['id']:{'action':'correct_artifact','remedy_target':row['local_target'],'required_change':'Clarify the identified ambiguous reference.',
                    'reason':'The claimed clarity defect follows from the actual wording.'}
                for row in data['review_findings']}})}}
        with patch.object(review,'review_text',side_effect=[self.verdict([issue,fact]),self.verdict([])]) as reviewer, \
             patch.object(review,'repair_text',return_value='A revised explanation.') as repairer, \
             patch.object(review,'request_chat',side_effect=chat), \
             patch.object(review,'_reviewer_pool_is_prevalidated',return_value=True):
            result=review.run_quality_cycle('Write.',{'action':'write','answer':self.draft},{},model='author',
                reviewer=reviewer,repairer=repairer)
        self.assertEqual(result['action'],'write');self.assertTrue(result['quality_review']['repair_applied'])
        passed=repairer.call_args.args[2]
        self.assertTrue(any(r['check']=='factual_grounding' for r in passed))
        self.assertTrue(any(r.get('finding_validation',{}).get('decision')=='confirmed' for r in passed))
        self.assertNotEqual(repairer.call_args.kwargs['model'],'author')

    def test_confirmed_applicability_proof_survives_the_real_revision_contract(self):
        from text_task_contract import TextRevisionContract
        issue=self.finding();issue['finding_validation']={'model':'independent',
            'decision':'confirmed','reason':'This defect is independently established.'}
        revision=TextRevisionContract.create(self.draft,[issue])
        issue['finding_validation']['decision']='dismissed'
        self.assertEqual(revision.findings[0]['finding_validation']['decision'],'confirmed')

    def test_applicability_outage_blocks_rewrite_and_preserves_original_findings(self):
        issue=self.finding()
        with patch.object(review,'review_text',return_value=self.verdict([issue])) as reviewer, \
             patch.object(review,'repair_text') as repairer, \
             patch.object(review,'request_chat',side_effect=TimeoutError('Judge unavailable')) as wire:
            result=review.run_quality_cycle('Write.',{'action':'write','answer':self.draft},{},model='author',
                reviewer=reviewer,repairer=repairer)
        self.assertEqual(result['action'],'write_blocked');repairer.assert_not_called()
        self.assertEqual(result['answer'],self.draft);self.assertEqual(result['quality_review']['issues'],[{**issue,'severity':'major','adjudication':'unavailable'}])
        self.assertEqual(wire.call_count,1)


if __name__=='__main__':unittest.main()
