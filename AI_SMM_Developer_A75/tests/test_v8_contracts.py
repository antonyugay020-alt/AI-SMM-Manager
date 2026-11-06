# -*- coding: utf-8 -*-
"""Regressions derived from actual V7 Windows results (redacted synthetic fixtures).

No model calls; the real V8 acceptance re-executes these contracts with Ollama.
"""
import json
import unittest
from unittest.mock import patch

from editorial_evidence_matrix import (publication_rows, structural_digest,
    disproved_missing_field_claim, disproved_document_role_claim)
from factual_claim_audit import scan_unsupported_business_claims
from independent_acceptance_editor import call_editor
from strategy_integrity import strategy_integrity_issues
from editorial_guard import editorial_risks

REQUEST_A = ('Для клуба взрослых разработай стратегию и календарь. Не выдумывай '
             'адрес, цены, гарантированный результат, скидки, стоимость консультации.')
REQUEST_B = ('Для мастерской напиши 2 поста с полным уникальным текстом и '
             'визуальным сценарием каждой публикации. Нельзя придумывать цены и адреса.')
HEAD='| Неделя/день | Площадка и формат | Сценарий/визуал | Текст/ключевые реплики | CTA | KPI |\n|---|---|---|---|---|---|'
FIRST='Полезный текст о проверке велосипеда с конкретными действиями. '*6
SECOND='Текст о работе с переключателями с практическими пояснениями. '*6
ROW1='| 1/Вт | Instagram, Reels | '+('Сцена и титры с наглядными действиями; '*4)+' | '+FIRST+' | Запишитесь на диагностику | Обращения: подсчитать сообщения |'
ROW2='| 1/Пт | Instagram, Reels | '+('Сценарий демонстрации процедуры ремонта; '*4)+' | '+SECOND+' | Задайте вопрос в Direct | Охват: статистика публикаций |'
CAL=HEAD+'\n'+ROW1+'\n'+ROW2

class V8IndependentContractTests(unittest.TestCase):
    def test_v7_negative_control_with_ne_vydumyvay_is_detected(self):
        bad='Гарантируем результат за 1 день, ул. Неизвестная 999, цена 50000 рублей.'
        findings=scan_unsupported_business_claims(REQUEST_A,'Стратегия. '+bad,CAL)
        self.assertGreaterEqual(len(findings),3)
        self.assertTrue(all(item['quote'] in ('Стратегия. '+bad) for item in findings))

    def test_nelzya_and_ne_vydumyvay_both_have_same_policy(self):
        restrictions=('Нельзя придумывать цены, адреса, гарантии.',
                      'Не выдумывай цены, адреса, гарантии.')
        claim='Стоимость 50000 рублей, адрес ул. Неизвестная 999; гарантируем результат за 1 день.'
        for request in restrictions:
            with self.subTest(request=request):
                self.assertGreaterEqual(len(scan_unsupported_business_claims(request,claim,'')),3)

    def test_fictional_scenario_is_not_automatically_a_business_fact(self):
        self.assertEqual(scan_unsupported_business_claims(REQUEST_A,
            'Сценарий съёмки: мастер показывает коробку, актёр задаёт вопрос.', ''), [])

    def test_unconfirmed_free_consultation_rejected(self):
        claims=scan_unsupported_business_claims(REQUEST_A,
             'Запишитесь на бесплатную консультацию.', '')
        self.assertTrue(any('бесплатн' in item['quote'] for item in claims))

    def test_free_consultation_explicitly_authorized(self):
        request=REQUEST_A+' БЕСПЛАТНУЮ консультацию можно рекламировать; это подтверждённая услуга.'
        self.assertEqual(scan_unsupported_business_claims(request,
            'Запишитесь на бесплатную консультацию.', ''),[])

    def test_strategy_recovery_sees_unsourced_free_offer(self):
        issues=strategy_integrity_issues('Запишитесь на бесплатную консультацию.',REQUEST_A,per_week=2)
        self.assertTrue(issues)

    def test_publication_reviewer_sees_unsourced_free_offer(self):
        post={'caption':'Предлагаем бесплатную консультацию.', 'visual':'В кадре консультация',
              'cta':'Запишитесь на бесплатную консультацию'}
        self.assertTrue(editorial_risks(post,REQUEST_A))

    def test_disproved_missing_caption_claim_requires_caption_and_uniqueness(self):
        issue={'document':'calendar','type':'missing_required_content',
               'quote':ROW1[:250], 'reason':'Отсутствует уникальный полный текст для публикации'}
        self.assertTrue(disproved_missing_field_claim(issue,CAL))
        self.assertFalse(disproved_missing_field_claim(issue,CAL.replace(FIRST,SECOND)))
        self.assertFalse(disproved_missing_field_claim(issue,CAL.replace(FIRST,'Кратко.')))

    def test_disproved_missing_cta_when_explicit_cta_column_has_value(self):
        issue={'document':'calendar','type':'missing_required_content',
               'quote':ROW1[:200],'reason':'В строке отсутствует описание CTA в отдельном поле'}
        self.assertTrue(disproved_missing_field_claim(issue,CAL))
        self.assertFalse(disproved_missing_field_claim(issue,CAL.replace('Запишитесь на диагностику','')))

    def test_general_quality_or_safety_issue_never_suppressed(self):
        for kind in ('quality','invented_fact','unsafe_claim'):
            issue={'document':'calendar','type':kind,'quote':ROW1[:200],
                   'reason':'Отсутствует уникальный полный текст'}
            self.assertFalse(disproved_missing_field_claim(issue,CAL))

    def test_role_aware_plan_post_evidence_in_calendar(self):
        issue={'document':'strategy','type':'missing_required_content',
               'reason':'Отсутствует визуальный сценарий для публикации 2 в стратегии'}
        self.assertTrue(disproved_document_role_claim(issue,REQUEST_B,CAL))
        self.assertFalse(disproved_document_role_claim(issue,REQUEST_B,CAL.replace('Сценарий демонстрации процедуры ремонта; '*4,'')))
        self.assertFalse(disproved_document_role_claim(issue,REQUEST_B+' В стратегии для каждой публикации дай визуал.',CAL))

    def test_presence_digest_matches_original_table(self):
        rows=publication_rows(CAL)
        self.assertEqual(len(rows),2)
        digest=structural_digest(CAL)
        self.assertIn('1/Вт',digest)
        self.assertIn('текст=',digest)
        self.assertIn('CTA=',digest)
        self.assertIn('1/Пт',digest)

    def test_false_editor_absence_claim_does_not_block_genuine_documents(self):
        issue={'document':'calendar','type':'missing_required_content',
               'quote':ROW1[:250], 'reason':'Отсутствует уникальный полный текст'}
        with patch('independent_acceptance_editor._call_editor_once',
                   return_value={'pass':False,'issues':[issue]}):
            out=call_editor(REQUEST_B,'Стратегия высокого уровня',CAL)
        self.assertTrue(out['pass'])
        self.assertEqual(out['issues'],[])
        self.assertEqual(len(out['disproved_absence_claims']),1)

    def test_valid_editorial_quality_rejection_still_blocks(self):
        issue={'document':'calendar','type':'quality',
               'quote':ROW1[:250], 'reason':'Недостаточно практических деталей'}
        with patch('independent_acceptance_editor._call_editor_once',
                   return_value={'pass':False,'issues':[issue]}):
            out=call_editor(REQUEST_B,'Стратегия',CAL)
        self.assertFalse(out['pass'])
        self.assertEqual(len(out['issues']),1)

    def test_model_pass_cannot_override_price_or_free_offer(self):
        with patch('independent_acceptance_editor._call_editor_once',
                   return_value={'pass':True,'issues':[]}):
            out=call_editor(REQUEST_A,
                'Гарантируем результат за 1 день. Ул. Неизвестная 999. '
                'Запишитесь на бесплатную консультацию: цена 50000 рублей.',CAL)
        self.assertFalse(out['pass'])
        self.assertGreaterEqual(len(out['issues']),3)

if __name__=='__main__':
    unittest.main()
