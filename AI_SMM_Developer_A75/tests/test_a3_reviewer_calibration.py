import unittest
from editorial_evidence_matrix import structural_digest
from factual_claim_audit import scan_unsupported_business_claims
from strategy_integrity import strategy_integrity_issues

BRIEF=('Подготовь стратегию и контент-план. Не придумывай цены, бесплатные занятия, '
       'сроки и результаты. Бизнес: мастерская одежды.')

class ReviewerCalibrationTests(unittest.TestCase):
    def test_field_index_reports_presence_never_invents_length_thresholds(self):
        body='''| Неделя/день | Площадка | Тема | Цель | Визуальный сценарий | Текст | CTA | KPI |
|---|---|---|---|---|---|---|---|
| 1/Пн | Telegram | Обзор | Информировать | Съёмка процесса | Подробный текст | Напишите | Охват и ответы |
'''
        index=structural_digest(body)
        self.assertIn('KPI=заполнено',index)
        self.assertNotIn('символ',index)
        self.assertNotIn('249',index)

    def test_explicit_ban_in_strategy_is_not_a_free_service_offer(self):
        strategy='- Не допускается упоминание бесплатных занятий, скидок и отзывов.'
        self.assertEqual(scan_unsupported_business_claims(BRIEF,strategy,''),[])
        positive='Предлагаем бесплатные занятия для всех клиентов.'
        self.assertTrue(scan_unsupported_business_claims(BRIEF,positive,''))

    def test_target_disclosed_as_unconfirmed_requires_measurement(self):
        request='Составь стратегию без выдуманных показателей.'
        qualified='Ориентир: увеличение лайков на 10% (необходимо уточнить исторические данные для установления реалистичного ориентира).'
        issues=strategy_integrity_issues(qualified,request,per_week=3)
        self.assertEqual(issues,[])
        unqualified='Ориентир: увеличение лайков на 10% за неделю.'
        self.assertTrue(strategy_integrity_issues(unqualified,request,per_week=3))

if __name__=='__main__':unittest.main()
