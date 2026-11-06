import unittest
from unittest.mock import patch

from factual_claim_audit import scan_unsupported_business_claims
from independent_quality_gate import _calendar_slots_for_issues, _calendar_issues_by_slot
from independent_acceptance_editor import _inventory_business_assertions
from assertion_scope import classify_obvious_non_business
from strategy_integrity import separate_embedded_publication_section


class GeneralBusinessClaimInventoryTests(unittest.TestCase):
    def test_unbounded_infinitive_action_in_strategy_plan_is_not_misread_as_existing_operation(self):
        recommendation=classify_obvious_non_business({
            'document':'strategy',
            'content':'Оптимизировать контент на основе полученных результатов.',
            'field_type':'document_narrative',
            'section_path':['Стратегия SMM','План действий']})
        self.assertEqual(recommendation['statement_class'],'planning_constraint')
        current_operation=classify_obvious_non_business({
            'document':'strategy',
            'content':'Наша студия оптимизирует контент каждый месяц.',
            'field_type':'document_narrative',
            'section_path':['Стратегия SMM','План действий']})
        self.assertIsNone(current_operation)

    def test_quoted_brief_directives_and_plan_fragments_are_not_business_claims(self):
        cases=(
            ('Создайте прикладную SMM-стратегию и контент-план для Instagram.',
             'planning_constraint'),
            ('Считайте неизвестными цены, адрес, сроки и отзывы.',
             'planning_constraint'),
            ('Для KPI выберите показатель без числовой цели.',
             'planning_constraint'),
            ('Количество лайков и комментариев на публикациях.',
             'planning_constraint'),
            ('Мониторинг и анализ результатов.', 'planning_constraint'),
            ('**Этапы:**', 'formatting_only'),
            ('Чтобы оно хорошо росло, важно правильно ухаживать за ним.',
             'general_information'),
            ('Подкормка: когда и чем подкармливать?', 'audience_prompt'),
        )
        for text,expected in cases:
            with self.subTest(text=text):
                result=classify_obvious_non_business(
                    text=text,field_type='document_narrative',
                    section_path=['Стратегия SMM','Контент-план'])
                self.assertIsNotNone(result)
                self.assertEqual(result['statement_class'],expected)

    def test_quoted_prompt_rules_do_not_exempt_business_promises(self):
        for text in ('Мы ответим на вопросы о наличии.',
                     'Мы поможем выбрать растение.',
                     'Мы ремонтируем любые модели обуви.'):
            with self.subTest(text=text):
                self.assertIsNone(classify_obvious_non_business(
                    text=text,field_type='caption',section_path=['Контент-план']))

    def test_plain_content_plan_header_with_multiple_ready_posts_is_separated(self):
        strategy=(
            'Стратегия:\nПозиционирование мастерской после осмотра.\n\n'
            'Контент-план:\n\n'
            '1. Публикация 1:\n   - Тема: уход.\n   - Текст: Первый уникальный текст.\n'
            '2. Публикация 2:\n   - Тема: выбор.\n   - Текст: Второй уникальный текст.\n\n'
            'Примечание:\nНе добавляй обещания.')
        result,edits=separate_embedded_publication_section(strategy)
        self.assertEqual(len(edits),1)
        self.assertIn('Позиционирование мастерской',result)
        self.assertIn('Примечание:',result)
        self.assertNotIn('Первый уникальный текст',result)
        self.assertNotIn('Второй уникальный текст',result)

    def test_single_post_is_not_moved_out_of_strategy(self):
        strategy='Контент-план:\n1. Публикация 1:\n- Текст: Одна заметка.'
        result,edits=separate_embedded_publication_section(strategy)
        self.assertEqual(result,strategy)
        self.assertEqual(edits,[])

    def test_strategy_root_is_preserved_while_nested_numbered_posts_are_separated(self):
        # Captured R15 failure shape: the model put two ready posts directly
        # under a strategy document heading, without a separate plan heading.
        strategy=(
            '## SMM-стратегия для проекта\n\n'
            '**Позиционирование:** Короткая стратегическая рекомендация.\n\n'
            '### Публикация 1:\n* **Тема:** А\n* **Текст:** Готовый текст один.\n\n'
            '### Публикация 2:\n* **Тема:** Б\n* **Текст:** Готовый текст два.\n\n'
            '**Общие правила измерения:**\nОхват и ответы измеряются в статистике.\n\n'
            '**Примечание:** Не добавляй неизвестные факты.')
        result,edits=separate_embedded_publication_section(strategy)
        self.assertEqual(len(edits),1)
        self.assertIn('SMM-стратегия',result)
        self.assertIn('Позиционирование',result)
        self.assertNotIn('Готовый текст один',result)
        self.assertNotIn('Готовый текст два',result)
        self.assertIn('Общие правила измерения',result)
        self.assertIn('Не добавляй неизвестные факты',result)

    def test_editorial_directives_and_audience_prompts_are_not_business_facts(self):
        cases = [
            ('Предложить конкретные шаги или вопросы, которые помогут описать проблему до обращения в сервис.',
             'planning_constraint'),
            ('Написать текст публикации', 'planning_constraint'),
            ('Каждую публикацию оформить в виде вопроса или конкретного совета.',
             'planning_constraint'),
            ('Образовательный пост', 'creative_direction'),
            ('Рекомендации по предварительной проверке и организации данных',
             'creative_direction'),
            ('Напишите, какие сложности вы испытываете', 'audience_prompt'),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                result=classify_obvious_non_business(
                    text=text,field_type='document_narrative',
                    section_path=['SMM-стратегия','Контент-план'])
                self.assertIsNotNone(result)
                self.assertEqual(result['statement_class'],expected)

    def test_editorial_scope_rules_do_not_exempt_company_promises(self):
        for text in ('Мы предоставим все необходимые инструменты.',
                     'Мы гарантируем безопасный результат.',
                     'Мы бесплатно поможем выбрать услугу.'):
            with self.subTest(text=text):
                self.assertIsNone(classify_obvious_non_business(
                    text=text,field_type='document_narrative',
                    section_path=['SMM-стратегия','План действий']))

    def test_content_commitments_are_editorial_but_fulfillment_claims_remain_reviewable(self):
        for text in ('Мы расскажем о базовых рекомендациях по уходу за растениями.',
                     'В публикации разберём, что входит в первый набор для рисования.'):
            with self.subTest(text=text):
                result=classify_obvious_non_business(
                    text=text,field_type='document_narrative',
                    section_path=[])
                self.assertIsNotNone(result)
                self.assertEqual(result['statement_class'],'creative_direction')
        for text in ('Мы постараемся ответить на каждый вопрос.',
                     'Мы предоставим рекомендации по уходу.',
                     'Мы ремонтируем любые модели обуви.'):
            with self.subTest(text=text):
                self.assertIsNone(classify_obvious_non_business(
                    text=text,field_type='document_narrative',
                    section_path=['SMM-стратегия','Редакционные направления']))

    def test_general_domain_information_and_conditional_question_cta(self):
        for text in ('Изношенные тормозные колодки оставляют следы на роторах.',
                     'В стартовый набор обычно входят карандаши, ластик и бумага.'):
            with self.subTest(text=text):
                result=classify_obvious_non_business(
                    text=text,field_type='caption',section_path=['Контент-план'])
                self.assertIsNotNone(result)
                self.assertEqual(result['statement_class'],'general_information')
        cta=classify_obvious_non_business(
            text='Если у вас есть вопросы о составе набора — напишите в комментариях.',
            field_type='caption',section_path=['Контент-план'])
        self.assertIsNotNone(cta)
        self.assertEqual(cta['statement_class'],'audience_prompt')
        self.assertIsNone(classify_obvious_non_business(
            text='Если у вас есть вопросы — напишите в комментариях, мы ответим.',
            field_type='caption',section_path=['Контент-план']))

    def test_strategy_measurement_and_editorial_quality_are_not_company_facts(self):
        cases=(
            ('Активность в чате отслеживается через модерацию.',
             ['Стратегия SMM','План измерения'],'planning_constraint'),
            ('Контент разработан с учетом ограничений и требований.',
             ['Стратегия SMM','Заключение'],'creative_direction'),
        )
        for text,sections,expected in cases:
            with self.subTest(text=text):
                result=classify_obvious_non_business(
                    text=text,field_type='document_narrative',section_path=sections)
                self.assertIsNotNone(result)
                self.assertEqual(result['statement_class'],expected)
        self.assertIsNone(classify_obvious_non_business(
            text='Наша команда отслеживает активность в чате через модерацию.',
            field_type='document_narrative',section_path=['Стратегия SMM','План измерения']))

    def test_format_markers_and_strategy_kpis_are_excluded_from_business_fact_inventory(self):
        strategy=('```markdown\n## Измеримые KPI\n- Количество лайков: отслеживать базовый уровень.\n'
                  '1. Сначала выбрать тему.\n```\n## Услуги\nМастерская заменяет набойки после осмотра.')
        captured=[]
        def chat(payload, **kwargs):
            import json
            rows=json.loads(payload['messages'][1]['content'].split(
                'СЕГМЕНТЫ С ИХ СТРУКТУРНЫМИ ПОЛЯМИ:\n',1)[1])
            captured.extend(rows)
            return {'message':{'content':json.dumps({'segment_decisions':{
                row['evidence_id']:'business_claim' if 'Мастерская заменяет' in row['text']
                else 'not_business_claim' for row in rows}})}}
        with patch('independent_acceptance_editor.request_chat',side_effect=chat):
            result=_inventory_business_assertions('Проверь документ.',strategy,'')
        submitted=' '.join(row['text'] for row in captured)
        self.assertNotIn('```markdown',submitted)
        self.assertNotIn('1.',submitted)
        self.assertEqual(len(result),1)
        self.assertIn('Мастерская заменяет',result[0]['quote'])

    def test_unseen_first_person_business_action_is_candidate(self):
        brief = 'Создай материалы для мастерской. Не придумывай неподтверждённые факты.'
        text = 'В стратегии: Мы диагностируем велосипед перед настройкой переключателей.'
        found = scan_unsupported_business_claims(brief, text, '')
        self.assertTrue(any('Мы диагностируем велосипед' in item['quote'] for item in found))

    def test_promotional_attribute_before_business_noun_is_candidate(self):
        brief = 'Составь стратегию. Не придумывай неподтверждённые факты.'
        found = scan_unsupported_business_claims(
            brief, 'Позиционирование: Профессиональная, надёжная и доступная мастерская.', '')
        self.assertTrue(any('Профессиональная' in item['quote'] for item in found))

    def test_shared_claim_family_does_not_authorize_a_different_claim(self):
        brief = 'Мы проводим диагностику велосипедов. Не придумывай неподтверждённые факты.'
        found = scan_unsupported_business_claims(
            brief, 'Мы гарантируем отсутствие любых поломок после обслуживания.', '')
        self.assertTrue(any('гарантируем отсутствие' in item['quote'] for item in found))

    def test_claim_inventory_instructions_classify_copy_assertions_as_business_claims(self):
        captured = []

        def chat(payload, **kwargs):
            captured.append(payload)
            import json
            import re
            user = payload['messages'][1]['content']
            rows = json.loads(user.split('СЕГМЕНТЫ С ИХ СТРУКТУРНЫМИ ПОЛЯМИ:\n', 1)[1])
            decisions = {row['evidence_id']: ('business_claim' if 'Мы ремонтируем' in row['text']
                                                else 'not_business_claim') for row in rows}
            return {'message': {'content': json.dumps({'segment_decisions': decisions})}}

        with patch('independent_acceptance_editor.request_chat', side_effect=chat):
            result = _inventory_business_assertions(
                'Создай контент.', '',
                '| День | Подпись |\n|---|---|\n| Вт | Мы ремонтируем велосипеды. |')
        self.assertEqual(len(result), 1)
        prompt = captured[0]['messages'][0]['content']
        self.assertIn('даже внутри предлагаемой публикации или сценария', prompt)
        self.assertIn('Выбирай business_claim только если сегмент утверждает конкретный факт/обещание именно о компании', prompt)

    def test_malformed_editor_response_gets_one_bounded_higher_budget_retry(self):
        from independent_acceptance_editor import _call_editor_once
        payloads = []

        def chat(payload, **kwargs):
            payloads.append(payload)
            content = '{"pass":true,"issues":[]}' if len(payloads) == 2 else '{"pass":'
            return {'message': {'content': content}}

        with patch('independent_acceptance_editor.request_chat', side_effect=chat):
            verdict = _call_editor_once('Проверь документы.', 'Стратегия проекта.', '',
                                        strategy_only=True)
        self.assertTrue(verdict['pass'])
        self.assertEqual([item['options']['num_predict'] for item in payloads], [2000, 4000])

    def test_empty_editor_response_gets_one_higher_budget_retry(self):
        from independent_acceptance_editor import _call_editor_once
        payloads=[]
        def chat(payload, **kwargs):
            payloads.append(payload)
            content='' if len(payloads)==1 else '{"pass":true,"issues":[]}'
            return {'message':{'content':content}}
        with patch('independent_acceptance_editor.request_chat',side_effect=chat):
            verdict=_call_editor_once('Проверь документ.','Стратегия проекта.','',strategy_only=True)
        self.assertTrue(verdict['pass'])
        self.assertEqual([p['options']['num_predict'] for p in payloads],[2000,4000])


class RepairSlotResolutionTests(unittest.TestCase):
    def setUp(self):
        self.calendar = (
            '| Неделя/день | Площадка и формат | Конкретная тема | Цель | Сценарий/визуал | '
            'Текст/ключевые реплики | CTA | KPI | Метод измерения | Целевая аудитория | '
            'Необходимые материалы |\n'
            '|---|---|---|---|---|---|---|---|---|---|---|\n'
            '| 1/Вт | Instagram, карусель | Проверка тормозов | Объяснить диагностику | '
            'Три кадра проверки | Тормоза — первое, что должно работать. | Напишите вопрос | '
            'Комментарии | Insights | Владельцы велосипедов | Фото деталей |')

    def test_host_row_context_maps_issue_when_claim_quote_is_a_cell(self):
        issue = {'document': 'calendar', 'quote': 'Тормоза — первое, что должно работать.',
                 'row_context': {'slot': '1/Вт', 'fields': {'Текст': 'Тормоза — первое, что должно работать.'}}}
        self.assertEqual(_calendar_slots_for_issues([issue], self.calendar), ['1/Вт'])
        self.assertEqual(_calendar_issues_by_slot([issue], self.calendar), {'1/Вт': [issue]})


if __name__ == '__main__':
    unittest.main()
