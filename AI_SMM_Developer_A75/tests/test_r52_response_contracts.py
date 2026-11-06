"""Response contracts must survive provider, publication and review boundaries."""
import copy
import io
import json
import random
import unittest
from unittest.mock import patch

import generic_text_review as review
from assertion_scope import classify_obvious_non_business
from ollama_resilience import request_chat
from publication_writer import (write_week, write_single, repair_single, repair_fields,
                                normalize_model_post, validate_single)
from semantic_brief import plan_slots
from publication_schedule import publication_ids


class ResponseContractTests(unittest.TestCase):
    def test_first_pass_planner_supports_entire_cadence_range_including_one_slot(self):
        for frequency in range(1,8):
            rng=random.Random(81300+frequency); calls=[]
            def ask(model,system,user):
                slots=json.loads(user)['open_slots']; calls.append(slots)
                proposals=[{'topic':'Обсудить '+ ' '.join(''.join(rng.choice('абвгдежзиклмнопрстуф') for _ in range(9))
                                                       for _ in range(3))} for _ in slots]
                # A single topic may arrive as a bare object. Both envelopes
                # carry the same host-owned slot contract.
                return json.dumps(proposals[0] if frequency==1 else {'posts':proposals},ensure_ascii=False)
            with self.subTest(frequency=frequency):
                slots=plan_slots(ask,'author','План для Telegram','',{'channels':'Telegram'},
                                 1,per_week=frequency,first_pass_only=True)
                self.assertEqual([f"{s['week']}/{s['day']}" for s in slots],publication_ids(1,frequency))
                self.assertEqual(len(calls),1)

    def test_capability_probe_rejects_legacy_json_echo_without_control_decisions(self):
        import review_qualification
        for content in ('{"pass":true}','{"pass":true,"issues":null}','{"pass":true,"issues":[]}'):
            with self.subTest(content=content),patch.dict(review_qualification._CACHE,{},clear=True), \
                 patch('generic_text_review.request_chat',return_value={'message':{'content':content}}) as call:
                self.assertFalse(review._has_structured_json_contract('reviewer'))
                self.assertEqual(call.call_count,1)
                self.assertEqual(call.call_args.args[0]['format']['required'],['decisions'])

    def test_cloud_ollama_uses_prompt_schema_without_unsupported_decoder_parameter(self):
        original={'model':'any-family:cloud','format':{'type':'object','required':['value']},
                  'messages':[{'role':'system','content':'Contract task'},
                              {'role':'user','content':'Private request'}]}
        saved=copy.deepcopy(original); seen=[]; events=[]
        def transport(req,timeout):
            seen.append(json.loads(req.data))
            return io.BytesIO(b'{"message":{"content":"{\\"value\\":42}"}}')
        with patch('ollama_resilience.urllib.request.urlopen',side_effect=transport):
            request_chat(original,stage='contract',attempts=1,
                         report=lambda stage,**kw:events.append((stage,kw)))
        self.assertEqual(original,saved)
        self.assertNotIn('format',seen[0])
        self.assertIn('"required":["value"]',seen[0]['messages'][0]['content'])
        self.assertEqual(seen[0]['messages'][-1],original['messages'][-1])
        self.assertEqual(events[0][1]['response_contract_mode'],'prompt_schema')
        self.assertNotIn('Private request',repr(events))

    def test_api_provider_gets_schema_in_prompt_and_existing_json_object_transport(self):
        seen={}
        def api(model,messages,**kwargs):
            seen.update(model=model,messages=messages,kwargs=kwargs)
            return {'message':{'content':'{"pass":true,"issues":[]}'}}
        with patch('text_providers.call_text',side_effect=api):
            request_chat({'model':'openai/test-model','format':review.DIMENSION_REVIEW_SCHEMA,
                          'messages':[{'role':'user','content':'Review'}]},stage='review',attempts=1)
        self.assertEqual(seen['kwargs']['response_format'],'json_object')
        self.assertIn('"issues"',seen['messages'][0]['content'])
        self.assertEqual(seen['messages'][-1]['content'],'Review')

    def test_every_publication_generation_and_repair_route_exposes_same_visual_contract(self):
        slot={'week':2,'day':'Пт','platform':'Telegram','format':'Видео',
              'topic':'Как выбрать тему','brief':'Нейтральный вопрос','audience':'Читатели','goal':'Вопросы'}
        post={'id':'2/Пт','topic':slot['topic'],
              'visual':{'scenes':[{'description':'Будущий макет: на однотонном фоне разместить карточку с двумя вопросами для выбора темы.','text':'Что вам интересно?'}]},
              'caption':'Какую тему вы хотели бы обсудить? Опишите, что осталось непонятным.',
              'cta':'Поделитесь вопросом.','kpi':'Количество вопросов','measurement':'Подсчитать вопросы под публикацией',
              'materials':'Подготовить карточку','audience':'Читатели'}
        calls=[]
        def ask(model,system,user,**kwargs):
            schema=kwargs['json_schema']; calls.append((system,schema))
            if 'posts' in schema['properties']: return json.dumps({'posts':[post]},ensure_ascii=False)
            if 'replacements' in schema['properties']: return json.dumps({'replacements':{'P001':'Сцена 1: будущий макет на однотонном фоне с двумя вопросами для выбора темы. Надпись: Что вам интересно?'}},ensure_ascii=False)
            return json.dumps(post,ensure_ascii=False)
        result=write_week(ask,'author','Сделай публикацию',{},'derived',[slot])[0]
        normalized,_=normalize_model_post(result)
        self.assertEqual(validate_single(normalized,slot),[])
        write_single(ask,'author','Сделай публикацию',{},'derived',slot)
        repair_single(ask,'author','Сделай публикацию',{},'derived',slot,normalized,['визуал'])
        bad={**normalized,'visual':'Видео'}
        fixed=repair_fields(ask,'author','Сделай публикацию',{},'derived',slot,bad,
                            ['слишком общий визуальный сценарий'])
        self.assertEqual(validate_single(fixed,slot),[])
        self.assertEqual(len(calls),4)
        for system,schema in calls[:3]:
            self.assertIn('JSON Schema ответа',system)
            body=schema['properties'].get('posts',{}).get('items') or schema['properties'].get('changes') or schema
            self.assertEqual(body['properties']['visual']['type'],'object')
            self.assertIn('scenes',body['properties']['visual']['required'])
        self.assertIn('replacements',calls[3][1]['properties'])
        self.assertIn('слишком общий визуальный сценарий',validate_single(bad,slot))

    def test_bad_editor_dimension_preserves_findings_and_completes_other_dimensions_without_repair(self):
        draft='Тестовый текст содержит неподтверждённую гарантию.'
        assignments=dict(zip(review.CHECKS,['model-a','model-b','model-c','model-d']))
        for bad in review.CHECKS:
            with self.subTest(unavailable=bad):
                finding=next(x for x in review.CHECKS if x!=bad); calls=[]
                def chat(payload,**kwargs):
                    user=json.loads(payload['messages'][1]['content'])
                    dimension=user.get('review_dimension','factual_grounding'); calls.append(dimension)
                    if 'findings' in user:
                        from source_review_fixture import general_source_reply
                        return ({'message':{'content':'{"decisions":{}}'}} if dimension==bad
                                else general_source_reply(payload))
                    self.assertEqual(payload['format']['required'],['pass','issues'])
                    if dimension==bad: result={'pass':False,'issues':None}
                    elif dimension==finding:
                        result={'pass':False,'issues':[{'evidence_id':user['review_evidence'][0]['evidence_id'],
                                                       'reason':'Условие не подтверждено','severity':'major'}]}
                        if dimension in ('requirements','completeness'):
                            result['issues'][0].update(requirement_id=user['requirement_bindings'][0]['id'],
                                                      failure_type='other_explicit_requirement')
                    else: result={'pass':True,'issues':[]}
                    return {'message':{'content':json.dumps(result,ensure_ascii=False)}}
                def source_check(*args,**kwargs):
                    calls.append('factual_grounding')
                    if bad=='factual_grounding':
                        raise ValueError('Source audit unavailable')
                    return {'pass':True,'issues':[]}
                with patch('generic_text_review.request_chat',side_effect=chat), \
                     patch('generic_text_review.review_public_copy_sources',side_effect=source_check):
                    result=review.run_quality_cycle('Создай текст',{'action':'write','answer':draft},{},
                        model='author',reviewer_models=assignments,
                        repairer=lambda *a,**k:self.fail('incomplete coverage must not trigger a rewrite'))
                verdict=result['quality_review']
                self.assertEqual(result['action'],'write_blocked')
                self.assertEqual(result['answer'],draft)
                expected=[check for check in review.CHECKS for _ in range(2 if check==bad and check!='factual_grounding' else 1)]
                self.assertEqual(calls,expected)
                self.assertEqual(verdict['review_state'],'partial')
                self.assertEqual(set(verdict['review_errors']),{bad})
                self.assertEqual(verdict['issues'][0]['check'],finding)
                self.assertEqual(verdict['reviewer_models'],assignments)
                self.assertFalse(verdict['repair_applied'])

    def test_unavailable_panel_does_not_invent_a_content_issue(self):
        assignments=dict(zip(review.CHECKS,['model-a','model-b','model-c','model-d']))
        with patch('generic_text_review.request_chat',return_value={'message':{'content':'{"pass":true}'}}):
            verdict=review.review_text('Brief','Draft',{},model='author',reviewer_models=assignments)
        self.assertFalse(verdict['pass'])
        self.assertEqual(verdict['review_state'],'unavailable')
        self.assertEqual(verdict['issues'],[])
        self.assertEqual(verdict['completed_dimensions'],[])
        self.assertEqual(set(verdict['review_errors']),set(review.CHECKS))

    def test_future_editorial_subject_is_platform_independent_and_handles_unicode_dashes(self):
        rng=random.Random(83351)
        for dash in (' ','-','\u2011','\u2013','\u2014'):
            for noun in ('канал','аккаунт','страница','рассылка'):
                label=''.join(rng.choice('ABCDEFGH') for _ in range(8))
                text=f'{label}{dash}{noun} будет использоваться для публикаций и взаимодействия через сообщения.'
                item={'quote':text,'field_type':'document_narrative',
                      'section_path':['Стратегия SMM','Каналы и продвижение']}
                with self.subTest(text=text):
                    self.assertEqual(classify_obvious_non_business(item)['statement_class'],'planning_constraint')
        english={'quote':'Project newsletter will be used for publishing and collecting questions.',
                 'field_type':'document_narrative','section_path':['Content plan']}
        self.assertEqual(classify_obvious_non_business(english)['statement_class'],'planning_constraint')

    def test_editorial_context_does_not_hide_business_claims_or_copy(self):
        for text in ('Наш канал будет использоваться для записи на консультацию.',
                     'Канал будет использоваться для бесплатной консультации клиентов.',
                     'Канал будет использоваться для приёма заказов за один день.',
                     'Project newsletter will be used for free consultations.',
                     'Стоимость услуги составляет 500 рублей.',
                     'Компания гарантирует рост обращений.',
                     'Мы оказываем услугу за один день.'):
            with self.subTest(text=text):
                self.assertIsNone(classify_obvious_non_business({'quote':text,
                    'field_type':'document_narrative','section_path':['Стратегия SMM','План действий']}))
        self.assertIsNone(classify_obvious_non_business({
            'quote':'Канал будет использоваться для публикаций и взаимодействия.',
            'field_type':'caption','section_path':['Стратегия SMM']}))

    def test_infinitive_recommendations_share_scope_across_strategy_sections(self):
        for section in ('Стратегическое направление','План действий','Канал и продвижение'):
            for action in ('Экспериментировать','Оптимизировать','Сравнивать','Анализировать'):
                with self.subTest(section=section,action=action):
                    result=classify_obvious_non_business({'quote':f'{action} варианты подачи контента.',
                        'field_type':'document_narrative','section_path':['Стратегия SMM',section]})
                    self.assertEqual(result['statement_class'],'planning_constraint')


if __name__=='__main__': unittest.main()
