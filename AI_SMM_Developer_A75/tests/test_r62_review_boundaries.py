"""Malformed and crossed reviewer replies at real boundaries; no live quality claim."""
import copy
import io
import json
import random
import unittest
from unittest.mock import patch

from artifact_review import ArtifactReviewContract, ReviewContractError, validated_review_call
from claim_support import adjudicate_factual_findings, public_copy_findings
from claim_interpretation import bind_interpretation
from ollama_resilience import request_chat
from review_arbitration import adjudicate_findings, FindingReviewUnavailable
from semantic_editor import audit_post, audit_post_with_independent_model
import generic_text_review as text_review
import independent_acceptance_editor as document_review


def decision():
    return dict(statement_class='business_fact', speech_act='assertion', assertion_subject='business',
        source_relation='unrelated', verdict='unsupported', source_quote_id='none',
        invitation_kind='none', reason='The source does not establish this business assertion.')


def with_changes(**changes):
    value=decision();value.update(changes);return value


class SourceContractRecovery(unittest.TestCase):
    def test_uncertain_source_decision_never_authorizes_standalone_rewriting(self):
        draft='Мы выполняем отдельную процедуру.'
        calls=[]
        def chat(payload,**kw):
            user=json.loads(payload['messages'][1]['content']);calls.append(user)
            if 'findings' in user:
                value={'decisions':{row['finding_id']:with_changes(verdict='uncertain',
                    reason='The source is insufficient to decide the asserted procedure.') for row in user['findings']}}
            else:value={'pass':True,'issues':[]}
            return {'message':{'content':json.dumps(value)}}
        assignments=dict(zip(text_review.CHECKS,['r1','r2','r3','r4']))
        with patch('generic_text_review.request_chat',side_effect=chat):
            result=text_review.run_quality_cycle('Напиши пост.',{'action':'write','answer':draft},{},
                model='author',reviewer_models=assignments,
                repairer=lambda *a,**kw:self.fail('Uncertainty must not rewrite the author'))
        self.assertEqual(result['action'],'write_blocked')
        self.assertEqual(result['answer'],draft)
        self.assertIn('factual_grounding',result['quality_review']['review_errors'])
        self.assertFalse(result['quality_review']['issues'])
        self.assertGreaterEqual(len(calls),4)
        self.assertLessEqual(len(calls),12, 'Unbounded independent review/adjudication calls')
        self.assertTrue(all(isinstance(call,dict) for call in calls))

    def test_uncertain_strategy_source_decision_is_an_unavailable_review(self):
        issue={'document':'strategy','type':'invented_fact','quote':'Мы выполняем процедуру.',
            'reason':'Утверждение требует проверки по источнику.'}
        def uncertain(*a,**kw):return {'supported':[], 'blocking':[{**issue,'adjudication':'uncertain'}]}
        with patch('independent_acceptance_editor._call_editor_once',return_value={'pass':False,'issues':[issue]}):
            with self.assertRaises(document_review.IndependentReviewUnavailable):
                document_review.call_strategy_editor('Составь стратегию.',issue['quote'],claim_adjudicator=uncertain)

    def test_uncertain_invitation_scope_stays_uncertain(self):
        out=bind_interpretation(with_changes(statement_class='invitation_cta',speech_act='invitation',
            assertion_subject='reader',invitation_kind='uncertain',source_relation='not_needed',verdict='supported'),require=True)
        self.assertEqual((out['statement_class'],out['verdict']),('uncertain','uncertain'))

    def test_partial_semantic_error_retries_only_unresolved_id_and_keeps_sibling(self):
        request='Подтверждено: мастерская ремонтирует велосипеды.'
        findings=public_copy_findings('Our team guarantees a result. Describe your situation to us.')
        calls=[];events=[]
        def ask(model,system,user,**kw):
            value=json.loads(user);calls.append((system,value,kw['json_schema']))
            self.assertNotIn('Верни только quote_id',system)
            if len(calls)==1:
                return {'decisions':{'F001':decision(), 'F002':with_changes(
                    statement_class='invitation_cta',speech_act='invitation',assertion_subject='reader',
                    invitation_kind='communication',source_relation='instruction',verdict='supported')}}
            self.assertIn('source_relation описывает выбранную цитату',system)
            self.assertEqual([r['finding_id'] for r in value['findings']],['F002'])
            self.assertEqual(kw['json_schema']['properties']['decisions']['required'],['F002'])
            self.assertTrue(all(r['quote_id'].startswith('F002_') for r in value['authorized_quote_options']))
            return {'decisions':{'F002':with_changes(statement_class='invitation_cta',speech_act='invitation',
                assertion_subject='reader',invitation_kind='communication',source_relation='not_needed',verdict='supported')}}
        result=adjudicate_factual_findings(request,findings,ask=ask,require_semantic_contract=True,
            report=lambda stage,**kw:events.append((stage,kw)))
        self.assertEqual(len(calls),2)
        self.assertEqual([r['adjudication'] for r in result['blocking']],['unsupported'])
        self.assertEqual([r['quote'] for r in result['non_business']],[findings[1]['quote']])
        self.assertEqual(events[1][1]['retained_decisions'],1)

    def test_retry_cannot_override_completed_sibling_by_sending_its_id_again(self):
        calls=[]
        def ask(model,system,user,**kw):
            calls.append(user)
            return {'decisions':{'F001':decision()}}
        result=adjudicate_factual_findings('Составь текст.',public_copy_findings('First claim. Second claim.'),
            ask=ask,require_semantic_contract=True)
        self.assertEqual(len(calls),2)
        self.assertEqual({r['quote']:r['adjudication'] for r in result['blocking']},
            {'First claim.':'unsupported','Second claim.':'unavailable'})

    def test_service_access_is_a_business_assertion_even_when_reader_performs_action(self):
        for seed in range(30):
            rng=random.Random(seed)
            token=''.join(rng.choice('abcxyz') for _ in range(16))
            request=f'Подтверждённая услуга: диагностика {token} по предварительной записи.'
            text=f'Запишитесь на диагностику {token} по предварительной записи.'
            def ask(model,system,user,**kw):
                value=json.loads(user)
                option=next(r for r in value['authorized_quote_options'] if token in r['quote'])
                return {'decisions':{'F001':with_changes(statement_class='invitation_cta',
                    speech_act='invitation',assertion_subject='reader',invitation_kind='service_access',
                    source_relation='entails',source_quote_id=option['quote_id'],verdict='supported')}}
            out=adjudicate_factual_findings(request,public_copy_findings(text),ask=ask,require_semantic_contract=True)
            self.assertEqual(out['blocking'],[],out)
            self.assertEqual(len(out['supported']),1)
            self.assertEqual(out['non_business'],[])

    def test_service_access_without_citation_cannot_be_waived_as_reader_invitation(self):
        value=with_changes(statement_class='invitation_cta',speech_act='invitation',
            assertion_subject='reader',invitation_kind='service_access',source_relation='not_needed',verdict='supported')
        out=adjudicate_factual_findings('Составь текст.',public_copy_findings('Запишитесь на обслуживание.'),
            ask=lambda *a,**kw:{'decisions':{'F001':value}},require_semantic_contract=True)
        self.assertFalse(out['non_business'])
        self.assertEqual(out['blocking'][0]['adjudication'],'unsupported')

    def test_invalid_json_field_types_fail_closed_after_at_most_one_correction(self):
        for field in ('source_quote_id','statement_class','speech_act','assertion_subject','source_relation','reason'):
            for malformed in (None,[],{},True,7):
                calls=[]
                def ask(*a,**kw):
                    calls.append(1);return {'decisions':{'F001':with_changes(**{field:malformed})}}
                out=adjudicate_factual_findings('Составь текст.',public_copy_findings('One asserted detail.'),
                    ask=ask,require_semantic_contract=True)
                self.assertLessEqual(len(calls),2)
                self.assertEqual(out['blocking'][0]['adjudication'],'unavailable',(field,malformed,out))

    def test_gateway_tool_arguments_recover_bad_semantic_contract_on_production_adapter(self):
        calls=[]
        def wire(req,timeout):
            payload=json.loads(req.data);calls.append(payload)
            value=with_changes(statement_class='audience_prompt',speech_act='question',assertion_subject='reader',
                source_relation='instruction' if len(calls)==1 else 'not_needed',verdict='supported')
            answer={'decisions':{'F001':value}}
            return io.BytesIO(json.dumps({'message':{'tool_calls':[{'function':{
                'name':'return_structured_result','arguments':answer}}]}}).encode())
        def ask(model,system,user,*,json_schema):
            response=request_chat({'model':'reviewer:cloud','stream':False,'format':json_schema,
                'messages':[{'role':'system','content':system},{'role':'user','content':user}]},
                stage='source boundary',attempts=1)
            return response['message']['content']
        with patch('ollama_resilience.urllib.request.urlopen',side_effect=wire):
            out=adjudicate_factual_findings('Составь пост.',public_copy_findings('Any questions for your own plans?'),
                ask=ask,require_semantic_contract=True)
        self.assertEqual(len(calls),2)
        self.assertFalse(out['blocking'])
        self.assertEqual(len(out['non_business']),1)

    def test_transport_outage_does_not_start_a_semantic_retry(self):
        calls=[]
        def ask(*a,**kw):calls.append(1);raise ConnectionError('Offline transport')
        out=adjudicate_factual_findings('Составь пост.',public_copy_findings('Some specific statement.'),
            ask=ask,require_semantic_contract=True)
        self.assertEqual(calls,[1]);self.assertEqual(out['blocking'][0]['adjudication'],'unavailable')


class PublicationContractRecovery(unittest.TestCase):
    post={'caption':'A specific complete sentence.'}

    def test_actual_auditor_corrects_empty_rejection_then_retains_valid_negative_verdict(self):
        calls=[];events=[]
        def ask(model,system,user,*,json_schema):
            calls.append(user)
            self.assertEqual(user,calls[0])
            if len(calls)==1:return '{"approved":false,"issues":[]}'
            self.assertIn('без конкретного замечания',system)
            return json.dumps({'approved':False,'issues':[{'field':'caption','evidence_id':json_schema['properties']['issues']['items']['properties']['evidence_id']['enum'][0],
                'kind':'quality','requirement_id':'none','reason':'This exact sentence has a concrete editorial defect.'}]})
        post=copy.deepcopy(self.post)
        reviewed,issues=audit_post(ask,'reviewer','Write a post.',{}, {},post,
            report=lambda stage,**kw:events.append((stage,kw)))
        self.assertIsNone(reviewed);self.assertEqual(len(calls),2)
        self.assertEqual(issues[0]['quote'],post['caption']);self.assertEqual(post,self.post)
        self.assertEqual(events[0][0],'review_contract_invalid')

    def test_invalid_recheck_preserves_original_findings_and_specific_cause(self):
        finding={'field':'caption','quote':self.post['caption'],'reason':'Potential defect in this exact sentence.'}
        events=[]
        def audit(*args,**kw):
            if args[1]=='primary':return None,[finding]
            raise ValueError('Противоречивый вердикт редактора')
        with self.assertRaises(FindingReviewUnavailable) as caught:
            audit_post_with_independent_model(None,'author',['primary','backup'],'Write.',{}, {},self.post,
                audit_function=audit,adjudicate_function=lambda *a,**kw:([],[finding]),
                report=lambda stage,**kw:events.append((stage,kw)))
        self.assertIn('Противоречивый вердикт',str(caught.exception))
        self.assertEqual(caught.exception.findings,[finding])
        self.assertTrue(any(stage=='publication_full_recheck_failed' for stage,_ in events))

    def test_malformed_field_reference_types_cannot_escape_as_uncaught_type_error(self):
        contract=ArtifactReviewContract.create('Write.',self.post)
        for key in ('field','evidence_id','kind','requirement_id','reason'):
            for bad in (None,[],{},True,11):
                item={'field':'caption','evidence_id':contract.spans[0].ident,'kind':'quality','requirement_id':'none',
                    'reason':'A concrete explained defect.'};item[key]=bad
                with self.assertRaises(ValueError):contract.validate({'approved':False,'issues':[item]})

    def test_valid_negative_verdict_needs_no_retry_and_transport_failure_is_not_retried(self):
        for transport in (False,True):
            calls=[]
            def ask(*a,**kw):
                calls.append(1)
                if transport:raise TimeoutError('Transport timed out')
                return '{"valid":false}'
            if transport:
                with self.assertRaises(TimeoutError):validated_review_call(ask,'r','s','u',{},lambda x:x)
            else:self.assertEqual(validated_review_call(ask,'r','s','u',{},lambda x:x),{'valid':False})
            self.assertEqual(calls,[1])

    def test_two_invalid_verdicts_raise_explicit_contract_failure(self):
        calls=[]
        def ask(*a,**kw):calls.append(1);return '{"approved":false,"issues":[]}'
        with self.assertRaises(ReviewContractError):audit_post(ask,'r','Write.',{}, {},self.post)
        self.assertEqual(len(calls),2)

    def test_dismissal_cannot_repeat_a_rejection_reason_instead_of_explaining_an_error(self):
        finding={'field':'caption','quote':self.post['caption'],'reason':'This exact sentence fails the content goal.'}
        calls=[]
        def ask(*a,**kw):
            calls.append(a[2])
            return {'decisions':{'A001':{'action':'keep_artifact' if len(calls)==1 else 'correct_artifact',
                'remedy_target':'none' if len(calls)==1 else 'caption',
                'required_change':'' if len(calls)==1 else 'Correct the recorded defect.',
                'reason':finding['reason']}}}
        confirmed,dismissed=adjudicate_findings(ask,'judge','Write.',self.post,[finding])
        self.assertEqual(len(calls),2);self.assertFalse(dismissed);self.assertEqual(len(confirmed),1)


if __name__=='__main__':unittest.main()
