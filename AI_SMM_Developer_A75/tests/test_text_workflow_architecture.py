from substance_fixture import assessed_reply
"""Whole text-workflow contracts, using injected model responses, not live quality claims."""
import io
import json
import random
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from generic_text_review import acceptance_model_pool, CHECKS
from model_roles import allocate_revision_roles, model_family
from text_task_contract import TextTaskContract, TextRevisionContract, record_model_failure
from text_workflow import run_text_task
from ollama_resilience import request_chat, ProviderHTTPError, request_deadline


class TextWorkflowArchitectureTests(unittest.TestCase):
    def test_request_sources_are_immutable_across_both_artifact_routes(self):
        material={'content':'Подтверждённый факт из документа.', 'verified':True}
        context={'verified_project_materials':[material], 'existing_documents':['AI draft is not evidence']}
        for request in ('Напиши одну подпись.', 'Подготовь стратегию и контент-план.'):
            contract=TextTaskContract.create(request,context)
            seen=[]
            def executor(req,state,**kwargs):
                seen.append((req,state));return {'action':'respond','answer':'ready'}
            result=run_text_task(request,context,planner=executor,compound_runner=executor,
                                 quality_cycle=lambda r,d,c,**k:d)
            self.assertEqual(seen[0][0],request)
            self.assertEqual(seen[0][1]['text_task_signature'],contract.signature)
            self.assertEqual(result['execution_contract'],contract.metadata())
            self.assertEqual(seen[0][1]['evidence_context'],contract.evidence.to_dict())
            self.assertNotIn('evidence_context',context)
            self.assertEqual(len(contract.evidence.sources),2)

    def test_cross_request_sources_are_rejected_before_generation(self):
        original=TextTaskContract.create('Задание первого проекта.',{})
        with self.assertRaisesRegex(ValueError,'другому запросу'):
            run_text_task('Задание второго проекта.',{'evidence_context':original.evidence.to_dict()},
                          planner=lambda *a,**k:self.fail('source mismatch reached model'))

    def test_resume_routes_the_same_rejected_bytes_without_regeneration(self):
        captured=[]
        request='Напиши один вымышленный пост.'
        proposal={'mode':'fictional','permission_quote':request}
        def quality(req,draft,state,**kwargs):
            captured.append(draft)
            self.assertEqual(state['proposed_content_contract']['permission_quote'],request)
            self.assertNotIn('authorized_creative_permission',state)
            return draft
        result=run_text_task(request,{'task_mode':'resume','pending_text_candidate':{
            'draft':'Saved draft.','content_contract':proposal,
            'request_sha256':__import__('hashlib').sha256(request.encode('utf-8')).hexdigest()}},
            planner=lambda *a,**k:self.fail('resume must not regenerate'),
            quality_cycle=quality)
        self.assertEqual(captured[0]['answer'],'Saved draft.')
        self.assertEqual(result['instruction'],request)

    def test_resume_rejects_foreign_checkpoint_before_any_model_call(self):
        import hashlib
        request='Напиши пост для проекта Б.'
        foreign='Напиши пост для проекта А.'
        checkpoint={'draft':'Другой черновик',
            'request_sha256':hashlib.sha256(foreign.encode('utf-8')).hexdigest()}
        with self.assertRaisesRegex(ValueError,'другому заданию'):
            run_text_task(request,{'task_mode':'resume','pending_text_candidate':checkpoint},
                planner=lambda *a,**k:self.fail('foreign checkpoint reached planner'),
                quality_cycle=lambda *a,**k:self.fail('foreign checkpoint reached reviewer'))

    def test_unsigned_resume_draft_is_rejected_before_model_calls(self):
        request='Подготовь текст для проекта А.'
        with self.assertRaisesRegex(ValueError,'другому заданию'):
            run_text_task(request,{'task_mode':'resume','pending_text_candidate':{
                'draft':'Старый черновик без подтверждения исходного задания'}},
                planner=lambda *a,**k:self.fail('unsigned checkpoint reached author'),
                quality_cycle=lambda *a,**k:self.fail('unsigned checkpoint reached reviewer'))

    def test_resume_keeps_request_bound_draft_without_regeneration(self):
        import hashlib
        request='Подготовь короткое описание.'
        observed=[]
        checkpoint={'draft':'Сохранённый текст',
            'request_sha256':hashlib.sha256(request.encode('utf-8')).hexdigest()}
        out=run_text_task(request,{'task_mode':'resume','pending_text_candidate':checkpoint},
            planner=lambda *a,**k:self.fail('resumed draft regenerated'),
            quality_cycle=lambda req,draft,ctx,**k:observed.append(draft) or draft)
        self.assertEqual(out['answer'],'Сохранённый текст')
        self.assertEqual(len(observed),1)

    def test_revision_contract_uses_actual_changes_and_does_not_trust_model_change_report(self):
        draft='Мы гарантируем результат. Полезная часть.'
        revision=TextRevisionContract.create(draft,[{'check':'factual_grounding',
            'quote':'Мы гарантируем результат.','reason':'В источнике нет обещания.'}])
        ident=revision.findings[0]['id']
        good={'revised_text':'Общее пояснение. Полезная часть.',
              'resolutions':{ident:{'action':'rewritten','revised_quote':'Общее пояснение.'}}}
        self.assertEqual(revision.validate(good),good['revised_text'])
        bad=[{'revised_text':draft,'resolutions':good['resolutions']}]
        retained=revision.validate({'revised_text':draft+' Added.',
            'resolutions':{ident:{'action':'removed','revised_quote':''}}})
        self.assertTrue(revision.evidence(retained)['findings'][0]['original_quote_retained'])
        self.assertEqual(revision.evidence(retained)['semantic_resolution'],'requires_independent_review')
        for value in bad:
            with self.subTest(value=value),self.assertRaises(ValueError):revision.validate(value)
        for report in ({},{ident:{'action':'rewritten','revised_quote':'Not in text.'}}):
            self.assertEqual(revision.validate({'revised_text':good['revised_text'],
                'resolutions':report}),good['revised_text'])
        self.assertEqual(revision.schema()['required'],['revised_text'])
        observed=revision.evidence(good['revised_text'])
        self.assertEqual(observed['semantic_resolution'],'requires_independent_review')
        self.assertFalse(observed['findings'][0]['original_quote_retained'])
        with self.assertRaises(ValueError):TextRevisionContract.create(draft,[{'quote':'Other draft','reason':'x'}])

    def test_completeness_correction_can_preserve_useful_original_copy(self):
        draft='Полезный пример.'
        revision=TextRevisionContract.create(draft,[{'check':'completeness','quote':draft,'reason':'Нет вывода.'}])
        ident=revision.findings[0]['id']
        self.assertEqual(revision.validate({'revised_text':draft+' Нужный вывод.',
            'resolutions':{ident:{'action':'rewritten','revised_quote':'Нужный вывод.'}}}),draft+' Нужный вывод.')

    def test_writer_roles_reserve_review_capacity_across_sizes_and_families(self):
        pool=['author:1','review1:1','review2:1','review3:1','review4:1','cloud:cloud']
        for seed in range(40):
            roles=allocate_revision_roles('author:1',pool,chooser=random.Random(seed))
            self.assertIn('cloud:cloud',roles.reviewer_pool)
            self.assertNotEqual(roles.revision_author,'author:1')
            self.assertEqual(len({model_family(m) for m in roles.reviewer_pool}),4)
            self.assertNotIn('author:1',roles.reviewer_pool)
            self.assertNotIn(roles.revision_author,roles.reviewer_pool)
        roles=allocate_revision_roles('author:1',pool[:-1])
        self.assertEqual(roles.revision_author,'author:1')
        self.assertEqual(len(roles.reviewer_pool),4)
        roles=allocate_revision_roles('author:1',pool+['review1:7'],
                                      fixed_reviewers=dict(zip(CHECKS,pool[1:5])))
        self.assertEqual(roles.revision_author,'cloud:cloud')

    def test_full_standalone_cycle_uses_typed_revision_and_excludes_both_writers_from_review(self):
        pool=['author:1','review1:1','review2:1','review3:1','review4:1','cloud:cloud','othercloud:cloud']
        events=[];calls=[];initial=[]
        draft='Мы гарантируем результат за день.'
        revised='Небольшой пример помогает разобраться в выборе. Напишите нам.'
        def chat(payload,**kwargs):
            user=json.loads(payload['messages'][1]['content']);calls.append(payload)
            if 'revision_findings' in user:
                self.assertIn(payload['model'],('cloud:cloud','othercloud:cloud'))
                self.assertEqual(user['rejected_draft'],draft)
                value={'revised_text':revised,'resolutions':{f['id']:{'action':'removed','revised_quote':''}
                       for f in user['revision_findings']}}
            elif 'findings' in user:
                value={'decisions':{row['finding_id']:{'invitation_kind':'none','statement_class':'business_fact','speech_act':'assertion','assertion_subject':'business','source_relation':'unrelated',
                    'verdict':'unsupported','source_quote_id':'none','reason':'Нет подтверждённого источника.'}
                    for row in user['findings']}}
            else:
                value={'pass':True,'issues':[]}
            return {'message':{'content':json.dumps(assessed_reply(payload,value),ensure_ascii=False)}}
        with acceptance_model_pool(pool),patch('generic_text_review._reviewer_pool_is_prevalidated',return_value=True),patch('generic_text_review.request_chat',side_effect=chat):
            result=run_text_task('Напиши один полезный пост.',
                {'randomized_acceptance_run':True,'acceptance_execution_mode':'bounded_workflow'},
                model='author:1',planner=lambda *a,**k:{'action':'write','answer':draft},
                progress=events.append,on_initial_response=initial.append)
        self.assertEqual(result['action'],'write')
        self.assertEqual(result['answer'],revised)
        review=result['quality_review']
        self.assertEqual(review['initial_author_model'],'author:1')
        self.assertIn(review['revision_author_model'],('cloud:cloud','othercloud:cloud'))
        self.assertEqual(len(set(review['reviewer_models'].values())),4)
        self.assertFalse(set(review['reviewer_models'].values()) & {'author:1',review['revision_author_model']})
        self.assertFalse(next(e for e in events if e['stage']=='text_first_pass_review_result')['passed'])
        self.assertEqual(initial[0]['answer'],draft)
        self.assertEqual(sum('revision_findings' in json.loads(c['messages'][1]['content']) for c in calls),1)

    def test_compound_final_revision_uses_assigned_writer_and_preserves_other_artifact(self):
        import copy
        from production_pipeline import run_document_task
        from independent_acceptance_editor import ValidatedEditorialVerdict
        from test_windows_acceptance_v4 import TaskRecoveryTests
        context={'randomized_acceptance_run':True,'acceptance_execution_mode':'bounded_workflow',
                 'quality_model_pool':['author:1','review1:1','review2:1','review3:1','review4:1','cloud:cloud']}
        prepared=TaskRecoveryTests.result(True)
        prepared['documents'][0]['content']='Полезная стратегия, которую сохраняем.'
        prepared['documents'][1]['content']=('''| Неделя/день | Площадка и формат | Конкретная тема | Цель | Сценарий/визуал | Текст/ключевые реплики | CTA | KPI | Метод измерения | Целевая аудитория | Необходимые материалы |
|---|---|---|---|---|---|---|---|---|---|---|
| 1/Вт | Telegram, пост | Первый вопрос | Разъяснить | Visual | Неподтверждённое обещание. | Вопрос | Комментарии | Подсчёт | Читатели | Изображение |
| 1/Пт | Telegram, пост | Второй вопрос | Разъяснить | Visual | Сохранённая полезная публикация. | Вопрос | Комментарии | Подсчёт | Читатели | Изображение |''')
        calls=[];reviews=[]
        def executor(request,state,model,progress=None):
            calls.append((model,copy.deepcopy(state)))
            result=copy.deepcopy(prepared)
            if state.get('task_mode')=='final_review_repair':
                self.assertEqual(state['final_review_repair_slots'],['1/Вт'])
                result['documents'][1]['content']=result['documents'][1]['content'].replace(
                    'Неподтверждённое обещание.','Общее образовательное пояснение.')
            return result
        def reviewer(request,strategy,calendar):
            reviews.append(calendar)
            issues=[] if 'Неподтверждённое обещание.' not in calendar else [{
                'document':'calendar','type':'invented_fact','quote':'Неподтверждённое обещание.',
                'reason':'Нет подтверждённого источника.', 'row_context':{'slot':'1/Вт'}}]
            return ValidatedEditorialVerdict({'pass':not issues,'issues':issues,'review_state':'complete'})
        result=run_document_task('Подготовь стратегию и контент-план.',context,
            model='author:1',review_model='review1:1',execute=executor,
            strategy_reviewer=lambda *a:{'pass':True,'issues':[]},final_reviewer=reviewer)
        self.assertEqual([model for model,state in calls],['author:1','cloud:cloud'])
        self.assertEqual(len(reviews),2)
        self.assertEqual(result['documents'][0]['content'],prepared['documents'][0]['content'])
        self.assertIn('Сохранённая полезная публикация.',result['documents'][1]['content'])
        self.assertEqual(result['content_delivery']['independent_editor']['status'],'PASS')

    def test_targeted_strategy_revision_has_one_call_and_never_regenerates_rejected_document(self):
        from strategy_recovery import produce_checked_strategy
        calls=[]
        draft={'content':'Отклонённый документ.','valid':False,'reason':'Нет источника.'}
        def revise(body,reason,attempt):
            calls.append(body);return body+' Осталась ошибка.'
        result=produce_checked_strategy('Исходное задание.',1,
            lambda *a:self.fail('A targeted revision cannot regenerate the strategy'),
            lambda *a:(False,'Не исправлена проблема.'),attempts=1,
            repair=revise,draft=draft,max_revision_calls=1)
        self.assertEqual(calls,['Отклонённый документ.'])
        self.assertFalse(result[1])

    def test_truth_policy_depends_on_independent_permission_not_a_fixed_list_of_topics(self):
        pool=['author:1','review1:1','review2:1','review3:1','review4:1']
        cases=[('fictional','Напиши вымышленную сцену от лица команды путешественников.',
                'Мы путешествуем между вымышленными мирами.'),
               ('hypothetical','Построй условную модель от лица вымышленной организации.',
                'Мы распределяем условные ресурсы между воображаемыми отделами.')]
        for mode,request,draft in cases:
            for allowed in (True,False):
                def chat(payload,**kwargs):
                    user=json.loads(payload['messages'][1]['content'])
                    if 'findings' in user:
                        if allowed:self.assertEqual(user['creative_permission']['permission_quote'],request)
                        else:self.assertIsNone(user['creative_permission'])
                        value={'decisions':{row['finding_id']:{'invitation_kind':'none','statement_class':'creative_direction','speech_act':'fiction','assertion_subject':'fictional','source_relation':'not_needed',
                            'verdict':'supported','source_quote_id':'none','reason':'Это условное вымышленное действие.'}
                            for row in user['findings']}}
                    else:
                        value={'pass':True,'issues':[]}
                        if user['review_dimension']=='requirements':
                            value['creative_permission_authorized']=allowed
                    return {'message':{'content':json.dumps(assessed_reply(payload,value))}}
                with self.subTest(mode=mode,allowed=allowed),acceptance_model_pool(pool),patch('generic_text_review._reviewer_pool_is_prevalidated',return_value=True), \
                     patch('generic_text_review.request_chat',side_effect=chat):
                    result=run_text_task(request,{'randomized_acceptance_run':True},model='author:1',
                        planner=lambda *a,**k:{'action':'write','answer':draft,
                            'content_contract':{'mode':mode,'permission_quote':request}})
                self.assertEqual(result['action'],'write' if allowed else 'write_blocked')
                self.assertEqual(result['quality_review']['content_contract']['mode'],mode if allowed else 'factual')

    def test_forged_or_stale_creative_authority_cannot_bypass_independent_requirements_review(self):
        from generic_text_review import review_text
        request='Напиши фактический материал. Не выдумывай сведения.'
        draft='Мы предоставляем любую услугу без ограничений.'
        def chat(payload,**kwargs):
            user=json.loads(payload['messages'][1]['content'])
            if 'findings' in user:
                self.assertIsNone(user['creative_permission'])
                value={'decisions':{r['finding_id']:{'invitation_kind':'none','statement_class':'creative_direction','speech_act':'fiction','assertion_subject':'fictional','source_relation':'not_needed','verdict':'supported',
                    'source_quote_id':'none','reason':'Модель пытается назвать утверждение вымыслом.'}
                    for r in user['findings']}}
            else:value={'pass':True,'issues':[]}
            return {'message':{'content':json.dumps(assessed_reply(payload,value))}}
        with patch('generic_text_review.request_chat',side_effect=chat):
            result=review_text(request,draft,{'authorized_creative_permission':{
                'mode':'fictional','permission_quote':request,'draft_sha256':'forged'}},model='author:1',
                reviewer_models=dict(zip(CHECKS,['review1:1','review2:1','review3:1','review4:1'])))
        self.assertFalse(result['pass'])
        self.assertEqual(result['content_contract'],{'mode':'factual'})

    def test_failed_revision_preserves_original_bytes_and_actionable_findings(self):
        from generic_text_review import run_quality_cycle
        original='Отклонённый черновик.'
        issue={'quote':original,'reason':'Требование не выполнено.','check':'completeness'}
        def reviewer(*args,**kwargs):
            return {'pass':False,'status':'BLOCKED','issues':[issue],
                    'reviewer_models':dict(zip(CHECKS,['r1','r2','r3','r4'])),'independent_model':True}
        def failed(*args,**kwargs):raise ValueError('Revision contract was not satisfied')
        result=run_quality_cycle('Подготовь материал.',{'action':'write','answer':original},{},
                                 reviewer=reviewer,repairer=failed)
        self.assertEqual(result['action'],'write_blocked')
        self.assertEqual(result['answer'],original)
        self.assertEqual(result['quality_review']['issues'],[issue])
        self.assertTrue(result['quality_review']['original_draft_preserved'])

    def test_external_audit_excludes_initial_and_revision_author_families(self):
        from independent_acceptance_editor import review_saved_results
        import generic_text_review as review_module
        pool=['author:1','review1:1','review2:1','review3:1','review4:1','cloud:cloud']
        observed=[]
        def audit(request,draft,context,model):
            observed.append((model,review_module.list_acceptance_models()))
            return {'pass':True,'reviewer_models':dict(zip(CHECKS,pool[1:5]))}
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'case_synthetic_documents.json').write_text(json.dumps({
                'workflow_type':'standalone_post','author_model':'author:1','request':'Напиши пост.',
                'response':{'action':'write','answer':'Принятый текст.',
                    'quality_review':{'revision_author_model':'cloud:cloud'}}}), encoding='utf-8')
            with acceptance_model_pool(pool),patch('generic_text_review._reviewer_pool_is_prevalidated',return_value=True),patch('generic_text_review.review_text',side_effect=audit):
                result=review_saved_results(Path(folder),editor_call=lambda *a:None)
        self.assertEqual(result['cases'][0]['status'],'PASS')
        self.assertEqual(observed,[('cloud:cloud',pool[1:5]),('cloud:cloud',pool[1:5])])


class ProviderBoundaryTests(unittest.TestCase):
    payload={'model':'arbitrary:cloud','format':'json','messages':[{'role':'user','content':'PRIVATE_REQUEST'}]}

    def error(self,code,body=b'{"error":"temporary upstream failure"}'):
        return urllib.error.HTTPError('http://localhost/api/chat',code,'provider failed',{},io.BytesIO(body))

    def test_transient_provider_failure_can_retry_once_without_changing_payload_or_content_score(self):
        requests=[];events=[]
        def transport(req,timeout):
            requests.append(req.data)
            if len(requests)==1:raise self.error(500)
            return io.BytesIO(b'{"message":{"content":"{\\"ok\\":true}"}}')
        with patch('ollama_resilience.urllib.request.urlopen',side_effect=transport):
            result=request_chat(self.payload,stage='strategy',attempts=2,
                                report=lambda name,**d:events.append(d))
        self.assertEqual(json.loads(result['message']['content']),{'ok':True})
        self.assertEqual(len(requests),2)
        self.assertEqual(requests[0],requests[1])
        self.assertEqual([e['outcome'] for e in events],['provider_error','ok'])
        self.assertEqual(events[0]['http_status'],500)
        self.assertNotIn('PRIVATE_REQUEST',repr(events))

    def test_auth_quota_and_contract_errors_never_retry(self):
        for status in (400,401,403,404,422,429):
            with self.subTest(status=status),patch('ollama_resilience.urllib.request.urlopen',
                    side_effect=self.error(status)) as transport:
                with self.assertRaises(ProviderHTTPError) as caught:
                    request_chat(self.payload,stage='failure',attempts=2)
                self.assertEqual(caught.exception.status_code,status)
                self.assertEqual(transport.call_count,1)

    def test_provider_retry_respects_original_case_deadline_and_does_not_retry_timeout(self):
        from ollama_resilience import OllamaStageError
        clock=[0.0]
        def transport(req,timeout):
            clock[0]=11.0;raise self.error(503)
        with patch('ollama_resilience.time.monotonic',side_effect=lambda:clock[0]), \
             patch('ollama_resilience.urllib.request.urlopen',side_effect=transport) as model:
            with request_deadline(10,retry_timeouts=False),self.assertRaises(OllamaStageError):
                request_chat(self.payload,stage='deadline',attempts=2)
        self.assertEqual(model.call_count,1)
        with patch('ollama_resilience.urllib.request.urlopen',side_effect=TimeoutError('timeout')) as model:
            with request_deadline(10,retry_timeouts=False,abort_case_on_timeout=True),self.assertRaises(OllamaStageError):
                request_chat(self.payload,stage='timeout',attempts=2)
        self.assertEqual(model.call_count,1)

    def test_terminal_provider_body_is_project_local_redacted_and_not_in_telemetry(self):
        secret='SYNTHETIC_CREDENTIAL_VALUE'
        body=json.dumps({'error':'upstream parser failed','credential':secret,
                         'header':'Bearer OTHER_SECRET'}).encode()
        events=[]
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict('os.environ',{'SYNTHETIC_API_KEY':secret}), \
             patch('ollama_resilience.urllib.request.urlopen',side_effect=self.error(500,body)):
            with self.assertRaises(ProviderHTTPError) as caught:
                request_chat(self.payload,stage='provider',attempts=1,
                             report=lambda name,**d:events.append(d))
            filename=record_model_failure(folder,'a'*64,'arbitrary:cloud','strategy',caught.exception)
            saved=json.loads((Path(folder)/filename).read_text(encoding='utf-8'))
        self.assertIn('upstream parser failed',saved['response'])
        self.assertNotIn(secret,saved['response']+repr(events)+str(caught.exception))
        self.assertNotIn('OTHER_SECRET',saved['response'])
        self.assertNotIn('upstream parser failed',repr(events))
        self.assertEqual(saved['diagnostics']['http_status'],500)


if __name__=='__main__':unittest.main()
