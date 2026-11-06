import json
import unittest
from editorial_evidence_matrix import disproved_unrequested_contact_link
from document_recovery import patch_document
from factual_claim_audit import scan_unsupported_business_claims
from independent_acceptance_editor import call_editor

class FollowupA4Tests(unittest.TestCase):
    def test_missing_link_is_not_required_by_consultation_objective(self):
        request='Цель: получить запросы на консультацию. Стоимость и адрес неизвестны.'
        issue={'document':'calendar','type':'missing_required_content',
               'quote':'Запишитесь на консультацию','reason':'Отсутствует ссылка в биографии или посте для записи.'}
        self.assertTrue(disproved_unrequested_contact_link(issue,request))
        self.assertFalse(disproved_unrequested_contact_link(issue,request+' Добавь ссылку на форму в каждый пост.'))

    def test_quote_from_failure_targets_the_exact_line_for_repair(self):
        source='\n'.join(['# Заголовок','Отклонённая строка: бесплатную консультацию','Другой текст'])
        def ask(model,system,user,**kwargs):
            data=json.loads(user)
            self.assertEqual(data['priority_line_ids'],['L00002'])
            self.assertEqual(kwargs['json_schema']['properties']['changes']['items']['properties']['line_id']['enum'],
                             ['L00002','L00003'])
            return json.dumps({'changes':[{'line_id':'L00002','text':'Порядок консультации уточняется при обращении'}]})
        updated=patch_document(ask,'qwen3:8b','без бесплатной консультации',source,
                              "Не подтверждённое предложение услуги: 'бесплатную консультацию'",constrained=True)
        self.assertIn('Порядок консультации уточняется',updated)
        self.assertNotIn('бесплатную консультацию',updated)

    def test_editor_does_not_require_a_link_for_consultation_questions(self):
        request='Цель: получить вопросы о консультации. Стоимость и адрес неизвестны.'
        strategy='В стратегии указана цель получать вопросы о консультации.'
        calendar='Публикация: Задайте вопрос о консультации в комментариях.'
        opinion={'pass':False,'issues':[{'document':'calendar','type':'missing_required_content',
          'quote':calendar,'reason':'Отсутствует ссылка в биографии или в посте на консультацию.'}]}
        from unittest.mock import patch
        with patch('independent_acceptance_editor._call_editor_once',return_value=opinion):
            result=call_editor(request,strategy,calendar)
        self.assertTrue(result['pass'])
        self.assertEqual(len(result['disproved_absence_claims']),1)
        requested=request+' Добавь ссылку на форму записи в каждый пост.'
        with patch('independent_acceptance_editor._call_editor_once',return_value=opinion):
            result=call_editor(requested,strategy,calendar)
        self.assertFalse(result['pass'])

    def test_negated_forbidden_item_does_not_authorize_or_trigger_false_finding(self):
        request='Не придумывай цены, отзывы, бесплатную консультацию и сроки.'
        self.assertEqual(scan_unsupported_business_claims(request,
            'Не предлагать бесплатную консультацию.', ''),[])
        self.assertTrue(scan_unsupported_business_claims(request,
            'Мы предлагаем бесплатную консультацию.', ''))

if __name__=='__main__':unittest.main()
