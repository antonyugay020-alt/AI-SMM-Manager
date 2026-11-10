"""Whole boundary checks, with injected replies; not live semantic certification."""
import copy
import io
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_runtime as runtime
import compound_tasks as ct
from ollama_resilience import request_chat,StructuredResponseError,_MODEL_CALL_ROLE
from review_arbitration import adjudicate_findings,FindingReviewUnavailable
from semantic_editor import audit_post_with_independent_model
from generic_text_review import select_independent_reviewers
from publication_schema import POST_FIELDS,publication_field_type
from deliverable_contract import review_requirement_bindings
from test_stage69_scope import good_post,mini_plan


class RouterBoundaryTests(unittest.TestCase):
    def transport(self,arguments):
        return io.BytesIO(json.dumps({'message':{'tool_calls':[{'function':{
            'name':'return_structured_result','arguments':arguments}}]}}).encode())

    def test_direct_cloud_task_arguments_reach_the_actual_router_without_wrapper(self):
        seen=[]
        value={'action':'write','answer':'A complete useful draft.','start':None,'end':None,'content_contract':{'mode':'factual'}}
        def send(req,timeout):
            sent=json.loads(req.data);seen.append(sent)
            self.assertEqual(sent['tools'][0]['function']['parameters'],runtime.TASK_PLAN_SCHEMA)
            return self.transport(value)
        with patch('ollama_resilience.urllib.request.urlopen',side_effect=send):
            out=runtime.plan_task('Напиши один материал.',{},model='writer:cloud')
        self.assertEqual(out['answer'],value['answer'])
        self.assertEqual(out['instruction'],'Напиши один материал.')
        self.assertEqual(len(seen),1)

    def test_arbitrary_json_objects_preserve_literal_result_field(self):
        rng=random.Random(6101)
        for index in range(80):
            value={'result':{'key_'+str(index):''.join(rng.choice('abcабв{}[]🐾') for _ in range(35))}}
            with patch('ollama_resilience.urllib.request.urlopen',return_value=self.transport(value)):
                out=request_chat({'model':'writer:cloud','format':'json','messages':[]},stage='root',attempts=1)
            self.assertEqual(json.loads(out['message']['content']),value)

    def test_non_object_root_keeps_only_its_explicit_transport_envelope(self):
        for argument in ({'result':['a','b']},{'unexpected':['a','b']}):
            with patch('ollama_resilience.urllib.request.urlopen',return_value=self.transport(argument)):
                if 'result' in argument:
                    out=request_chat({'model':'writer:cloud','format':{'type':'array','items':{'type':'string'}},'messages':[]},stage='array',attempts=1)
                    self.assertEqual(json.loads(out['message']['content']),['a','b'])
                else:
                    with self.assertRaises(StructuredResponseError):
                        request_chat({'model':'writer:cloud','format':{'type':'array'},'messages':[]},stage='array',attempts=1)

    def test_root_contract_is_identical_for_tool_and_plain_json_routes(self):
        from ollama_resilience import _prepare_response_contract
        schemas=[('json',{'ok':True}),({'type':'array'},['a','b']),
                 ({'type':'object','required':['value'],'properties':{'value':{}}},{'value':42})]
        for contract,value in schemas:
            payload={'model':'writer:cloud','format':contract,'messages':[]}
            sent,_=_prepare_response_contract(payload)
            prompt=sent['messages'][0]['content']
            if isinstance(contract,dict) and contract.get('type')=='array':
                self.assertIn('Передай единственный аргумент result',prompt)
                self.assertNotIn('Не добавляй обёртки',prompt)
            else:
                self.assertIn('Не добавляй обёртки',prompt)
            raw={'message':{'content':json.dumps(value)}}
            with patch('ollama_resilience.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(raw).encode())):
                out=request_chat(payload,stage='plain root',attempts=1)
                self.assertEqual(json.loads(out['message']['content']),value)
        for contract,bad in (('json',[]),({'type':'array'},{}),
                             ({'type':'object','required':['value']},{})):
            events=[]
            raw={'message':{'content':json.dumps(bad)}}
            with patch('ollama_resilience.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(raw).encode())):
                with self.assertRaises(StructuredResponseError):
                    request_chat({'model':'writer:cloud','format':contract,'messages':[]},
                        stage='bad root',attempts=1,report=lambda stage,**kw:events.append(kw))
            self.assertFalse(any(e['outcome']=='ok' for e in events))

    def test_host_checks_required_fields_types_and_media_interval(self):
        valid={'action':'write','answer':'Useful draft','start':None,'end':None,'content_contract':{'mode':'factual'}}
        for change in ({'action':[]},{'action':'delete_everything'},{'answer':{}},{'answer':''},
                       {'start':True},{'end':float('inf')},{'content_contract':{'mode':'unknown'}},
                       {'extra':1},{'instruction':[]},{'action':'video_trim'},
                       {'action':'video_trim','start':5,'end':4}):
            with self.subTest(change=change),self.assertRaises(ValueError):runtime.validate_task_plan({**valid,**change})
        for key in runtime.TASK_PLAN_SCHEMA['required']:
            value=dict(valid);value.pop(key)
            with self.assertRaises(ValueError):runtime.validate_task_plan(value)
        self.assertEqual(runtime.validate_task_plan({**valid,'action':'video_trim','start':0,'end':2})['end'],2)

    def test_single_publication_writer_receives_the_complete_host_owned_slot(self):
        from publication_writer import write_single
        slot={'week':3,'day':'Пт','topic':'Точная тема','format':'Пост','platform':'Telegram',
            'brief':'Полезное объяснение','goal':'Задать вопрос','audience':'Начинающие'}
        def ask(model,system,user,**kw):
            value=json.loads(user)
            self.assertEqual(value['approved_slot'],{**slot,'id':'3/Пт'})
            self.assertEqual(value['original_request'],'Исходное задание')
            return json.dumps(good_post(slot),ensure_ascii=False)
        out=write_single(ask,'writer','Исходное задание',{},'derived, not a source',slot)
        self.assertEqual(out['id'],'3/Пт')

    def test_canonical_field_roles_do_not_depend_on_display_label_matching(self):
        for field in POST_FIELDS:self.assertEqual(publication_field_type(field),field)
        self.assertEqual(publication_field_type('materials'),'materials')

    def test_comparison_uses_independent_cloud_judges_and_balances_reuse(self):
        authors=['writer-'+str(n) for n in range(8)]
        for seed in range(30):
            judges=select_independent_reviewers(authors,candidates=[*authors,'cloud-a:cloud','cloud-b:cloud'],chooser=random.Random(seed))
            self.assertTrue(set(judges.values())<={'cloud-a:cloud','cloud-b:cloud'})
            self.assertLessEqual(abs(list(judges.values()).count('cloud-a:cloud')-4),1)


class FindingApplicabilityTests(unittest.TestCase):
    request='Выбери показатель и способ измерения без произвольной числовой цели.'
    fields={'kpi':'Количество комментариев','caption':'Полезное объяснение для читателя.'}
    def finding(self):
        binding=review_requirement_bindings(self.request)[0]
        return {'field':'kpi','quote':self.fields['kpi'],'reason':'Название показателя ошибочно объявлено числовой целью.',
            'type':'missing_required_content','requirement_id':binding['id'],'requirement_quote':binding['quote']}

    def test_a_cited_requirement_does_not_itself_certify_the_editors_reasoning(self):
        issue=self.finding();saved=copy.deepcopy(issue)
        def ask(model,system,user,**kw):
            data=json.loads(user)
            self.assertEqual(data['review_findings'][0]['finding'],issue)
            self.assertEqual(data['requirements'][issue['requirement_id']],issue['requirement_quote'])
            return {'decisions':{'A001':{'action':'keep_artifact','remedy_target':'none','required_change':'','reason':'Название величины не задаёт её целевое значение.'}}}
        confirmed,dismissed=adjudicate_findings(ask,'independent',self.request,self.fields,[issue])
        self.assertFalse(confirmed);self.assertEqual(dismissed[0]['quote'],issue['quote'])
        self.assertEqual(issue,saved)

    def test_forged_and_incomplete_decisions_never_approve(self):
        for raw in ({'decisions':{}},{'decisions':{'A999':{'decision':'dismissed','reason':'Wrong ID'}}},
                    {'decisions':{'A001':{'decision':'approved','reason':'Wrong decision'}}},
                    {'decisions':{'A001':{'decision':'dismissed','reason':'short'}}},
                    {'decisions':{'A001':{'decision':'dismissed','reason':'Specific explanation.','revised_text':'Own edit'}}}):
            with self.subTest(raw=raw),self.assertRaises(FindingReviewUnavailable) as caught:
                adjudicate_findings(lambda *a,**k:raw,'judge',self.request,self.fields,[self.finding()])
            self.assertTrue(caught.exception.findings)

    def test_uncertainty_keeps_findings_and_cannot_authorize_rewrite(self):
        raw={'decisions':{'A001':{'action':'review_unavailable','remedy_target':'none','required_change':'','reason':'The cited requirement does not establish a clear answer.'}}}
        with self.assertRaises(FindingReviewUnavailable) as caught:
            adjudicate_findings(lambda *a,**k:raw,'judge',self.request,self.fields,[self.finding()])
        self.assertEqual(caught.exception.findings[0]['finding_validation']['decision'],'uncertain')

    def test_foreign_field_or_requirement_stops_before_model_call(self):
        for issue in ({**self.finding(),'field':'foreign'},{**self.finding(),'quote':'Not in text'},
                      {**self.finding(),'requirement_quote':'Altered source'}):
            ask=lambda *a,**k:self.fail('Invalid source binding must stop before transport')
            with self.assertRaises(ValueError):adjudicate_findings(ask,'judge',self.request,self.fields,[issue])


class PublicationReviewChainTests(unittest.TestCase):
    def run_review(self,decision='dismissed',fresh='pass'):
        post={'id':'1/Вт','topic':'Тема','caption':'Полезное объяснение.','kpi':'Количество комментариев'}
        calls=[];events=[]
        def ask(model,system,user,**kw):
            value=json.loads(user);calls.append((model,_MODEL_CALL_ROLE.get(),value))
            if 'review_findings' in value:
                return json.dumps({'decisions':{row['id']:{'action':{'confirmed':'correct_artifact','dismissed':'keep_artifact','uncertain':'review_unavailable'}[decision],
                    'remedy_target':row['local_target'] if decision=='confirmed' else 'none',
                    'required_change':'Correct the recorded defect.' if decision=='confirmed' else '',
                    'reason':'A concrete independently checked rationale.'} for row in value['review_findings']}})
            if model=='primary':
                evidence=next(r for r in value['artifact_evidence'] if r['field']=='kpi')
                return json.dumps({'approved':False,'issues':[{'field':'kpi','evidence_id':evidence['evidence_id'],
                    'kind':'quality','requirement_id':'none','reason':'An alleged defect in this observable metric.'}]})
            if fresh=='broken':return 'not JSON'
            return json.dumps({'approved':True,'issues':[]})
        try:
            result=audit_post_with_independent_model(ask,'writer',['primary','secondary'],'Сделай публикацию.',{}, {},post,
                report=lambda stage,**kw:events.append((stage,kw)))
        except Exception as exc:return exc,calls,post,events
        return result,calls,post,events

    def test_dismissal_requires_a_complete_recheck_before_acceptance(self):
        result,calls,post,events=self.run_review()
        self.assertEqual([r[0] for r in calls],['primary','secondary','secondary'])
        self.assertEqual([r[1] for r in calls],['reviewer','reviewer_backup','reviewer_backup'])
        reviewed,issues=result;self.assertFalse(issues)
        self.assertTrue(reviewed['_review_adjudication']['full_independent_recheck'])
        for field,value in post.items():self.assertEqual(reviewed[field],value)
        self.assertEqual(events[-1][1]['model'],'secondary')

    def test_confirmed_finding_cannot_be_overruled_by_a_bare_backup_pass(self):
        result,calls,post,events=self.run_review('confirmed')
        self.assertEqual(len(calls),2)
        reviewed,issues=result
        self.assertEqual(issues[0]['finding_validation']['decision'],'confirmed')
        self.assertEqual(issues[0]['quote'],post['kpi'])

    def test_uncertain_adjudication_or_broken_full_recheck_stays_unavailable(self):
        for decision,fresh in (('uncertain','pass'),('dismissed','broken')):
            result,calls,post,events=self.run_review(decision,fresh)
            self.assertIsInstance(result,FindingReviewUnavailable)
            self.assertFalse(any(e[0]=='publication_reviewer_completed' and e[1]['status']=='PASS' for e in events))

    def test_reviewer_cannot_mutate_the_input_or_approve_its_own_edit(self):
        post={'caption':'Original bytes'};seen=[]
        def audit(ask,model,request,facts,slot,value,*args):
            seen.append((model,value['caption']))
            if model=='primary':value['caption']='Own edit'
            return value,[]
        out,issues=audit_post_with_independent_model(None,'writer',['primary','secondary'],'Request',{}, {},post,audit_function=audit)
        self.assertEqual(post['caption'],'Original bytes')
        self.assertEqual(out,post);self.assertFalse(issues)
        self.assertEqual(seen,[('primary','Original bytes'),('secondary','Original bytes')])

    def test_actual_compound_chain_resolves_invalid_finding_before_any_author_repair(self):
        request='Разработай SMM-стратегию и контент-план на 1 неделю, 2 публикации в неделю, всего 2 публикации для Instagram.'
        strategy='# Стратегия\n'+('Цели. Аудитория. Каналы. Метрики. План действий. '*25)
        def ask(model,system,user,**kw):
            value=json.loads(user)
            if 'review_findings' in value:
                return json.dumps({'decisions':{row['id']:{'action':'keep_artifact','remedy_target':'none','required_change':'','reason':'The observable metric has no invented numeric target.'} for row in value['review_findings']}})
            if 'artifact_evidence' in value:
                if model=='secondary':return '{"approved":true,"issues":[]}'
                evidence=next(r for r in value['artifact_evidence'] if r['field']=='kpi')
                return json.dumps({'approved':False,'issues':[{'field':'kpi','evidence_id':evidence['evidence_id'],
                    'kind':'quality','requirement_id':'none','reason':'An incorrect interpretation of a metric name.'}]})
            raise AssertionError('Unexpected call')
        def plan(*args,**kw):kw.pop('first_pass_only',None);return mini_plan(*args,**kw)
        with tempfile.TemporaryDirectory() as home:
            with patch.object(ct,'analyze',return_value={'channels':'Instagram','channels_basis':'explicit'}), \
                 patch.object(ct,'produce_checked_strategy',return_value=(strategy,True,'')), \
                 patch.object(ct,'plan_slots',side_effect=plan), \
                 patch.object(ct,'govern_plan',side_effect=lambda a,m,r,k,b,s,*v,**kw:(s,{},True)), \
                 patch.object(ct,'write_week',side_effect=lambda a,m,r,k,b,slots,*v,**kw:[good_post(s) for s in slots]), \
                 patch.object(ct,'repair_single',side_effect=AssertionError('Unjustified author rewrite')), \
                 patch.object(ct,'_ask',side_effect=ask), \
                 patch.object(ct,'editorial_audit',return_value=[]), \
                 patch.object(ct,'copy_too_similar',return_value=False):
                out=ct.execute_compound(request,{'project_workspace':home,'randomized_acceptance_run':True,
                    'plan_reviewer_models':['primary','secondary']},model='writer')
            self.assertEqual(out['content_delivery']['checked_posts'],2,out)
            self.assertTrue(out['documents'][-1]['valid'])
            saved=json.loads(next((Path(home)/'drafts').glob('publication_progress_*.json')).read_text(encoding='utf-8'))
            self.assertEqual(saved['completed'],2)
            self.assertTrue(all(p['_review_adjudication']['full_independent_recheck'] for p in saved['posts'].values()))


class PublicationOutageRecoveryTests(unittest.TestCase):
    def test_review_outage_never_regenerates_draft_and_resume_reviews_same_bytes_first(self):
        request='Разработай SMM-стратегию и контент-план на 1 неделю, 2 публикации в неделю, всего 2 публикации для Instagram.'
        strategy='# Стратегия\n'+('Цели. Аудитория. Каналы. Метрики. План действий. '*25)
        offline=[True];writes=[];reviewed=[]
        def ask(model,system,user,**kw):
            value=json.loads(user)
            post=value['post'];reviewed.append(copy.deepcopy(post))
            if offline[0] and post['id']=='1/Пт':raise ConnectionError('Injected reviewer outage')
            return '{"approved":true,"issues":[]}'
        def writer(a,m,r,k,b,slots,*args,**kw):
            writes.extend(s['day'] for s in slots)
            return [good_post(slot) for slot in slots]
        def plan(*args,**kw):kw.pop('first_pass_only',None);return mini_plan(*args,**kw)
        with tempfile.TemporaryDirectory() as home:
            context={'project_workspace':home,'plan_reviewer_models':['primary','secondary']}
            with patch.object(ct,'analyze',return_value={'channels':'Instagram','channels_basis':'explicit'}), \
                 patch.object(ct,'produce_checked_strategy',return_value=(strategy,True,'')), \
                 patch.object(ct,'plan_slots',side_effect=plan), \
                 patch.object(ct,'govern_plan',side_effect=lambda a,m,r,k,b,s,*v,**kw:(s,{},True)), \
                 patch.object(ct,'write_week',side_effect=writer), \
                 patch.object(ct,'write_single',side_effect=AssertionError('Unexpected author regeneration')) as single, \
                 patch.object(ct,'repair_single',side_effect=AssertionError('Unexpected author repair')) as repair, \
                 patch.object(ct,'repair_fields',side_effect=AssertionError('Unexpected author patch')) as field_repair, \
                 patch.object(ct,'_ask',side_effect=ask), \
                 patch.object(ct,'editorial_audit',return_value=[]), \
                 patch.object(ct,'copy_too_similar',return_value=False):
                first=ct.execute_compound(request,context,model='writer')
                self.assertEqual(first['failure_code'],'independent_review_unavailable')
                self.assertEqual(first['content_delivery']['checked_posts'],1)
                self.assertEqual(writes,['Вт','Пт'])
                failed=first['recovery_context']['rejected_posts']['1/Пт']
                self.assertEqual(failed['review_state'],'unavailable')
                saved_file=next((Path(home)/'drafts').glob('publication_progress_*.json'))
                accepted=copy.deepcopy(json.loads(saved_file.read_text(encoding='utf-8'))['posts']['1/Вт'])
                exact_failed=copy.deepcopy(failed['post'])
                offline[0]=False
                result=ct.execute_compound(request,context,model='writer')
                self.assertTrue(result['documents'][-1]['valid'],result)
                self.assertEqual(writes,['Вт','Пт'],'Resume must review saved bytes before author calls')
                self.assertEqual(reviewed[-1],exact_failed)
                saved=json.loads(saved_file.read_text(encoding='utf-8'))
                self.assertEqual(saved['posts']['1/Вт'],accepted)
                self.assertEqual(saved['completed'],2)
                self.assertEqual(single.call_count+repair.call_count+field_repair.call_count,0)

    def test_no_second_reviewer_preserves_typed_findings_as_unavailable(self):
        post={'caption':'Exact content'}
        finding={'field':'caption','quote':post['caption'],'reason':'A potentially actionable editorial finding.'}
        with self.assertRaises(FindingReviewUnavailable) as caught:
            audit_post_with_independent_model(None,'writer',['only-reviewer'],'Request',{}, {},post,
                audit_function=lambda *a,**k:(None,[finding]))
        self.assertEqual(caught.exception.findings,[finding])

if __name__=='__main__':unittest.main()
