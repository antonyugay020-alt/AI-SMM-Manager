"""Contract tests for source-grounded resolution of reviewer fact disputes."""
import json
import unittest
from unittest.mock import patch

from claim_support import (adjudicate_factual_findings, _quote_options,
                           _source_quote_authority, _citation_fit_is_weak,
                           _is_planning_metadata)
from evidence_context import build_evidence_context, EvidenceSource
from independent_acceptance_editor import call_editor
from independent_acceptance_editor import parse_verdict
from independent_acceptance_editor import _evidence_chunks, _negated_claim_is_not_an_assertion


class ClaimSupportTests(unittest.TestCase):
    def test_future_or_nominal_actions_in_strategy_sections_are_not_claimed_as_existing_operations(self):
        recommendations=[
            {'document':'strategy','quote':'Работать с ВКонтакте, используя визуальные материалы для объяснения темы.',
             'section_path':['Стратегия SMM','Канал и продвижение']},
            {'document':'strategy','quote':'Подготовка материалов после согласования темы с владельцем.',
             'section_path':['Стратегия SMM','План действий']},
            {'document':'strategy','quote':'Демонстрация доступности вводного занятия для новичков.',
             'section_path':['Стратегия SMM','Контентные направления']},
            {'document':'strategy','quote':'Необходимость подтверждения деталей услуги до их публикации.',
             'section_path':['Стратегия SMM','Риски и данные для уточнения']},
        ]
        self.assertTrue(all(_is_planning_metadata(item) for item in recommendations))

    def test_present_company_operation_remains_source_gated_inside_strategy_section(self):
        assertion={'document':'strategy',
            'quote':'Наша студия проводит вводные занятия каждый день.',
            'section_path':['Стратегия SMM','Канал и продвижение']}
        self.assertFalse(_is_planning_metadata(assertion))

    def test_metric_name_and_measurement_pair_in_strategy_section_is_planning_metadata(self):
        issue={'quote':'- **Количество взаимодействий** — Instagram Insights',
               'section_path':['Стратегия SMM','Метрики и измерение']}
        self.assertTrue(_is_planning_metadata(issue))
        promise={'quote':'Мы ответим на все обращения в течение часа.',
                 'section_path':['Стратегия SMM','Метрики и измерение']}
        self.assertFalse(_is_planning_metadata(promise))

    def test_requested_content_plan_total_is_not_misread_as_a_business_cadence(self):
        item={'document':'strategy','type':'invented_fact',
            'quote':'- Всего: 4 публикации за указанный период.',
            'reason':'Параметр контентного плана не подтверждён как режим работы бизнеса.',
            'field_type':'document_narrative',
            'section_path':['SMM-стратегия','Параметры контентного плана']}
        self.assertTrue(_is_planning_metadata(item))
        item['quote']='Наша студия публикует всего 4 публикации за период.'
        self.assertFalse(_is_planning_metadata(item))
        calls=[]
        result=adjudicate_factual_findings(
            'Контент-план: одна неделя, всего 4 публикации.',[{
                **item,'quote':'- Всего: 4 публикации за указанный период.'}],
            ask=lambda *args,**kwargs:calls.append(args))
        self.assertEqual(calls,[])
        self.assertEqual(result['blocking'],[])
        self.assertEqual(result['non_business'][0]['statement_class'],'planning_constraint')

    def test_authorized_booking_condition_accepts_semantic_paraphrase(self):
        self.assertFalse(_citation_fit_is_weak(
            'commercial_condition',
            'Предоставляем услуги по предварительной записи.',
            'Мастерская принимает клиентов по предварительной записи.',
            matched=2, content_terms=4))

    def test_commercial_paraphrase_still_requires_exact_values_and_conditions(self):
        self.assertTrue(_citation_fit_is_weak(
            'commercial_condition', 'Стоимость занятия — 5000 тенге.',
            'Занятие стоит 4000 тенге.', matched=2, content_terms=4))
        self.assertTrue(_citation_fit_is_weak(
            'commercial_condition', 'Услуги предоставляются бесплатно.',
            'Услуги предоставляются.', matched=2, content_terms=3))
        self.assertTrue(_citation_fit_is_weak(
            'commercial_condition', 'Принимаем клиентов по записи.',
            'Принимаем клиентов без записи.', matched=2, content_terms=4))

    def test_source_citations_are_atomic_and_do_not_merge_brief_roles(self):
        request = (
            'Студия «Глина» проводит занятия по ручной лепке. '
            'Цель: объяснить процесс занятия и получить вопросы о записи. '
            'Расписание и цена неизвестны. Не выдумывай результаты учеников.'
        )
        source = EvidenceSource('request:test', 'user_request', 'Бриф', request)
        options = _quote_options((source,), {
            'quote': 'В занятии можно создать готовый предмет',
            'reason': 'Не подтверждён обещанный результат занятия.'
        })
        quotes = [item['quote'] for item in options.values()]
        self.assertIn('Студия «Глина» проводит занятия по ручной лепке.', quotes)
        self.assertIn('Цель: объяснить процесс занятия и получить вопросы о записи.', quotes)
        self.assertIn('Расписание и цена неизвестны.', quotes)
        self.assertIn('Не выдумывай результаты учеников.', quotes)
        self.assertNotIn(request, quotes)
        self.assertTrue(all(len(quote.split()) <= 40 for quote in quotes))

    def test_business_support_catalog_excludes_goals_unknowns_and_prohibitions(self):
        from claim_support import _quote_options
        request=('Студия «Глина» проводит занятия по ручной лепке. '
                 'Цель: объяснить процесс занятия. Цена и расписание неизвестны. '
                 'Не выдумывай результаты учеников.')
        source=EvidenceSource('request:test','user_request','Бриф',request)
        options=_quote_options((source,),{'quote':'Утверждение о занятии.'},authoritative_only=True)
        quotes=[item['quote'] for item in options.values()]
        self.assertEqual(quotes,['Студия «Глина» проводит занятия по ручной лепке.'])

    def test_prohibitions_are_not_positive_authority_even_when_they_repeat_a_claim_term(self):
        quote='Нельзя придумывать цены и обещания полной безопасности.'
        self.assertFalse(_source_quote_authority(quote)[0])
        source=EvidenceSource('request:test','user_request','Бриф',quote)
        options=_quote_options((source,),{'quote':'Услуга гарантирует безопасность велосипеда.'},
                               authoritative_only=True)
        self.assertEqual(options,{})

    def test_fact_and_unknown_in_one_user_sentence_become_separate_authority_spans(self):
        request=('Проект «Языковой клуб»: разговорные занятия английским для взрослых '
                 'начинающего уровня в небольших группах, расписание и стоимость пока не определены.')
        source=EvidenceSource('request:test','user_request','Бриф',request)
        issue={'quote':'В клубе проходят разговорные занятия английским для взрослых новичков в небольших группах.'}
        all_options=_quote_options((source,),issue)
        options=_quote_options((source,),issue,authoritative_only=True)
        all_quotes=[item['quote'] for item in all_options.values()]
        quotes=[item['quote'] for item in options.values()]
        self.assertEqual(all_quotes,['Проект «Языковой клуб»: разговорные занятия английским для взрослых начинающего уровня в небольших группах',
                                     'расписание и стоимость пока не определены.'])
        self.assertEqual(quotes,[all_quotes[0]])
        self.assertTrue(_source_quote_authority(quotes[0])[0])
        self.assertFalse(_source_quote_authority(all_quotes[1])[0])

    def test_goal_quote_cannot_authorize_a_service_process_or_result(self):
        request = 'Цель: объяснить процесс занятия и получить вопросы о записи.'
        issue = {'document':'calendar','type':'invented_fact',
                 'quote':'В нашей мастерской снимаем мерки и вручную подгоняем каждое платье.',
                 'reason':'Не подтверждены способы работы мастерской.'}
        def ask(model, system, user):
            payload=json.loads(user)
            self.assertEqual(payload['authorized_quote_options'],[])
            return {'decisions':{'F001':{'invitation_kind':'none',
                'statement_class':'operational_process','speech_act':'assertion','assertion_subject':'business','source_relation':'unrelated','verdict':'unsupported',
                'source_quote_id':'none',
                'reason':'Цель пользователя упоминает процесс занятия.'}}}
        result=adjudicate_factual_findings(request,[issue],ask=ask)
        self.assertEqual(len(result['supported']),0)
        self.assertEqual(result['blocking'][0]['adjudication'],'unsupported')

    def test_explicit_business_fact_remains_valid_evidence(self):
        request='Мастерская снимает мерки и вручную подгоняет каждое платье.'
        claim='В нашей мастерской снимаем мерки и вручную подгоняем каждое платье.'
        issue={'document':'calendar','type':'invented_fact','quote':claim,
               'reason':'Нужно проверить способы работы.'}
        def ask(model,system,user):
            payload=json.loads(user)
            quote_id=next(row['quote_id'] for row in payload['authorized_quote_options']
                          if row['quote']=='Мастерская снимает мерки и вручную подгоняет каждое платье.')
            return {'decisions':{'F001':{'invitation_kind':'none','statement_class':'operational_process','speech_act':'assertion','assertion_subject':'business','source_relation':'entails',
                'verdict':'supported','source_quote_id':quote_id,
                'reason':'Действия мастерской прямо указаны в запросе.'}}}
        result=adjudicate_factual_findings(request,[issue],ask=ask)
        self.assertEqual(result['blocking'],[])
        self.assertEqual(result['supported'][0]['support']['quote'],request)

    def test_exact_affirmative_user_fact_is_supported_without_model_reinterpretation(self):
        request=('Проект «Линия». В работе есть только следующая подтверждённая информация: '
                 'вводные занятия по рисунку для взрослых без предварительной подготовки.')
        exact='вводные занятия по рисунку для взрослых без предварительной подготовки'
        issue={'document':'strategy','type':'invented_fact','quote':exact,
               'reason':'Факт требует проверки по источникам.'}
        result=adjudicate_factual_findings(request,[issue],
            ask=lambda *args: self.fail('an exact affirmative source match is deterministic'))
        self.assertEqual(result['blocking'],[])
        self.assertEqual(len(result['supported']),1)
        self.assertEqual(result['supported'][0]['support']['quote'],
            'вводные занятия по рисунку для взрослых без предварительной подготовки.')
        self.assertEqual(result['supported'][0]['adjudication'],'supported')

    def test_broad_service_fact_cannot_support_detailed_materials_claim(self):
        request='Студия «Глина» проводит занятия по ручной лепке для взрослых новичков.'
        issue={'document':'calendar','type':'invented_fact',
               'quote':'На занятии участники получают глину, инструменты и формочки.',
               'reason':'Не подтверждены материалы и комплектация.'}
        def ask(model,system,user):
            payload=json.loads(user)
            option=next(row for row in payload['authorized_quote_options']
                        if 'занятия по ручной лепке' in row['quote'])
            return {'decisions':{'F001':{'invitation_kind':'none','statement_class':'operational_process','speech_act':'assertion','assertion_subject':'business','source_relation':'entails',
                'verdict':'supported','source_quote_id':option['quote_id'],
                'reason':'В запросе указаны занятия по лепке.'}}}
        result=adjudicate_factual_findings(request,[issue],ask=ask)
        self.assertEqual(result['supported'],[])
        self.assertEqual(result['blocking'][0]['adjudication'],'unsupported')
        self.assertEqual(result['blocking'][0]['adjudication_trace']['host_source_role'],
                         'weak_topical_overlap')

    def test_audience_goal_cannot_authorize_business_outcome(self):
        request='Аудитория: работающие взрослые, которые хотят увереннее говорить.'
        source=EvidenceSource('request:test','user_request','Бриф',request)
        options=_quote_options((source,),{'quote':'Занятия помогают каждому говорить уверенно.'},
                               authoritative_only=True)
        self.assertEqual(options,{})

    def test_inventory_field_context_reaches_source_adjudicator(self):
        request='Студия проводит занятия по ручной лепке.'
        issue={'document':'strategy','type':'invented_fact',
               'quote':'Мы ответим на любой вопрос о занятиях.',
               'reason':'Не подтверждён ответ компании.',
               'evidence_id':'strategy:0009',
               'evidence_segment':'Мы ответим на любой вопрос о занятиях.',
               'section_path':['Публикация 1','Готовый текст'],
               'field_role':'Готовый текст',
               'row_context':{}}
        def ask(model,system,user):
            payload=json.loads(user)
            context=payload['findings'][0]['claim_context']
            self.assertEqual(context['field_role'],'Готовый текст')
            self.assertEqual(context['section_path'],['Публикация 1','Готовый текст'])
            return {'decisions':{'F001':{'invitation_kind':'none','statement_class':'operational_process','speech_act':'assertion','assertion_subject':'business','source_relation':'unrelated',
                'verdict':'unsupported','source_quote_id':'none',
                'reason':'Ответ компании не указан в источнике.'}}}
        result=adjudicate_factual_findings(request,[issue],ask=ask)
        self.assertEqual(result['blocking'][0]['adjudication'],'unsupported')

    def test_claim_scope_ignores_neighboring_sentence_in_evidence_context(self):
        request='Языковой клуб проводит разговорные занятия английским для взрослых новичков.'
        claim='В клубе проходят разговорные занятия английским для взрослых, начинающих изучать язык.'
        issue={'document':'calendar','type':'invented_fact','quote':claim,
               'evidence_segment':claim+' Дополнительный контекст упоминает аудиторию, канал и цель кампании.',
               'reason':'Нужно сверить описание услуги.'}
        def ask(model,system,user):
            payload=json.loads(user)
            option=next(row for row in payload['authorized_quote_options']
                        if row['quote']==request)
            return {'decisions':{'F001':{'invitation_kind':'none','statement_class':'business_fact','speech_act':'assertion','assertion_subject':'business','source_relation':'entails',
                'verdict':'supported','source_quote_id':option['quote_id'],
                'reason':'Источник подтверждает те же занятия и аудиторию.'}}}
        result=adjudicate_factual_findings(request,[issue],ask=ask)
        self.assertEqual(result['blocking'],[])
        self.assertEqual(result['supported'][0]['support']['quote'],request)

    def test_planning_metadata_audit_event_has_claim_context(self):
        issue={'document':'strategy','type':'invented_fact','quote':'Цель: объяснить пользу профилактики.',
               'field_role':'Общая стратегия','evidence_id':'strategy:0001'}
        events=[]
        result=adjudicate_factual_findings(issue['quote'],[issue],
            ask=lambda *args: self.fail('planning metadata should bypass model'),
            report=lambda stage,**data: events.append((stage,data)))
        self.assertEqual(result['blocking'],[])
        self.assertEqual(events[0][0],'source_adjudication_finding_classified')
        self.assertEqual(events[0][1]['evidence_id'],'strategy:0001')

    def test_markdown_bold_goal_is_planning_metadata(self):
        issue={'document':'strategy','type':'invented_fact',
               'quote':'**Цель:** Объяснить пользу профилактики и получить обращения на диагностику.',
               'field_role':'Общая стратегия'}
        result=adjudicate_factual_findings(issue['quote'],[issue],ask=lambda *args: self.fail('unexpected'))
        self.assertEqual(result['blocking'],[])
        self.assertEqual(result['non_business'][0]['adjudication'],'not_business_claim')

    def test_user_goal_is_routed_as_planning_metadata_without_model_call(self):
        issue={'document':'strategy','type':'invented_fact',
               'quote':'Цель: объяснить пользу профилактики и получить обращения на диагностику.',
               'field_role':'Общая стратегия',
               'reason':'Недостаточно фактов о компании.'}
        def ask(*args):
            self.fail('Параметр цели не должен попадать в арбитраж бизнес-фактов')
        result=adjudicate_factual_findings(issue['quote'],[issue],ask=ask)
        self.assertEqual(result['blocking'],[])
        self.assertEqual(result['non_business'][0]['statement_class'],'planning_constraint')

    def test_strategy_intent_in_conclusion_is_planning_not_a_business_fact(self):
        statement = ('Стратегия направлена на информирование аудитории о важности '
                     'профилактики велосипеда и получение обращений на диагностику.')
        issue={'document':'strategy','type':'invented_fact','quote':statement,
               'field_role':'Заключение',
               'reason':'Цель стратегии не подтверждена фактами о компании.'}
        result=adjudicate_factual_findings(statement,[issue],
            ask=lambda *args: self.fail('Стратегическая цель не должна попадать в проверку фактов'))
        self.assertEqual(result['blocking'],[])
        self.assertEqual(result['non_business'][0]['adjudication'],'not_business_claim')

    def test_strategy_goal_paraphrase_in_goal_section_bypasses_fact_adjudication(self):
        issue={'document':'strategy','type':'invented_fact',
               'quote':'Студия «Глина» может использовать контент, чтобы получить вопросы о записи.',
               'section_path':['SMM-стратегия','2. Цели и KPI'],
               'field_role':'Цели',
               'reason':'Это сформулированная цель контентной работы.'}
        result=adjudicate_factual_findings(issue['quote'],[issue],
            ask=lambda *args:self.fail('goal prose should be classified from its section and intent'))
        self.assertEqual(result['blocking'],[])
        self.assertEqual(result['non_business'][0]['adjudication'],'not_business_claim')

    def test_specific_consultation_cta_cannot_be_whitelisted_by_model(self):
        request='Мастерская ремонтирует и подгоняет одежду.'
        issue={'document':'calendar','type':'invented_fact',
               'quote':'Запишитесь на консультацию.', 'field_role':'CTA',
               'section_path':['Контент-план','Неделя 1'],
               'reason':'Проверить, доступна ли консультация.'}
        def ask(model,system,user):
            return {'decisions':{'F001':{'invitation_kind':'service_access','statement_class':'invitation_cta','speech_act':'invitation','assertion_subject':'reader','source_relation':'not_needed',
                'verdict':'supported','source_quote_id':'none',
                'reason':'Призыв записаться является CTA.'}}}
        result=adjudicate_factual_findings(request,[issue],ask=ask)
        self.assertEqual(result['non_business'],[])
        self.assertEqual(result['supported'],[])
        self.assertEqual(result['blocking'][0]['adjudication'],'unsupported')

    def test_neutral_question_cta_can_be_host_classified_without_business_source(self):
        issue={'document':'calendar','type':'invented_fact','quote':'Задайте нам вопрос.',
               'field_role':'CTA','section_path':['Контент-план','Неделя 1'],
               'reason':'Нейтральное приглашение к диалогу.'}
        result=adjudicate_factual_findings(issue['quote'],[issue],
            ask=lambda *args:self.fail('Host-classified neutral CTA needs no factual adjudication'))
        self.assertEqual(result['blocking'],[])
        self.assertEqual(result['non_business'][0]['statement_class'],'invitation_cta')

    def test_simple_business_fact_paraphrase_needs_a_core_source_anchor(self):
        request='Для мастерской «Веломир»: диагностика и ремонт городских велосипедов.'
        claim='Категория: обслуживание велосипедов.'
        issue={'document':'strategy','type':'invented_fact','quote':claim,
               'reason':'Краткое описание категории по перечисленным услугам.'}
        def ask(model,system,user):
            payload=json.loads(user)
            option=next(row for row in payload['authorized_quote_options']
                        if row['quote']==request)
            return {'decisions':{'F001':{'invitation_kind':'none','statement_class':'business_fact','speech_act':'assertion','assertion_subject':'business','source_relation':'entails',
                'verdict':'supported','source_quote_id':option['quote_id'],
                'reason':'Цитата прямо перечисляет услуги по ремонту велосипедов.'}}}
        result=adjudicate_factual_findings(request,[issue],ask=ask)
        self.assertEqual(result['blocking'],[])
        self.assertEqual(len(result['supported']),1)

    def test_outcome_claim_stays_blocked_when_source_only_shares_business_topic(self):
        request='Студия «Глина» проводит занятия по ручной лепке для взрослых новичков.'
        claim='Занятия обеспечивают комфорт и безопасность.'
        issue={'document':'calendar','type':'invented_fact','quote':claim,
               'reason':'Проверка обещания результата.'}
        def ask(model,system,user):
            payload=json.loads(user)
            option=next(row for row in payload['authorized_quote_options']
                        if row['quote']==request)
            return {'decisions':{'F001':{'invitation_kind':'none','statement_class':'service_outcome','speech_act':'assertion','assertion_subject':'business','source_relation':'entails',
                'verdict':'supported','source_quote_id':option['quote_id'],
                'reason':'Источник подтверждает занятия по ручной лепке.'}}}
        result=adjudicate_factual_findings(request,[issue],ask=ask)
        self.assertEqual(result['supported'],[])
        self.assertEqual(result['blocking'][0]['adjudication_trace']['host_source_role'],
                         'weak_topical_overlap')

    def test_measurement_field_is_planning_even_when_it_contains_a_numeric_target(self):
        issue={'document':'strategy','type':'invented_fact',
               'quote':'Рост сообщений в директе на 15% (гипотеза для проверки).',
               'field_role':'KPI',
               'reason':'Целевой процент не подтверждён исходными данными.'}
        result=adjudicate_factual_findings(issue['quote'],[issue],ask=lambda *args: self.fail('unexpected call'))
        self.assertEqual(result['blocking'],[])
        self.assertEqual(result['non_business'][0]['adjudication'],'not_business_claim')

    def test_unknown_source_span_cannot_support_a_positive_business_claim(self):
        request='Процессы, способы работы, сроки и результаты проекта не указаны.'
        issue={'document':'calendar','type':'invented_fact',
               'quote':'В нашей мастерской снимаем мерки и вручную подгоняем каждое платье.',
               'reason':'Не подтверждены способы работы мастерской.'}
        def ask(model,system,user):
            payload=json.loads(user)
            self.assertEqual(payload['authorized_quote_options'],[])
            return {'decisions':{'F001':{'invitation_kind':'none','statement_class':'operational_process','speech_act':'assertion','assertion_subject':'business','source_relation':'unrelated',
                'verdict':'unsupported','source_quote_id':'none',
                'reason':'Источник относится к процессам проекта.'}}}
        result=adjudicate_factual_findings(request,[issue],ask=ask)
        self.assertEqual(result['supported'],[])
        self.assertEqual(result['blocking'][0]['adjudication'],'unsupported')

    def test_supported_verdict_without_authorized_citation_is_a_rejected_claim(self):
        request='Цель: объяснить процесс подгонки. Условия неизвестны.'
        issue={'document':'calendar','type':'invented_fact',
               'quote':'В нашей мастерской каждую вещь подгоняют за один день.',
               'reason':'Не подтверждены процесс и срок.'}
        events=[]
        def ask(model,system,user):
            payload=json.loads(user)
            self.assertEqual(payload['authorized_quote_options'],[])
            return {'decisions':{'F001':{'invitation_kind':'none',
                'statement_class':'operational_process','speech_act':'assertion','assertion_subject':'business','source_relation':'entails','verdict':'supported',
                'source_quote_id':'none',
                'reason':'Такой процесс кажется обычным для мастерской.'}}}
        result=adjudicate_factual_findings(request,[issue],ask=ask,
            report=lambda stage,**details:events.append((stage,details)))
        self.assertEqual(result['supported'],[])
        self.assertEqual(result['blocking'][0]['adjudication'],'unsupported')
        self.assertEqual(result['blocking'][0]['adjudication_trace']['host_source_role'],
                         'no_authoritative_citation')
        self.assertFalse(any(item.get('adjudication')=='unavailable'
                             for item in result['blocking']))
        self.assertTrue(any(stage=='source_adjudication_uncited_support_rejected'
                            for stage,_ in events))

    def test_editor_outage_does_not_erase_deterministic_findings(self):
        hard={'document':'strategy','type':'unsafe_claim','quote':'гарантируем результат',
              'reason':'Обнаружена неподтверждённая гарантия.'}
        with patch('independent_acceptance_editor._review_windows',
                   return_value=[('Стратегия','Календарь')]), \
             patch('factual_claim_audit.scan_unsupported_business_claims',
                   side_effect=lambda *a,include_semantic_facts=False,**k:[hard] if not include_semantic_facts else [hard]), \
             patch('independent_acceptance_editor._call_editor_once',
                   side_effect=ValueError('malformed live verdict')):
            result=call_editor('Не обещай гарантий.','Стратегия','Календарь')
        self.assertFalse(result['pass'])
        self.assertEqual(result['review_state'],'unavailable')
        self.assertTrue(any(x.get('quote')==hard['quote'] for x in result['issues']))
        self.assertTrue(any(x.get('type')=='review_unavailable' for x in result['issues']))

    def test_absence_finding_requires_verbatim_user_requirement(self):
        request = 'В каждой публикации укажи цену и адрес.'
        strategy = 'Стратегия без цены.'
        calendar = 'Строка календаря без адреса.'
        issue = {'document':'calendar','type':'missing_required_content',
                 'evidence_id':'calendar:0001','claim':'без адреса',
                 'reason':'Не указан адрес, требуемый пользователем.'}
        raw = json.dumps({'pass':False,'issues':[issue]}, ensure_ascii=False)
        result = parse_verdict(raw, strategy=strategy, calendar=calendar, request=request)
        self.assertTrue(result['pass'])
        self.assertEqual(result['issues'], [])

        issue['requirement_quote'] = 'В каждой публикации укажи цену и адрес'
        raw = json.dumps({'pass':False,'issues':[issue]}, ensure_ascii=False)
        result = parse_verdict(raw, strategy=strategy, calendar=calendar, request=request)
        self.assertFalse(result['pass'])
        self.assertEqual(result['issues'][0]['requirement_quote'],
                         'В каждой публикации укажи цену и адрес')

    def test_unrelated_detail_cannot_borrow_a_general_checklist_requirement(self):
        request = ('Для каждого материала нужны отдельная тема, подробный текст, '
                   'визуальный сценарий, CTA, KPI и метод измерения.')
        quote = request
        issue = {'document':'calendar','type':'missing_required_content',
                 'evidence_id':'calendar:0001','claim':'Текст публикации',
                 'reason':'Отсутствует описание методов и условий создания комфортной среды для практики.',
                 'requirement_quote':quote}
        # Ground the document citation separately; the requirement quote is
        # exact, but it does not require the specific teaching details claimed.
        calendar = 'Текст публикации готов.'
        ref = next(k for k,v in _evidence_chunks('',calendar).items() if 'Текст публикации готов.' in v)
        issue['evidence_id'] = ref
        raw = json.dumps({'pass':False,'issues':[issue]}, ensure_ascii=False)
        result = parse_verdict(raw, strategy='Стратегия.', calendar=calendar, request=request)
        self.assertTrue(result['pass'])
        self.assertEqual(result['issues'], [])

    def test_negated_user_policy_in_document_is_not_a_business_assertion(self):
        request = 'Не обещай гарантированный результат.'
        strategy = 'Не обещай гарантированный результат.'
        calendar = 'Не обещай гарантированный результат.'
        ref = next(k for k,v in _evidence_chunks(strategy,calendar).items()
                   if k.startswith('calendar:') and 'гарантированный результат' in v)
        issue = {'document':'calendar','type':'invented_fact','evidence_id':ref,
                 'claim':'гарантированный результат',
                 'reason':'Упомянут гарантированный результат без подтверждения.'}
        parsed = parse_verdict(json.dumps({'pass':False,'issues':[issue]},ensure_ascii=False),
                                strategy=strategy,calendar=calendar,request=request)
        self.assertTrue(_negated_claim_is_not_an_assertion(parsed['issues'][0]))

    def test_final_editor_does_not_send_copied_negation_to_fact_adjudicator(self):
        request='Создай краткий обзор творческой студии.'
        segment='В материалах: не обещай гарантированный результат.'
        issue={'document':'calendar','type':'invented_fact',
               'quote':'гарантированный результат','reason':'Обещание не подтверждено.',
               'evidence_segment':segment}
        with patch('independent_acceptance_editor._review_windows',
                   return_value=[('Стратегия','Календарь')]), \
             patch('independent_acceptance_editor._call_editor_once',
                   return_value={'pass':False,'issues':[issue]}), \
             patch('factual_claim_audit.scan_unsupported_business_claims',return_value=[]), \
             patch('claim_support.adjudicate_factual_findings',
                   side_effect=AssertionError('negated policy must not be treated as a claim')):
            result=call_editor(request,'Стратегия','Календарь')
        self.assertTrue(result['pass'],result)

    def test_supported_semantic_paraphrase_requires_exact_authorized_citation(self):
        request = 'Языковой клуб проводит разговорные занятия английским для взрослых новичков.'
        context = build_evidence_context(request).to_dict()
        source = context['sources'][0]
        issue = {'document': 'calendar', 'type': 'invented_fact',
                 'quote': 'В клубе проходят разговорные занятия английским для начинающих.',
                 'reason': 'Не подтверждён формат занятия.'}

        def ask(model, system, user):
            payload = json.loads(user)
            self.assertEqual(payload['findings'][0]['claim'], issue['quote'])
            return json.dumps({'verdict': 'supported', 'source_id': source['source_id'],
                               'source_quote': 'проводит разговорные занятия английским для взрослых новичков',
                               'reason': 'Запрос прямо задаёт клуб и его формат.'}, ensure_ascii=False)

        result = adjudicate_factual_findings(request, [issue], evidence_context=context, ask=ask)
        self.assertEqual(len(result['supported']), 1)
        self.assertEqual(result['supported'][0]['support']['source_id'], source['source_id'])
        self.assertEqual(result['blocking'], [])

    def test_planning_constraints_and_knowledge_boundaries_are_not_business_claims(self):
        request = 'Подготовь проект и не придумывай неизвестные бизнес-данные.'
        findings = [
            {'document':'strategy','type':'invented_fact',
             'quote':'Не использовать гарантированный результат.',
             'reason':'Упомянута гарантия.'},
            {'document':'calendar','type':'invented_fact',
             'quote':'Стоимость и адрес не указаны.',
             'reason':'Указаны неизвестные данные.'},
        ]
        calls = []
        def ask(model, system, user):
            payload = json.loads(user)
            calls.append(payload)
            classes = ('planning_constraint', 'knowledge_boundary')
            return {'decisions':[{'finding_id':finding['finding_id'],
                    'statement_class':statement_class, 'verdict':'supported',
                    'source_id':'none', 'source_quote_id':'none',
                    'reason':'Это ограничение планирования, а не утверждение о компании.'}
                    for finding, statement_class in zip(payload['findings'],classes)]}
        result = adjudicate_factual_findings(request, findings, ask=ask)
        self.assertEqual(calls, [])
        self.assertEqual(len(result['non_business']), 2)
        self.assertEqual(result['blocking'], [])
        self.assertEqual([x['statement_class'] for x in result['non_business']],
                         ['planning_constraint', 'knowledge_boundary'])

    def test_pattern_and_editor_findings_share_one_source_adjudication(self):
        request='Создай план публикаций для мастерской.'
        pattern={'document':'calendar','type':'invented_fact','quote':'ручная подгонка каждого изделия',
                 'reason':'Не подтверждён фактический процесс.'}
        reviewer={'document':'calendar','type':'invented_fact','quote':'идеальная посадка за день',
                  'reason':'Не подтверждён результат услуги.'}
        def detector(req, strategy, calendar, evidence_context, *, include_semantic_facts):
            return [pattern] if include_semantic_facts else []
        captured=[]
        def resolve(req, findings, **kwargs):
            captured.extend(findings)
            return {'supported':[], 'blocking':[{**item, 'adjudication':'unsupported'}
                                                 for item in findings]}
        with patch('independent_acceptance_editor._review_windows',
                   return_value=[('Стратегия','Календарь')]), \
             patch('independent_acceptance_editor._call_editor_once',
                   return_value={'pass':False,'issues':[reviewer]}), \
             patch('factual_claim_audit.scan_unsupported_business_claims', side_effect=detector), \
             patch('claim_support.adjudicate_factual_findings', side_effect=resolve) as adjudicator:
            result=call_editor(request,'Стратегия','Календарь')
        adjudicator.assert_called_once()
        self.assertEqual({item['quote'] for item in captured},
                         {pattern['quote'], reviewer['quote']})
        self.assertFalse(result['pass'])
        self.assertEqual(len(result['issues']), 2)

    def test_live_schema_returns_host_owned_quote_id(self):
        request = 'Языковой клуб проводит разговорные занятия английским для взрослых новичков.'
        context = build_evidence_context(request).to_dict()
        source = context['sources'][0]
        issue = {'document': 'calendar', 'type': 'invented_fact',
                 'quote': 'В клубе есть разговорные занятия.',
                 'reason': 'Не подтверждён формат.'}
        sent = []
        def fake_urlopen(req, timeout):
            payload = json.loads(req.data.decode('utf-8'))
            schema = payload['format']
            self.assertEqual(schema['required'], ['decisions'])
            finding_schema = schema['properties']['decisions']['properties']['F001']
            self.assertIn('source_quote_id', finding_schema['required'])
            self.assertNotIn('source_id', finding_schema['properties'])
            self.assertNotIn('source_quote', finding_schema['properties'])
            sent.append(payload)
            user_data = json.loads(payload['messages'][1]['content'])
            self.assertEqual(len(user_data['findings']), 1)
            self.assertEqual(user_data['findings'][0]['finding_id'], 'F001')
            self.assertTrue(all(isinstance(x.get('content'), str)
                                for x in user_data['authorized_sources']))
            quote_id = next(x['quote_id'] for x in user_data['authorized_quote_options']
                            if x['source_id'] == source['source_id'] and 'разговорные занятия' in x['quote'])
            answer = {'decisions':{'F001':{'invitation_kind':'none',
                      'statement_class':'business_fact','speech_act':'assertion','assertion_subject':'business','source_relation':'entails','verdict':'supported',
                      'source_quote_id':quote_id,
                      'reason':'Фраза прямо подтверждена источником.'}}}
            import io
            return io.BytesIO(json.dumps({'message':{'content':json.dumps(answer,ensure_ascii=False)}},
                                         ensure_ascii=False).encode('utf-8'))
        with patch('claim_support.urllib.request.urlopen', side_effect=fake_urlopen):
            result = adjudicate_factual_findings(request, [issue], evidence_context=context)
        self.assertEqual(len(sent), 1)
        self.assertEqual(result['blocking'], [])
        self.assertIn('разговорные занятия', result['supported'][0]['support']['quote'])

    def test_batch_isolates_missing_decision_without_erasing_valid_sibling(self):
        findings = [
            {'document':'calendar','type':'invented_fact','quote':'Факт A.',
             'reason':'Требуется проверить утверждение A.'},
            {'document':'calendar','type':'invented_fact','quote':'Факт B.',
             'reason':'Требуется проверить утверждение B.'},
        ]
        calls = []
        def ask(model, system, user):
            calls.append(user)
            if len(calls) == 1:
                return {'decisions':[{'invitation_kind':'none','finding_id':'F001','statement_class':'business_fact','speech_act':'assertion','assertion_subject':'business','source_relation':'unrelated',
                    'verdict':'unsupported','source_id':'none','source_quote_id':'none',
                    'reason':'Нет подтверждения в разрешённых источниках.'}]}
            return {'decisions':[{'invitation_kind':'none','finding_id':f'F00{i}','statement_class':'business_fact','speech_act':'assertion','assertion_subject':'business','source_relation':'unrelated',
                'verdict':'unsupported','source_id':'none','source_quote_id':'none',
                'reason':'Нет подтверждения в разрешённых источниках.'} for i in (1,2)]}
        result = adjudicate_factual_findings('Проверь проект.', findings, ask=ask)
        # An omitted item blocks only itself; the valid sibling stays adjudicated.
        self.assertEqual(len(calls), 1)
        outcomes={x['quote']:x['adjudication'] for x in result['blocking']}
        self.assertEqual(outcomes,{'Факт A.':'unsupported','Факт B.':'unavailable'})

    def test_live_batch_isolates_incomplete_inventory_and_keeps_valid_decision(self):
        import io
        request='Создай план для проекта.'
        findings=[{'document':'calendar','type':'invented_fact','quote':'Факт A.',
                   'reason':'Проверь подтверждённость утверждения A.'},
                  {'document':'calendar','type':'invented_fact','quote':'Факт B.',
                   'reason':'Проверь подтверждённость утверждения B.'}]
        sent=[]
        def fake_urlopen(req, timeout):
            payload=json.loads(req.data.decode('utf-8'));sent.append(payload)
            items={'F001':{'invitation_kind':'none','statement_class':'business_fact','speech_act':'assertion','assertion_subject':'business','source_relation':'unrelated','verdict':'unsupported',
                    'source_quote_id':'none','reason':'Нет подтверждения в разрешённых источниках.'}}
            return io.BytesIO(json.dumps({'message':{'content':json.dumps({'decisions':items},ensure_ascii=False)}},
                                         ensure_ascii=False).encode('utf-8'))
        with patch('claim_support.urllib.request.urlopen',side_effect=fake_urlopen):
            result=adjudicate_factual_findings(request,findings)
        self.assertEqual(len(sent),2)
        self.assertEqual(sent[1]['format']['properties']['decisions']['required'],['F002'])
        self.assertEqual(len(result['blocking']),2)
        outcomes={x['quote']:x['adjudication'] for x in result['blocking']}
        self.assertEqual(outcomes,{'Факт A.':'unsupported','Факт B.':'unavailable'})

    def test_live_schema_scopes_each_quote_enum_to_its_finding(self):
        import io
        findings=[{'document':'calendar','type':'invented_fact','quote':'Факт A.',
                   'reason':'Проверь подтверждённость утверждения A.'},
                  {'document':'calendar','type':'invented_fact','quote':'Факт B.',
                   'reason':'Проверь подтверждённость утверждения B.'}]
        seen=[]
        def fake_urlopen(req,timeout):
            payload=json.loads(req.data.decode('utf-8'));seen.append(payload)
            properties=payload['format']['properties']['decisions']['properties']
            self.assertEqual(set(properties),{'F001','F002'})
            ids_a=properties['F001']['properties']['source_quote_id']['enum']
            ids_b=properties['F002']['properties']['source_quote_id']['enum']
            self.assertTrue(all(x=='none' or x.startswith('F001_') for x in ids_a))
            self.assertTrue(all(x=='none' or x.startswith('F002_') for x in ids_b))
            answer={'decisions':{
                'F001':{'invitation_kind':'none','statement_class':'business_fact','speech_act':'assertion','assertion_subject':'business','source_relation':'unrelated','verdict':'unsupported',
                        'source_quote_id':'none','reason':'Нет подтверждения в разрешённых источниках.'},
                'F002':{'invitation_kind':'none','statement_class':'business_fact','speech_act':'assertion','assertion_subject':'business','source_relation':'unrelated','verdict':'unsupported',
                        'source_quote_id':'none','reason':'Нет подтверждения в разрешённых источниках.'}}}
            return io.BytesIO(json.dumps({'message':{'content':json.dumps(answer,ensure_ascii=False)}},
                                         ensure_ascii=False).encode('utf-8'))
        with patch('claim_support.urllib.request.urlopen',side_effect=fake_urlopen):
            result=adjudicate_factual_findings('Проверь проект.',findings)
        self.assertEqual(len(seen),1)
        self.assertEqual({x['adjudication'] for x in result['blocking']},{'unsupported'})

    def test_contradictory_uncertain_verdict_gets_one_higher_budget_retry(self):
        import io
        request='Мастерская выполняет диагностику велосипедов.'
        issue={'document':'strategy','type':'invented_fact',
               'quote':'В мастерской проводится велосипедная диагностика.',
               'reason':'Проверь, подтверждена ли услуга.'}
        calls=[]
        def fake_urlopen(req,timeout):
            payload=json.loads(req.data.decode('utf-8'));calls.append(payload)
            user=json.loads(payload['messages'][1]['content'])
            finding=user['findings'][0]
            quote_id=next(x['quote_id'] for x in user['authorized_quote_options']
                          if x['quote']==request)
            decision={'invitation_kind':'none','statement_class':'business_fact','speech_act':'assertion','assertion_subject':'business','source_relation':'entails',
                'verdict':'uncertain' if len(calls)==1 else 'supported',
                'source_quote_id':quote_id,
                'reason':'Прямо подтверждено исходным запросом.'}
            body={'message':{'content':json.dumps({'decisions':{
                finding['finding_id']:decision}},ensure_ascii=False)}}
            return io.BytesIO(json.dumps(body,ensure_ascii=False).encode('utf-8'))
        with patch('claim_support.urllib.request.urlopen',side_effect=fake_urlopen):
            result=adjudicate_factual_findings(request,[issue])
        self.assertEqual(len(calls),2)
        self.assertEqual([x['options']['num_predict'] for x in calls],[1100,2400])
        self.assertEqual(result['blocking'],[])
        self.assertEqual(len(result['supported']),1)

    def test_negated_policy_is_host_classified_without_source_adjudication(self):
        request='Не обещай гарантированный результат.'
        issue={'document':'strategy','type':'invented_fact',
               'quote':'Не обещай гарантированный результат.',
               'reason':'Проверь тип этого высказывания.'}
        with patch('claim_support.urllib.request.urlopen') as urlopen:
            result=adjudicate_factual_findings(request,[issue])
        urlopen.assert_not_called()
        self.assertEqual(len(result['non_business']),1)
        self.assertEqual(result['blocking'],[])
        self.assertEqual(result['non_business'][0]['statement_class'],'planning_constraint')
        self.assertEqual(result['non_business'][0]['adjudication'],'not_business_claim')

    def test_non_business_citation_must_still_be_a_valid_source_quote_pair(self):
        request='Студия проводит занятия по ручной лепке.'
        context=build_evidence_context(request).to_dict()
        issue={'document':'strategy','type':'invented_fact','quote':'Редакционное ограничение для будущего текста.',
               'reason':'Проверь классификацию высказывания.'}
        def ask(model,system,user):
            payload=json.loads(user)
            option=payload['authorized_quote_options'][0]
            return {'decisions':[{'finding_id':'F001','statement_class':'planning_constraint',
                    'verdict':'supported','source_id':'none','source_quote_id':option['quote_id'],
                    'reason':'Это только инструкция к будущему материалу.'}]}
        result=adjudicate_factual_findings(request,[issue],evidence_context=context,ask=ask)
        self.assertEqual(result['supported'],[])
        self.assertEqual(result['non_business'],[])
        self.assertEqual(result['blocking'][0]['adjudication'],'unavailable')
        self.assertIn('legacy source_id не соответствует',result['blocking'][0]['reason'])

    def test_model_cannot_dismiss_finding_with_nonexistent_source_quote(self):
        request = 'Студия проводит занятия по ручной лепке.'
        context = build_evidence_context(request).to_dict()
        source = context['sources'][0]
        issue = {'document': 'calendar', 'type': 'invented_fact',
                 'quote': 'Занятие начинается с обжига.',
                 'reason': 'Не подтверждён этап занятия.'}

        def ask(*args):
            return {'verdict': 'supported', 'source_id': source['source_id'],
                    'source_quote': 'обжиг выполняется в начале занятия',
                    'reason': 'Кажется, это похоже на мастерскую.'}

        result = adjudicate_factual_findings(request, [issue], evidence_context=context, ask=ask)
        self.assertEqual(result['supported'], [])
        self.assertEqual(result['blocking'][0]['adjudication'], 'unavailable')
        self.assertIn('Сверка источника не завершена', result['blocking'][0]['reason'])

    def test_adjudicator_failure_keeps_finding_blocking(self):
        issue = {'document': 'strategy', 'type': 'unsafe_claim',
                 'quote': 'Гарантированный результат.', 'reason': 'Обещание без основания.'}
        result = adjudicate_factual_findings('Подготовь стратегию.', [issue], ask=lambda *args: (_ for _ in ()).throw(TimeoutError('offline')))
        self.assertEqual(result['supported'], [])
        self.assertEqual(result['blocking'][0]['adjudication'], 'unavailable')
        self.assertIn('offline', result['blocking'][0]['reason'])

    def test_oversized_authority_set_blocks_without_silent_source_truncation(self):
        request = 'Запрос.'
        context = build_evidence_context(request, {
            'verified_project_materials': [
                {'verified': True, 'title': 'Источник A', 'content': 'А' * 12000},
                {'verified': True, 'title': 'Источник B', 'content': 'Б' * 12000},
            ]}).to_dict()
        issue = {'document': 'calendar', 'type': 'invented_fact',
                 'quote': 'Утверждение.', 'reason': 'Факт не подтверждён.'}
        called = []
        result = adjudicate_factual_findings(request, [issue], evidence_context=context,
                                             ask=lambda *args: called.append(args))
        self.assertEqual(called, [])
        self.assertEqual(result['blocking'][0]['adjudication'], 'unavailable')
        self.assertIn('превышает бюджет', result['blocking'][0]['reason'])

    def test_unsupported_claim_remains_blocking_even_with_valid_schema(self):
        request = 'Клуб проводит разговорные занятия.'
        issue = {'document': 'calendar', 'type': 'invented_fact',
                 'quote': 'Каждый урок длится два часа.', 'reason': 'Указана длительность.'}
        result = adjudicate_factual_findings(request, [issue], ask=lambda *args: {
            'verdict': 'unsupported', 'source_id': 'none', 'source_quote': '',
            'reason': 'В источнике нет длительности занятий.'})
        self.assertEqual(result['supported'], [])
        self.assertEqual(result['blocking'][0]['adjudication'], 'unsupported')

    def test_independent_reviewer_findings_use_one_source_adjudication_path(self):
        request='Мастерская «Веломир» выполняет диагностику велосипедов и принимает клиентов по записи.'
        strategy='Мы проводим диагностику велосипедов.'
        context=build_evidence_context(request).to_dict()
        def supported(req, findings, **kwargs):
            self.assertTrue(any('диагностику' in x.get('quote','').casefold() for x in findings),findings)
            item=findings[0]
            return {'supported':[ {**item, 'support':{'source_id':context['sources'][0]['source_id'],
                    'quote':'выполняет диагностику велосипедов',
                    'reason':'Услуга явно указана в запросе.'}} ], 'blocking':[]}
        issue={'document':'strategy','type':'invented_fact','quote':'Мы проводим диагностику велосипедов.',
               'reason':'Метод работы должен быть подтверждён.'}
        with patch('independent_acceptance_editor._review_windows',
                   return_value=[('Стратегия', 'Календарь')]), \
             patch('independent_acceptance_editor._call_editor_once',
                   return_value={'pass':False,'issues':[issue]}):
            result=call_editor(request,strategy,'Календарь',evidence_context=context,
                               claim_adjudicator=supported)
        self.assertTrue(result['pass'],result)
        self.assertEqual(result['issues'],[])

    def test_unresolved_independent_claim_stays_blocking(self):
        request='Студия проводит занятия по ручной лепке для взрослых новичков.'
        strategy='Мы используем профессиональную печь для обжига.'
        context=build_evidence_context(request).to_dict()
        def unsupported(req, findings, **kwargs):
            self.assertTrue(findings)
            return {'supported':[], 'blocking':[{**findings[0], 'adjudication':'unsupported'}]}
        issue={'document':'strategy','type':'invented_fact','quote':'профессиональную печь для обжига',
               'reason':'Оборудование не указано в проектных источниках.'}
        with patch('independent_acceptance_editor._review_windows',
                   return_value=[('Стратегия', 'Календарь')]), \
             patch('independent_acceptance_editor._call_editor_once',
                   return_value={'pass':False,'issues':[issue]}):
            result=call_editor(request,strategy,'Календарь',evidence_context=context,
                               claim_adjudicator=unsupported)
        self.assertFalse(result['pass'],result)
        self.assertEqual(result['issues'][0]['adjudication'],'unsupported')

    def test_final_editor_routes_factual_disputes_through_shared_resolver(self):
        request = 'Проект «Языковой клуб»: разговорные занятия английским для взрослых новичков.'
        quote = 'Языковой клуб проводит разговорные занятия английским.'
        issue = {'document': 'calendar', 'type': 'invented_fact', 'quote': quote,
                 'reason': 'Не подтверждено существование клуба и занятий.'}
        resolution = {'supported': [{**issue, 'support': {'source_id': 'user_request:test',
                      'quote': 'Языковой клуб: разговорные занятия английским',
                      'reason': 'Это задано в исходном запросе.'}}], 'blocking': []}
        with patch('independent_acceptance_editor._review_windows',
                   return_value=[('Стратегия', 'Календарь')]), \
             patch('independent_acceptance_editor._call_editor_once',
                   return_value={'pass': False, 'issues': [issue]}), \
             patch('factual_claim_audit.scan_unsupported_business_claims', return_value=[]), \
             patch('claim_support.adjudicate_factual_findings', return_value=resolution) as resolver:
            result = call_editor(request, 'Стратегия', 'Календарь',
                                 evidence_context=build_evidence_context(request).to_dict())
        resolver.assert_called_once()
        self.assertTrue(result['pass'], result)
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['disproved_absence_claims'][0]['support']['quote'],
                         resolution['supported'][0]['support']['quote'])


if __name__ == '__main__':
    unittest.main()
