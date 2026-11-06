import unittest
from tempfile import TemporaryDirectory

from document_recovery import DocumentDraft
from editorial_evidence_matrix import disproved_user_stated_fact
from independent_acceptance_editor import (
    _nonblocking_editor_false_positive, _nonblocking_scope_issue, call_strategy_editor,
    IndependentReviewUnavailable)
from unittest.mock import patch
from strategy_integrity import normalize_explicit_cadence
from publication_writer import bind_post_to_slot
from strategy_recovery import produce_checked_strategy
from factual_claim_audit import scan_unsupported_business_claims
from strategy_integrity import strategy_integrity_issues


class EvidenceScopeTests(unittest.TestCase):
    def test_audience_desire_cannot_ground_business_result(self):
        request=('Проект «Ателье Тест»: ремонтирует и подгоняет взрослую одежду по фигуре. '
                 'Аудитория: взрослые, желающие продлить жизнь одежде.')
        issue={'type':'invented_fact','document':'calendar',
               'quote':'Ателье Тест помогает продлить жизнь вашим вещам, подгоняя их по фигуре и ремонтируя.',
               'reason':'Фраза «Ателье Тест помогает продлить жизнь вашим вещам, подгоняя их по фигуре и ремонтируя» '
                        'содержит неподтверждённое утверждение о результате.'}
        self.assertFalse(disproved_user_stated_fact(issue,request))

    def test_explicit_services_in_one_fact_clause_remain_supported(self):
        request='Ателье ремонтирует и подгоняет одежду по фигуре.'
        issue={'type':'invented_fact','document':'calendar',
               'quote':'Мы ремонтируем и подгоняем одежду по фигуре.',
               'reason':'Фраза «Мы ремонтируем и подгоняем одежду по фигуре» не подтверждена запросом.'}
        self.assertTrue(disproved_user_stated_fact(issue,request))

    def test_knowledge_boundary_note_is_not_a_business_absence_claim(self):
        issue={'type':'invented_fact','document':'strategy',
               'quote':'Процесс занятия не подтверждён исходным запросом',
               'reason':'Эта фраза является неподтверждённым утверждением.'}
        self.assertTrue(_nonblocking_scope_issue(issue, 'Цель: объяснить процесс занятия.', ''))

    def test_strategy_only_gate_accepts_source_knowledge_boundary_note(self):
        issue={'type':'invented_fact','document':'strategy',
               'quote':'Процесс занятия не подтверждён исходным запросом',
               'reason':'Фраза о процессе названа неподтверждённым фактом.'}
        with patch('independent_acceptance_editor._call_editor_once',
                   return_value={'pass':False,'issues':[issue]}):
            result=call_strategy_editor('Цель — объяснить процесс занятия.', '# Стратегия')
        self.assertTrue(result['pass'],result)
        self.assertEqual(result['issues'],[])

    def test_strategy_only_gate_keeps_unconfirmed_business_offer_blocking(self):
        issue={'type':'invented_fact','document':'strategy',
               'quote':'В студии есть собственная печь',
               'reason':'Наличие оборудования не подтверждено.'}
        with patch('independent_acceptance_editor._call_editor_once',
                   return_value={'pass':False,'issues':[issue]}):
            with self.assertRaises(IndependentReviewUnavailable):
                call_strategy_editor(
                    'Подготовь стратегию для студии.', '# Стратегия',
                    claim_adjudicator=lambda request, findings, **kwargs: {
                        'supported': [], 'blocking': [{**findings[0], 'adjudication': 'unavailable'}]})


    def test_semantic_business_claims_are_owned_by_independent_adjudication(self):
        request=('Для мастерской «Веломир»: делает диагностику, замену тормозных колодок '
                 'и настройку переключателей. Не придумывай услуги.')
        strategy=('В «Веломир» мы проводим диагностику, замену тормозных колодок '
                  'и настройку переключателей.')
        # A sentence whose specific service terms are all carried by one
        # affirmative brief sentence is grounded. Semantic paraphrases that
        # cannot be grounded lexically remain for the independent adjudicator.
        self.assertFalse(strategy_integrity_issues(strategy,request,per_week=2))
        self.assertEqual(scan_unsupported_business_claims(
            request,strategy,'',include_semantic_facts=False),[])
        self.assertEqual(scan_unsupported_business_claims(
            request,strategy,'',include_semantic_facts=True),[])

    def test_unknown_process_details_remain_candidates_for_source_adjudication(self):
        request='Студия проводит занятия по ручной лепке для взрослых новичков. Не выдумывай этапы.'
        doc='На занятии вы покрываете изделие глазурью, а затем обжигаете его.'
        # The early deterministic gate must not infer missing steps from the
        # industry. The independent semantic reviewer owns this question.
        self.assertEqual(scan_unsupported_business_claims(
            request,'',doc,include_semantic_facts=False),[])
        issue={'type':'invented_fact','document':'strategy','quote':doc,
               'reason':'Этапы занятия не указаны в источнике.'}
        with patch('independent_acceptance_editor._call_editor_once',
                   return_value={'pass':False,'issues':[issue]}):
            result=call_strategy_editor(request,doc,claim_adjudicator=lambda req, findings, **kwargs: {
                'supported': [], 'blocking': [{**findings[0], 'adjudication': 'unsupported'}]})
        self.assertFalse(result['pass'])
        self.assertEqual(result['issues'][0]['adjudication'],'unsupported')

    def test_visual_storyboard_is_a_proposal_not_a_claim_of_existing_assets(self):
        visual='Предлагаемый сценарий: кадр с руками за работой с глиной; титр «Этап занятия».'
        calendar=('| Неделя/день | Текст | Визуал | CTA | KPI |\n'
                  '|---|---|---|---|---|\n'
                  '| 1/Вт | Достаточно длинный текст о теме публикации. | '+visual+' | Задайте вопрос | Ответы |')
        issue={'type':'invented_fact','document':'calendar','quote':visual,
               'reason':'Визуальный сценарий содержит вымышленные сцены, не подтверждённые как существующие.'}
        self.assertTrue(_nonblocking_scope_issue(issue, 'Расскажите о процессе занятия.', calendar))

    def test_business_assertion_in_visual_remains_blocking(self):
        visual='В нашей студии есть собственная печь; показать её в кадре.'
        calendar=('| Неделя/день | Текст | Визуал | CTA | KPI |\n'
                  '|---|---|---|---|---|\n'
                  '| 1/Вт | Текст публикации с достаточной длиной. | '+visual+' | Задайте вопрос | Ответы |')
        issue={'type':'invented_fact','document':'calendar','quote':visual,
               'reason':'Визуальный сценарий утверждает наличие оборудования.'}
        self.assertFalse(_nonblocking_scope_issue(issue, 'Расскажите о занятиях.', calendar))

    def test_neutral_reader_questions_are_not_business_claims(self):
        request='Получить вопросы о записи. Материалы пока не уточнены.'
        for quote in ('Одежда не сидит как нужно?', 'Какие материалы используются?'):
            issue={'type':'invented_fact','document':'calendar','quote':quote,
                   'reason':f'Фраза «{quote}» содержит неподтверждённый факт.'}
            self.assertTrue(_nonblocking_editor_false_positive(issue,request),quote)


class PlanIdentityTests(unittest.TestCase):
    def test_model_cannot_reassign_a_post_to_another_slot_or_topic(self):
        slot={'week':1,'day':'Вт','topic':'Подготовка к занятию'}
        post={'id':'2/Пт','topic':'Скидки этой недели','caption':'Содержание остается модельным.'}
        bound, fields=bind_post_to_slot(post,slot)
        self.assertEqual(bound['id'],'1/Вт')
        self.assertEqual(bound['topic'],'Подготовка к занятию')
        self.assertEqual(fields,('id','topic'))
        self.assertEqual(post['topic'],'Скидки этой недели')


class ExplicitCadenceTests(unittest.TestCase):
    def test_affirmative_wrong_cadence_is_reconciled_from_explicit_user_value(self):
        source='Частота: 1 публикация в неделю. Публикуем по 1 публикации в неделю.'
        output,edits=normalize_explicit_cadence(source,2)
        self.assertEqual(output,'Частота: 2 публикации в неделю. Публикуем по 2 публикации в неделю.')
        self.assertEqual(len(edits),2)

    def test_historical_or_negative_cadence_is_left_for_validation(self):
        source='Раньше публиковали по 1 публикации в неделю; теперь по 2 публикации в неделю.'
        output,edits=normalize_explicit_cadence(source,2)
        self.assertEqual(output,source)
        self.assertEqual(edits,[])


class PersistedDraftRepairTests(unittest.TestCase):
    def test_each_retry_targets_latest_checkpoint_and_never_calls_generator(self):
        with TemporaryDirectory() as root:
            store=DocumentDraft(root,'sig','strategy')
            store.save('Draft v0',False,'issue 0')
            repair_inputs=[]
            outputs=iter(['Draft v1','Draft v2','Draft v3'])
            def generate(*args):
                self.fail('A saved rejected draft must never fall back to generation')
            def repair(body,reason,attempt):
                repair_inputs.append((body,reason,attempt))
                return next(outputs)
            validation_results=iter([(False,'issue 1'),(False,'issue 2'),(True,'')])
            result=produce_checked_strategy('brief',2,generate,
                lambda request,text:next(validation_results),draft=store.load(),
                repair=repair,checkpoint=store.save,attempts=3)
            self.assertTrue(result[1],result)
            self.assertEqual(result[0],'Draft v3')
            self.assertEqual([x[0] for x in repair_inputs],['Draft v0','Draft v1','Draft v2'])
            self.assertEqual(store.load()['content'],'Draft v3')

    def test_noop_repair_keeps_persisted_rejection_and_never_generates(self):
        with TemporaryDirectory() as root:
            store=DocumentDraft(root,'sig','strategy')
            store.save('Rejected draft with unsupported process',False,'unsupported process')
            calls=[]
            def generate(*args):self.fail('Persisted rejected draft must not be replaced by generation')
            result=produce_checked_strategy('brief',2,generate,
                lambda request,text:(False,'still blocked'),
                draft=store.load(),repair=lambda *args:'Rejected draft with unsupported process',
                checkpoint=store.save,attempts=3)
            self.assertFalse(result[1])
            self.assertEqual(result[0],'Rejected draft with unsupported process')
            saved=store.load()
            self.assertEqual(saved['content'],result[0])
            self.assertEqual(saved['history'][0]['content'],'Rejected draft with unsupported process')
            self.assertIn('still blocked',saved['reason'])


if __name__=='__main__':
    unittest.main()
