import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from independent_acceptance_editor import (parse_verdict, _evidence_chunks,
    _inventory_segments)
from professional_content import editorial_audit


class A31AcceptanceContractTests(unittest.TestCase):
    def test_strategy_preflight_separates_editorial_review_from_batched_claim_inventory(self):
        from unittest.mock import patch
        from independent_acceptance_editor import call_strategy_editor
        strategy='В мастерской каждый ремонт проходит ручную проверку.'
        claim={'document':'strategy','type':'invented_fact',
               'evidence_id':'strategy:0001','quote':strategy,
               'claim':strategy,'reason':'Проверить подтверждение.',
               'evidence_segment':strategy}
        reviewed=[]
        def adjudicate(request,findings,**kwargs):
            reviewed.extend(findings)
            return {'supported':[],'blocking':[{**findings[0],
                    'adjudication':'unsupported','reason':'Процесс ремонта не подтверждён.'}],
                    'non_business':[]}
        with patch('independent_acceptance_editor._call_editor_once',
                   return_value={'pass':True,'issues':[]}) as review, \
             patch('independent_acceptance_editor._inventory_business_assertions',
                   return_value=[claim]) as inventory, \
             patch('independent_acceptance_editor._review_artifact_form',return_value=[]):
            result=call_strategy_editor('Подготовь стратегию.',strategy,
                    require_inventory=True,claim_adjudicator=adjudicate)
        self.assertFalse(result['pass'])
        self.assertNotIn('require_inventory',review.call_args.kwargs)
        inventory.assert_called_once()
        self.assertEqual(reviewed[0]['quote'],strategy)

    def test_complete_editor_uses_batched_inventory_and_one_source_resolution(self):
        from unittest.mock import patch
        from independent_acceptance_editor import call_editor
        strategy='Студия проводит занятия для взрослых.'
        calendar='# Контент-план\nТема недели — знакомство.'
        segment=next(item for item in _inventory_segments(strategy,calendar)
                     if item['document']=='strategy')
        claim={**segment,'type':'invented_fact','quote':segment['content'],
               'claim':segment['content'],'reason':'Проверить источник.'}
        review_calls=[];adjudicated=[]
        def review(request,st,ca,**kwargs):
            review_calls.append(kwargs)
            return {'pass':True,'issues':[]}
        def resolve(request,findings,**kwargs):
            adjudicated.extend(findings)
            return {'supported':findings,'blocking':[]}
        with patch('independent_acceptance_editor._call_editor_once',side_effect=review), \
             patch('independent_acceptance_editor._inventory_business_assertions',
                   return_value=[claim]) as inventory, \
             patch('claim_support.adjudicate_factual_findings',side_effect=resolve), \
             patch('independent_acceptance_editor._review_artifact_form',return_value=[]):
            verdict=call_editor('Проверь стратегию и контент-план.',strategy,calendar,
                                require_inventory=True)
        self.assertTrue(verdict['pass'])
        self.assertEqual(len(review_calls),1)
        self.assertNotIn('require_inventory',review_calls[0])
        inventory.assert_called_once()
        self.assertEqual([item['quote'] for item in adjudicated],[segment['content']])

    def test_editorial_verdict_has_no_business_inventory_contract(self):
        import io
        from unittest.mock import patch
        from independent_acceptance_editor import _call_editor_once
        strategy=('## Цель\nПоказать процесс.\n## Позиционирование\n'
                  'Студия проводит занятия для взрослых.')
        calendar='# План\nТема — знакомство.'
        captured=[];events=[]
        def fake_urlopen(req,timeout):
            payload=json.loads(req.data.decode('utf-8'))
            captured.append(payload)
            answer={'pass':True,'issues':[]}
            body={'message':{'content':json.dumps(answer,ensure_ascii=False)}}
            return io.BytesIO(json.dumps(body,ensure_ascii=False).encode('utf-8'))
        with patch('independent_acceptance_editor.urllib.request.urlopen',side_effect=fake_urlopen):
            verdict=_call_editor_once('Проверь материалы.',strategy,calendar,
                 report=lambda name,**kw:events.append((name,kw)))
        self.assertTrue(verdict['pass'])
        self.assertIn('field_type',captured[0]['messages'][1]['content'])
        self.assertNotIn('business_claims',captured[0]['format']['properties'])
        self.assertNotIn('reviewed_evidence_ids',captured[0]['format']['properties'])

    def test_inventory_preserves_section_and_table_field_roles(self):
        strategy=('## Визуальный сценарий\n'
                  'Фото или видео с рабочего места студии.\n'
                  '## Уже доступные услуги\n'
                  'Студия проводит занятия для взрослых.\n')
        calendar=('| Неделя/день | Тема | Текст публикации | Сценарий/визуал | KPI и измерение |\n'
                  '|---|---|---|---|---|\n'
                  '| 1/Вт | Знакомство | Студия проводит занятия. | Предложить кадр мастерской. | '
                  'Предложение: вручную считать вопросы. |\n')
        segments=_inventory_segments(strategy,calendar)
        visual=next(item for item in segments if item['content'].startswith('Фото или видео'))
        service=next(item for item in segments if item['content'].startswith('Студия проводит занятия для'))
        calendar_visual=next(item for item in segments if item['content']=='Предложить кадр мастерской.')
        metric=next(item for item in segments if item['content'].startswith('Предложение: вручную'))
        self.assertEqual(visual['section_path'],['Визуальный сценарий'])
        self.assertEqual(service['section_path'],['Уже доступные услуги'])
        self.assertEqual(calendar_visual['field_role'],'Сценарий/визуал')
        self.assertEqual(metric['field_role'],'KPI и измерение')
        self.assertIn('Сценарий/визуал',calendar_visual['row_context']['fields'])

    def test_live_controls_use_the_same_full_inventory_acceptance_path(self):
        from independent_acceptance_editor import review_saved_results
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('one','two'):
                data={'request':'Два поста','response':{'documents':[
                    {'kind':'strategy','content':'Стратегия.'},
                    {'kind':'calendar','content':'План публикаций.'}]}}
                (Path(tmp)/(name+'_synthetic_documents.json')).write_text(
                    json.dumps(data,ensure_ascii=False),encoding='utf-8')
            calls=[]
            def fake_call(request,strategy,calendar,**kwargs):
                calls.append(kwargs)
                if 'Неизвестная 999' in strategy:
                    issue={'document':'strategy','type':'invented_fact',
                           'quote':'адрес ул. Неизвестная 999, цена 50000 рублей.',
                           'reason':'Необоснованные адрес и цена'}
                    return {'pass':False,'issues':[issue]}
                if 'снимаем мерки' in calendar:
                    issue={'document':'calendar','type':'invented_fact',
                           'quote':'В нашей мастерской снимаем мерки и вручную подгоняем каждое платье.',
                           'reason':'Процесс не подтверждён запросом'}
                    return {'pass':False,'issues':[issue]}
                return {'pass':True,'issues':[]}
            def adjudicate(request,findings,**kwargs):
                return {'supported':[],'non_business':[],
                        'blocking':[{**item,'adjudication':'unsupported'} for item in findings]}
            with patch('independent_acceptance_editor.call_editor',side_effect=fake_call):
                result=review_saved_results(Path(tmp),
                                           claim_adjudicator=adjudicate)
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(len(calls),5)
        self.assertTrue(all(call.get('require_inventory') is True for call in calls))

    def test_short_complete_calendar_is_not_rejected_by_document_length(self):
        calendar = '''# Контент-план

Период: 1 неделя. Частота: 2 публикации в неделю; всего 2.

| Неделя/день | Формат | Тема | Текст | Визуал | CTA | KPI |
|---|---|---|---|---|---|---|
| 1/Вт | Пост | Знакомство | Полный текст публикации | Сцена для макета | Задайте вопрос | Ответы |
| 1/Пт | Пост | Практика | Другой полный текст | Другая сцена | Напишите вопрос | Сохранения |
'''
        self.assertEqual([], editorial_audit(calendar, 1, 2))

    def test_live_finding_keeps_exact_unsupported_claim_separate_from_row(self):
        strategy = 'Стратегия.'
        calendar = ('| Неделя/день | Формат | Тема | Текст | Визуал | CTA | KPI |\n'
                    '|---|---|---|---|---|---|---|\n'
                    '| 1/Ср | Пост | Тема | Всем клиентам гарантируем результат за 1 день | Сцена | Вопрос | Ответы |')
        evidence_id = next(key for key, value in __import__(
            'independent_acceptance_editor')._evidence_chunks(strategy, calendar).items()
            if 'гарантируем результат' in value)
        claim = 'Всем клиентам гарантируем результат за 1 день'
        raw = json.dumps({'pass': False, 'issues': [{
            'document': 'calendar', 'type': 'invented_fact',
            'evidence_id': evidence_id, 'claim': claim,
            'reason': 'В брифе нет такой гарантии результата.'
        }]}, ensure_ascii=False)
        result = parse_verdict(raw, strategy=strategy, calendar=calendar,
                               request='Создай публикацию.', require_claim=True)
        self.assertEqual(claim, result['issues'][0]['quote'])
        self.assertIn(claim, result['issues'][0]['evidence_segment'])

    def test_live_finding_recovers_unique_claim_from_wrong_table_row(self):
        strategy = 'Стратегия.'
        calendar = ('| Неделя/день | Площадка | Тема | Текст |\n'
                    '|---|---|---|---|\n'
                    '| 1/Вт | Instagram | Первая тема | Обычный текст. |\n'
                    '| 1/Ср | Instagram | Вторая тема | Всем клиентам гарантируем результат за 1 день. |')
        evidence_id = next(key for key, value in __import__(
            'independent_acceptance_editor')._evidence_chunks(strategy, calendar).items()
            if 'Первая тема' in value)
        raw = json.dumps({'pass': False, 'issues': [{
            'document': 'calendar', 'type': 'invented_fact',
            'evidence_id': evidence_id,
            'claim': 'Всем клиентам гарантируем результат за 1 день.',
            'reason': 'В брифе есть неподтвержденная гарантия.'
        }]}, ensure_ascii=False)
        result = parse_verdict(raw, strategy=strategy, calendar=calendar,
                               request='Создай публикацию.', require_claim=True)
        issue = result['issues'][0]
        self.assertIn('Вторая тема', issue['evidence_segment'])
        self.assertEqual('calendar', issue['document'])

    def test_live_finding_recovers_unique_exact_claim_from_invalid_id(self):
        strategy = 'План стратегии.'
        calendar = ('| Неделя/день | Площадка | Тема | Текст |\n'
                    '|---|---|---|---|\n'
                    '| 1/Вт | Instagram | Первая тема | Обычный текст. |\n'
                    '| 1/Ср | Instagram | Вторая тема | Мы гарантируем результат за один день. |')
        raw = json.dumps({'pass': False, 'issues': [{
            'document': 'calendar', 'type': 'invented_fact',
            'evidence_id': 'calendar:9999',
            'claim': 'Мы гарантируем результат за один день.',
            'reason': 'В источниках проекта такая гарантия не подтверждена.'
        }]}, ensure_ascii=False)
        result = parse_verdict(raw, strategy=strategy, calendar=calendar,
                               request='Подготовь план.', require_claim=True)
        issue = result['issues'][0]
        self.assertIn('Вторая тема', issue['evidence_segment'])
        self.assertTrue(issue['evidence_id'].startswith('calendar:'))

    def test_live_finding_with_invalid_id_and_duplicate_exact_claim_stays_ambiguous(self):
        strategy = 'План стратегии.'
        calendar = ('| Неделя/день | Площадка | Тема | Текст |\n'
                    '|---|---|---|---|\n'
                    '| 1/Вт | Instagram | Первая тема | Мы гарантируем результат за один день. |\n'
                    '| 1/Ср | Instagram | Вторая тема | Мы гарантируем результат за один день. |')
        raw = json.dumps({'pass': False, 'issues': [{
            'document': 'calendar', 'type': 'invented_fact',
            'evidence_id': 'calendar:9999',
            'claim': 'Мы гарантируем результат за один день.',
            'reason': 'В источниках проекта такая гарантия не подтверждена.'
        }]}, ensure_ascii=False)
        with self.assertRaisesRegex(ValueError, 'неоднозначен'):
            parse_verdict(raw, strategy=strategy, calendar=calendar,
                          request='Подготовь план.', require_claim=True)

    def test_invalid_id_does_not_recover_a_near_match(self):
        strategy = 'План стратегии.'
        calendar = 'Мы гарантируем результат за один день.'
        raw = json.dumps({'pass': False, 'issues': [{
            'document': 'calendar', 'type': 'invented_fact',
            'evidence_id': 'calendar:9999',
            'claim': 'Мы гарантируем результат за два дня.',
            'reason': 'Срок гарантии не подтверждён источниками проекта.'
        }]}, ensure_ascii=False)
        with self.assertRaisesRegex(ValueError, 'недостоверный идентификатор'):
            parse_verdict(raw, strategy=strategy, calendar=calendar,
                          request='Подготовь план.', require_claim=True)

    def test_evidence_id_is_authoritative_over_model_document_label(self):
        strategy = 'Тема стратегии.'
        calendar = 'Точный неподтверждённый текст.'
        evidence_id = next(key for key, value in __import__(
            'independent_acceptance_editor')._evidence_chunks(strategy, calendar).items()
            if 'Точный неподтверждённый' in value)
        raw = json.dumps({'pass': False, 'issues': [{
            'document': 'strategy', 'type': 'invented_fact',
            'evidence_id': evidence_id, 'claim': 'Точный неподтверждённый текст.',
            'reason': 'Утверждение не подтверждено источниками проекта.'
        }]}, ensure_ascii=False)
        result = parse_verdict(raw, strategy=strategy, calendar=calendar,
                               require_claim=True)
        self.assertEqual('calendar', result['issues'][0]['document'])

    def test_semantic_fact_review_has_one_owner_and_is_injectable(self):
        request = 'Подготовь публикацию для мастерской. Методы обслуживания неизвестны.'
        strategy = 'Объяснить пользу обслуживания.'
        calendar = 'Мы осматриваем каждую раму вручную.'
        calls = []
        def editor(req, st, ca, **kwargs):
            return {'pass': False, 'issues': [{
                'document': 'calendar', 'type': 'invented_fact',
                'quote': 'Мы осматриваем каждую раму вручную.',
                'reason': 'Метод работы не подтверждён источником.'
            }]}
        def adjudicator(req, findings, **kwargs):
            calls.extend(findings)
            return {'supported': [], 'non_business': [],
                    'blocking': [{**findings[0], 'adjudication': 'unsupported'}]}
        result = __import__('independent_acceptance_editor').call_editor(
            request, strategy, calendar, editor_call=editor,
            claim_adjudicator=adjudicator)
        self.assertFalse(result['pass'])
        self.assertEqual(1, len(calls))


if __name__ == '__main__':
    unittest.main()
