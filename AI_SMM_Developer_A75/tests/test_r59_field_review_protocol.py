"""Boundary and recovery regressions; injected replies do not certify live quality."""
import copy
import io
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from artifact_review import ArtifactReviewContract, deduplicate_findings
import model_protocol as protocol
from ollama_resilience import request_chat, StructuredResponseError
from semantic_editor import audit_post, audit_post_with_independent_model
from workflow_recovery import capture_failures
import compound_tasks as ct
from test_stage69_scope import good_post, mini_plan


class FieldReviewTests(unittest.TestCase):
    def test_random_fields_and_topics_keep_exact_host_owned_quotes(self):
        rng=random.Random(5901)
        for n in range(80):
            field='field_'+str(rng.randrange(10**9))
            text=''.join(rng.choice('abcабв123') for _ in range(60))
            contract=ArtifactReviewContract.create('Review the artifact.',{field:text})
            value={'approved':False,'issues':[{'field':field,'evidence_id':contract.spans[0].ident,
                'kind':'quality','requirement_id':'none','reason':'A specific quality problem.'}]}
            issues,dismissed=contract.validate(value)
            self.assertEqual(issues[0]['quote'],text)
            self.assertFalse(dismissed)
            value['issues'][0]['field']='another_field'
            with self.assertRaises(ValueError):contract.validate(value)

    def test_planning_and_formatting_are_not_business_fact_assertions(self):
        c=ArtifactReviewContract.create('Create a post.',{'visual':'A proposed studio scene.','caption':'🐶🐱'},
            {'visual':'visual','caption':'caption'})
        for field,ident in ((s.field,s.ident) for s in c.spans):
            row={'field':field,'evidence_id':ident,'kind':'unsupported_fact',
                'requirement_id':'none','reason':'The source does not mention this.'}
            issues,dismissed=c.validate({'approved':False,'issues':[row]})
            self.assertFalse(issues);self.assertEqual(len(dismissed),1)
            row['kind']='unsafe_advice'
            self.assertEqual(len(c.validate({'approved':False,'issues':[row]})[0]),1)
            row['kind']='requirement_violation'
            with self.assertRaises(ValueError):c.validate({'approved':False,'issues':[row]})

    def test_real_requirements_and_business_assertions_still_block(self):
        c=ArtifactReviewContract.create('Use only authentic photographs.',{'visual':'Proposed scene.','caption':'Our company guarantees success.'},
            {'visual':'visual','caption':'caption'})
        ref=c.requirements[0][0]
        row={'field':'visual','evidence_id':next(s.ident for s in c.spans if s.field=='visual'),'kind':'requirement_violation',
            'requirement_id':ref,'reason':'Authentic photographs are required.'}
        self.assertEqual(len(c.validate({'approved':False,'issues':[row]})[0]),1)
        row.update(field='caption',evidence_id=next(s.ident for s in c.spans if s.field=='caption'),kind='unsupported_fact',requirement_id='none')
        self.assertEqual(len(c.validate({'approved':False,'issues':[row]})[0]),1)

    def test_review_cannot_write_its_own_approved_text(self):
        post={'topic':'Topic','caption':'Original immutable bytes.'}
        for strict in (False,True):
            answer=json.dumps({'approved':True,'issues':[],'revised_post':{'caption':'Own edit'}})
            def three(a,b,c):return answer
            def typed(a,b,c,**kw):return answer
            if strict:
                with self.assertRaises(ValueError):audit_post(typed,'judge','Task',{}, {},post)
            else:
                self.assertIsNone(audit_post(three,'judge','Task',{}, {},post)[0])
        self.assertEqual(post['caption'],'Original immutable bytes.')

    def test_bad_evidence_or_malformed_result_never_approves(self):
        c=ArtifactReviewContract.create('Task',{'text':'Original bytes.'})
        row={'field':'text','evidence_id':'E9999','kind':'quality','requirement_id':'none','reason':'Concrete problem.'}
        for value in ({'approved':False,'issues':[row]}, {'approved':True,'issues':[row]},
                      {'approved':False,'issues':[]}, {'approved':True,'issues':[],'extra':1}):
            with self.assertRaises(ValueError):c.validate(value)
        calls=[]
        def bad(*args,**kwargs):calls.append(args[1]);return None,[]
        with self.assertRaises(ValueError):
            audit_post_with_independent_model(None,'writer',['judge','backup'],'Task',{}, {},{},audit_function=bad)
        self.assertEqual(calls,['judge','backup'])

    def test_no_same_family_fallback_and_previous_topics_are_forwarded(self):
        with self.assertRaises(ValueError):
            audit_post_with_independent_model(None,'writer:8b',['writer:cloud'],'Task',{}, {},{})
        seen=[]
        def audit(*args,previous_topics=()):seen.extend(previous_topics);return args[5],[]
        audit_post_with_independent_model(None,'writer',['judge'],'Task',{}, {},{},
            previous_topics=['Earlier topic'],audit_function=audit)
        self.assertEqual(seen,['Earlier topic'])

    def test_mixed_findings_deduplicate_without_losing_typed_evidence(self):
        row={'quote':'Exact','reason':'Unverified fact','field':'caption'}
        values=deduplicate_findings([row,'warning',copy.deepcopy(row),'warning'])
        self.assertEqual(values,[row,'warning']);values[0]['quote']='Changed'
        self.assertEqual(row['quote'],'Exact')

    def test_topic_finding_requests_plan_recovery(self):
        slot={'week':1,'day':'Вт','topic':'Original topic'}
        issue={'field':'topic','quote':slot['topic'],'reason':'Topic misses the objective.'}
        records=capture_failures('1'*64,['1/Вт'],[slot],{},True,
            {'1/Вт':{'post':{'id':'1/Вт','caption':'Draft'},'issues':[issue]}},set())
        self.assertEqual(records[0]['stage'],'plan')
        self.assertEqual(records[0]['artifact'],slot)

    def test_same_named_model_via_another_provider_is_not_independent(self):
        from model_roles import model_family
        for model in ('openai/shared-model','groq/shared-model','openrouter/vendor/shared-model'):
            self.assertEqual(model_family(model),model_family('shared-model:cloud'))
            with self.assertRaises(ValueError):
                audit_post_with_independent_model(None,'shared-model:cloud',[model],'Task',{}, {},{})

    def test_partial_backup_outage_preserves_primary_findings_without_promoting_trust(self):
        from production_pipeline import run_document_task
        from independent_acceptance_editor import ValidatedEditorialVerdict
        issue={'document':'strategy','type':'quality','quote':'Exact quote','reason':'Specific concern.'}
        primary={'pass':False,'issues':[issue,{'type':'review_unavailable','document':'review'}],
            'review_state':'unavailable'}
        backup=ValidatedEditorialVerdict({'pass':False,'issues':[{'type':'review_unavailable','document':'review'}],
            'review_state':'unavailable'})
        def gate(result,request,review):return {'captured_verdict':review(request,'Strategy','Calendar')}
        with patch('generic_text_review.select_independent_model',side_effect=['judge','backup']), \
             patch('production_pipeline.run_compound_with_retry',return_value={'documents':[]}), \
             patch('production_pipeline.final_quality_gate',side_effect=gate), \
             patch('independent_acceptance_editor.call_editor',side_effect=[primary,backup]):
            result=run_document_task('Task',{},model='writer')
        verdict=result['captured_verdict']
        self.assertIn({**issue,'adjudication':'unavailable'},verdict['issues']);self.assertFalse(verdict['pass'])
        self.assertEqual(verdict['review_state'],'unavailable')
        self.assertNotIsInstance(verdict,ValidatedEditorialVerdict)


class ModelProtocolTests(unittest.TestCase):
    def setUp(self):
        self.routes=patch.object(protocol,'_PROMPT_ROUTES',set());self.routes.start()
        self.metadata=patch.object(protocol,'_METADATA',{});self.metadata.start()
        self.addCleanup(self.routes.stop);self.addCleanup(self.metadata.stop)

    def test_metadata_controls_thinking_and_schema_allowance_without_mutation(self):
        protocol.register_model_metadata('arbitrary',{'thinking':{'values':['light','deep'],'default':'light'}})
        schema={'type':'object','properties':{'findings':{'type':'array','maxItems':10,
            'items':{'type':'string','maxLength':500}}}}
        payload={'model':'arbitrary','think':False,'format':schema,'options':{'num_predict':1200}}
        before=copy.deepcopy(payload);result=protocol.prepare_model_payload(payload)
        self.assertEqual(payload,before);self.assertNotIn('think',result)
        self.assertGreater(result['options']['num_predict'],1200)
        self.assertLessEqual(result['options']['num_predict'],4096)
        payload['options']['num_predict']=8000
        self.assertEqual(protocol.prepare_model_payload(payload)['options']['num_predict'],8000)
        protocol.register_model_metadata('arbitrary',{'thinking':{'values':[True,False]}})
        self.assertEqual(protocol.prepare_model_payload(payload),payload)

    def test_gpt_oss_uses_supported_low_and_does_not_inflate_small_probe(self):
        payload={'model':'gpt-oss:20b-cloud','think':False,'format':{'type':'boolean'},'options':{'num_predict':1024}}
        result=protocol.prepare_model_payload(payload)
        self.assertEqual(result['think'],'low');self.assertEqual(result['options']['num_predict'],1024)

    def call(self,responses,attempts=2):
        seen=[];events=[]
        def transport(req,timeout):
            seen.append(json.loads(req.data));return io.BytesIO(json.dumps(responses.pop(0)).encode())
        payload={'model':'random:cloud','format':{'type':'object','required':['value'],
            'properties':{'value':{'type':'string'}}},'messages':[{'role':'user','content':'Task'}]}
        with patch('ollama_resilience.urllib.request.urlopen',side_effect=transport):
            result=request_chat(payload,stage='review',attempts=attempts,
                report=lambda stage,**kw:events.append(kw))
        return result,seen,events

    def test_completed_empty_tool_negotiates_within_existing_attempt_limit(self):
        result,seen,events=self.call([{'message':{'content':''},'done_reason':'stop'},
            {'message':{'content':'{"value":"Exact: punctuation"}'},'done_reason':'stop'}])
        self.assertEqual(len(seen),2);self.assertIn('tools',seen[0]);self.assertNotIn('tools',seen[1])
        self.assertEqual(json.loads(result['message']['content'])['value'],'Exact: punctuation')
        self.assertEqual([e['outcome'] for e in events],['invalid_response','ok'])

    def test_no_negotiation_on_truncation_malformed_or_semantic_rejection(self):
        for response in ({'message':{'content':''},'done_reason':'length'},
                         {'message':{'content':'broken'},'done_reason':'stop'}):
            with self.assertRaises(StructuredResponseError):self.call([response])
            self.assertFalse(protocol._PROMPT_ROUTES)
        result,seen,events=self.call([{'message':{'content':'{"value":"Rejected content"}'},'done_reason':'stop'}])
        self.assertEqual(len(seen),1);self.assertFalse(protocol._PROMPT_ROUTES)

    def test_empty_prompt_route_is_invalid_and_never_executes_tools(self):
        schema={'type':'object','required':['value'],'properties':{'value':{'type':'string'}}}
        protocol.note_empty_tool_result('random:cloud',schema,{'message':{},'done_reason':'stop'})
        for message in ({'content':''},{'tool_calls':[{'function':{'name':'shell','arguments':{}}}]}):
            with self.assertRaises(StructuredResponseError):self.call([{'message':message}],attempts=1)

    def test_single_attempt_cannot_create_an_extra_network_call(self):
        with self.assertRaises(StructuredResponseError):
            self.call([{'message':{'content':''},'done_reason':'stop'}],attempts=1)


    def test_completed_invalid_tool_contract_retries_as_checked_json(self):
        # Production editor makes two bounded requests; first rejected tool
        # payload must never be returned as an approved editorial decision.
        invalid={'message':{'tool_calls':[{'function':{'name':'return_structured_result',
            'arguments':{'pass':True,'issues':[]}}}]},'done_reason':'stop'}
        valid={'message':{'content':'{"value":"Reviewed only after valid JSON"}'},'done_reason':'stop'}
        answer,requests,events=self.call([invalid,valid],attempts=2)
        self.assertEqual(json.loads(answer['message']['content']),
                         {'value':'Reviewed only after valid JSON'})
        self.assertIn('tools',requests[0]);self.assertNotIn('tools',requests[1])
        self.assertEqual([e['outcome'] for e in events],['invalid_response','ok'])
        self.assertEqual(events[0]['response_diagnostics']['next_structured_route'],'prompt_json')

    def test_bad_tool_response_single_attempt_does_not_succeed(self):
        invalid={'message':{'tool_calls':[{'function':{'name':'return_structured_result',
            'arguments':{'unrequested':True}}}]},'done_reason':'stop'}
        with self.assertRaises(StructuredResponseError):self.call([invalid],attempts=1)
        # Subsequent ordinary request may use JSON transport, but is still
        # checked against the exact host-issued contract.
        with self.assertRaises(StructuredResponseError):
            self.call([{'message':{'content':'{"unrequested":true}'},'done_reason':'stop'}],attempts=1)
        good,reqs,_=self.call([{'message':{'content':'{"value":"Checked"}'},'done_reason':'stop'}],attempts=1)
        self.assertNotIn('tools',reqs[0]);self.assertEqual(json.loads(good['message']['content'])['value'],'Checked')

    def test_truncated_or_foreign_tool_never_negotiates(self):
        for reply in (
            {'message':{'tool_calls':[{'function':{'name':'return_structured_result',
                'arguments':{'unrequested':1}}}]},'done_reason':'length'},
            {'message':{'tool_calls':[{'function':{'name':'system_command',
                'arguments':{'unrequested':1}}}]},'done_reason':'stop'},
        ):
            with self.subTest(reply=reply),self.assertRaises(StructuredResponseError):
                self.call([reply],attempts=1)
            self.assertFalse(protocol._PROMPT_ROUTES)

    def test_configured_central_model_not_stale_default(self):
        from central_dialogue_transport import default_central_model
        opts={'AI_SMM_ENABLE_OPENAI_API':'1','AI_SMM_CLOUD_MODELS':'gemini/model, openai/gpt-6-luna'}
        self.assertEqual(default_central_model(opts),'openai/gpt-6-luna')
        self.assertEqual(default_central_model({**opts,'AI_SMM_CENTRAL_MODEL':'openai/custom'}),'openai/custom')
        self.assertEqual(default_central_model({'AI_SMM_CLOUD_MODELS':'openai/gpt-6-luna'}),'openai/gpt-5')
        self.assertEqual(default_central_model({**opts,'AI_SMM_ENABLE_OPENAI_API':'0'}),'openai/gpt-5')


class RejectedPublicationDataflowTests(unittest.TestCase):
    def test_actual_batch_rejection_persists_exact_draft_and_feeds_targeted_repair(self):
        request='Разработай SMM-стратегию и контент-план на 1 неделю, 2 публикации в неделю, всего 2 публикации для Instagram.'
        strategy='# Стратегия\n'+('Цели. Аудитория. Каналы. Метрики. План действий. '*25)
        captured=[];originals=[]
        def ask(model,system,user,**kwargs):
            value=json.loads(user)
            if 'review_findings' in value:
                return json.dumps({'decisions':{r['id']:{'action':'correct_artifact','remedy_target':r['local_target'],'required_change':'Correct the identified caption defect.',
                    'reason':'The independently checked editorial finding remains valid.'} for r in value['review_findings']}})
            if 'artifact_evidence' in value:
                evidence=next(x for x in value['artifact_evidence'] if x['field']=='caption')
                return json.dumps({'approved':False,'issues':[{'field':'caption','evidence_id':evidence['evidence_id'],
                    'kind':'quality','requirement_id':'none','reason':'Exact actionable editorial finding.'}]})
            raise AssertionError('Unexpected model request')
        def writer(ask,model,req,known,body,slots,*args,**kwargs):
            posts=[good_post(s) for s in slots];originals.extend(copy.deepcopy(posts));return posts
        def repair(*args,**kwargs):
            captured.append(copy.deepcopy(args));raise ValueError('Injected author unavailable')
        def plan(*args,**kwargs):
            kwargs.pop('first_pass_only',None);return mini_plan(*args,**kwargs)
        with tempfile.TemporaryDirectory() as home:
            with patch.object(ct,'analyze',return_value={'channels':'Instagram','channels_basis':'explicit'}), \
                 patch.object(ct,'produce_checked_strategy',return_value=(strategy,True,'')), \
                 patch.object(ct,'plan_slots',side_effect=plan), \
                 patch.object(ct,'govern_plan',side_effect=lambda a,m,r,k,b,s,*v,**kw:(s,{},True)), \
                 patch.object(ct,'write_week',side_effect=writer), \
                 patch.object(ct,'write_single',side_effect=ValueError('Injected writer unavailable')), \
                 patch.object(ct,'repair_single',side_effect=repair), \
                 patch.object(ct,'_ask',side_effect=ask), \
                 patch.object(ct,'editorial_audit',return_value=[]), \
                 patch.object(ct,'copy_too_similar',return_value=False):
                result=ct.execute_compound(request,{'project_workspace':home,'plan_reviewer_models':['judge','validator']},model='writer')
            self.assertFalse(result['documents'][-1]['valid'])
            records=result['recovery_context']['rejected_posts']
            self.assertEqual(len(records),2)
            for post in originals:
                record=records[post['id']]
                self.assertEqual(record['post']['caption'],post['caption'])
                self.assertEqual(record['issues'][0]['quote'],post['caption'])
                self.assertEqual(record['issues'][0]['reason'],'Exact actionable editorial finding.')
            self.assertTrue(captured,'The actual correction path must receive the saved finding')
            self.assertIn('Exact actionable editorial finding.',repr(captured))
            self.assertNotIn('Ещё не прошла проверку пакетная публикация',repr(records))
            saved=list((Path(home)/'drafts').glob('publication_repair_*.json'))
            self.assertTrue(saved,'Rejected work must survive restart on disk')

if __name__=='__main__':unittest.main()
