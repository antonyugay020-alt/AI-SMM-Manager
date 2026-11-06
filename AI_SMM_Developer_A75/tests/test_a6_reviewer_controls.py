import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from editorial_evidence_matrix import (
    disproved_user_stated_fact,
    disproved_calendar_format_strategy_claim,
)
from factual_claim_audit import scan_unsupported_business_claims
from independent_acceptance_editor import review_saved_results


class ReviewerControlsA6(unittest.TestCase):
    def test_strategy_repeating_absence_of_free_service_is_not_a_offer(self):
        request=('Не выдумывай скидки, бесплатные занятия и отзывы. '
                 'Нет подтверждённых сведений о ценах.')
        strategy=('Нет информации о скидках, бесплатных занятиях и отзывах. '
                  'Эти условия неизвестны и не должны предлагаться.')
        self.assertEqual(scan_unsupported_business_claims(request,strategy,''),[])

    def test_positive_unconfirmed_free_offer_is_still_blocked(self):
        request='Не выдумывай бесплатные занятия и цены.'
        self.assertTrue(scan_unsupported_business_claims(
            request,'Студия предлагает бесплатные занятия для новых учеников.',''))

    def test_explicitly_stated_process_or_service_disproves_unsupported_finding(self):
        request=('Ателье ремонтирует и подгоняет одежду; принимает вещи по договорённости. '
                 'Стоимость и адрес неизвестны.')
        issue={'document':'calendar','type':'invented_fact',
               'quote':'Мы принимаем вещи по договорённости.',
               'reason':'Фраза «мы принимаем вещи по договорённости» не подтверждена запросом.'}
        self.assertTrue(disproved_user_stated_fact(issue,request))
        invented={'document':'calendar','type':'invented_fact',
                  'quote':'Бесплатная подгонка за один день.',
                  'reason':'Фраза «бесплатная подгонка за один день» не подтверждена запросом.'}
        self.assertFalse(disproved_user_stated_fact(invented,request))

    def test_calendar_can_set_format_and_overlay_without_strategy_duplicate(self):
        calendar=('| Неделя/день | Площадка и формат | Тема | Цель | Сценарий/визуал | Текст | CTA | KPI |\n'
          '|---|---|---|---|---|---|---|---|\n'
          '| 1/Вт | Instagram, Карусель | Тема поста | Объяснить | 3 слайда с экранными титрами и CTA | '
          'Текст поста полностью приведён здесь. | Напишите вопрос | Ответы; способ — подсчёт |')
        issue={'document':'calendar','type':'invented_fact','quote':calendar.splitlines()[-1],
               'reason':'В стратегии не указано, что текст будет использоваться как титры на слайдах.'}
        self.assertTrue(disproved_calendar_format_strategy_claim(issue,calendar))

    def test_operational_control_detects_claim_despite_wrong_reviewer_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            for case in ('workshop','school'):
                data={'request':'Запрос','response':{'documents':[
                    {'kind':'strategy','content':'Стратегия'},
                    {'kind':'calendar','content':'Календарь'}]}}
                (Path(tmp)/(case+'_synthetic_documents.json')).write_text(
                    json.dumps(data),encoding='utf-8')
            def reviewer(request,strategy,calendar,**kwargs):
                if 'снимаем мерки' in calendar.casefold():
                    return {'pass':False,'issues':[{'document':'calendar',
                        'type':'missing_required_content',
                        'quote':'В нашей мастерской снимаем мерки и вручную подгоняем каждое платье.',
                        'reason':'Отсутствует информация, которую пользователь включил в требования.'}]}
                if 'снимаем мерки' in strategy.casefold():
                    return {'pass':False,'issues':[{'document':'strategy','type':'invented_fact',
                        'quote':'всем клиентам гарантируем результат за 1 день, адрес ул. Неизвестная 999, цена 50000 рублей.',
                        'reason':'Не подтверждены точные факты.'}]}
                return {'pass':True,'issues':[]}
            def deterministic_adjudicator(request, findings, **kwargs):
                return {'supported': [], 'non_business': [], 'blocking': [
                    {**item, 'adjudication': 'unsupported',
                     'reason': 'Процессы не указаны в исходном запросе.'}
                    for item in findings]}
            with patch('claim_support.adjudicate_factual_findings',
                       side_effect=deterministic_adjudicator):
                result=review_saved_results(Path(tmp), editor_call=reviewer,
                                             claim_adjudicator=deterministic_adjudicator)
        self.assertEqual(result['operational_claim_control']['status'],'PASS')
        self.assertTrue(result['operational_claim_control']['detected'])
        self.assertTrue(any(issue.get('type') == 'missing_required_content'
                            for issue in result['operational_claim_control']['issues']))
        self.assertTrue(any(issue.get('type') == 'invented_fact'
                            and issue.get('adjudication') == 'unsupported'
                            for issue in result['operational_claim_control']['issues']))
        self.assertEqual(result['status'],'FAIL')


if __name__=='__main__':
    unittest.main()
