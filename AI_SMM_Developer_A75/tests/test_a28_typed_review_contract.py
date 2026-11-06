"""A28 contracts: classify claim scope and validate format without padding copy."""
import json
import unittest

from claim_support import adjudicate_factual_findings
from content_quality_engine import inspect_post
from evidence_context import build_evidence_context
from publication_writer import validate_week
from independent_acceptance_editor import call_editor
from unittest.mock import patch


class TypedReviewContracts(unittest.TestCase):
    def test_generic_help_promise_is_not_waived_by_model_cta_label(self):
        request = 'Подготовь публикацию для веломастерской.'
        issue = {'document': 'calendar', 'type': 'invented_fact',
                 'quote': 'Мы готовы помочь.',
                 'reason': 'Не подтверждён процесс компании.'}
        result = adjudicate_factual_findings(request, [issue], ask=lambda *args: {
            'statement_class': 'invitation_cta', 'verdict': 'supported',
            'source_id': 'none', 'source_quote': '',
            'reason': 'Это приглашение к диалогу без описания услуги.'})
        self.assertEqual(result['non_business'], [])
        self.assertEqual(result['supported'], [])
        self.assertEqual(result['blocking'][0]['adjudication'], 'unsupported')

    def test_general_topic_information_does_not_require_business_source(self):
        request = 'Подготовь образовательный пост об уходе за одеждой.'
        issue = {'document': 'calendar', 'type': 'invented_fact',
                 'quote': 'Одежда может терять форму по разным причинам.',
                 'reason': 'В исходном запросе нет данных о причинах.'}
        result = adjudicate_factual_findings(request, [issue], ask=lambda *args: {
            'statement_class': 'general_information', 'verdict': 'supported',
            'source_id': 'none', 'source_quote': '',
            'reason': 'Это общее утверждение по теме, не факт о бизнесе.'})
        self.assertEqual(result['blocking'], [])
        self.assertEqual(len(result['non_business']), 1)


    def test_service_booking_cta_requires_source_adjudication(self):
        request=('Мастерская делает диагностику велосипедов и принимает клиентов '
                 'по предварительной записи. Адрес и стоимость неизвестны.')
        issue={'document':'calendar','type':'invented_fact',
               'quote':'Запишитесь на диагностику в мастерскую.',
               'reason':'Возможно, неизвестны адрес и стоимость.'}
        result=adjudicate_factual_findings(request,[issue],ask=lambda *args: {
            'statement_class':'invitation_cta','verdict':'unsupported',
            'source_id':'none','source_quote':'',
            'reason':'Это нейтральное приглашение и не утверждает адрес или стоимость.'})
        self.assertEqual(result['non_business'],[])
        self.assertEqual(result['supported'],[])
        self.assertEqual(result['blocking'][0]['adjudication'],'unsupported')

    def test_final_editor_uses_typed_claim_resolution_for_false_fact_flags(self):
        issue = {'document': 'calendar', 'type': 'invented_fact',
                 'quote': 'Мы готовы помочь.',
                 'reason': 'Не подтверждён конкретный процесс компании.',
                 'evidence_segment': 'CTA: Мы готовы помочь — задайте вопрос в комментариях.'}
        def resolve(request, findings, **kwargs):
            self.assertEqual(findings[0]['evidence_segment'], issue['evidence_segment'])
            return {'supported': [], 'blocking': [],
                    'non_business': [{**findings[0], 'statement_class': 'invitation_cta'}]}
        with patch('independent_acceptance_editor._review_windows',
                   return_value=[('Стратегия', 'Календарь')]), \
             patch('independent_acceptance_editor._call_editor_once',
                   return_value={'pass': False, 'issues': [issue]}), \
             patch('factual_claim_audit.scan_unsupported_business_claims', return_value=[]), \
             patch('claim_support.adjudicate_factual_findings', side_effect=resolve):
            result = call_editor('Создай публикацию.', 'Стратегия', 'Календарь')
        self.assertTrue(result['pass'])
        self.assertEqual(result['issues'], [])

    def test_short_copy_is_accepted_when_required_fields_and_storyboard_exist(self):
        slot = {'week': 1, 'day': 'Вт', 'format': 'Instagram Stories',
                'platform': 'Instagram', 'topic': 'Уход за одеждой', 'goal': 'Дать памятку'}
        post = {'id': '1/Вт', 'topic': slot['topic'],
                'visual': 'Слайд 1: вопрос. Слайд 2: основной совет. Слайд 3: призыв сохранить памятку.',
                'caption': 'Проверьте ярлык перед уходом.', 'cta': 'Сохраните памятку.',
                'kpi': 'Сохранения', 'audience': 'Подписчики',
                'measurement': 'Счётчик сохранений площадки', 'materials': 'Макет трёх слайдов'}
        self.assertEqual(validate_week([post], [slot]), [])
        self.assertEqual(inspect_post({**post, 'format': slot['format']}), [])

    def test_context_turns_an_emotional_phrase_into_a_business_outcome_claim(self):
        request = 'Подготовь публикацию для языкового клуба.'
        issue = {'document': 'strategy', 'type': 'invented_fact',
                 'quote': 'чувствовать себя уверенно',
                 'evidence_segment': 'Наш клуб помогает ученикам чувствовать себя уверенно.',
                 'reason': 'Не подтверждён обещанный результат.'}
        captured = {}
        def ask(model, system, user):
            captured.update(json.loads(user))
            return {'statement_class': 'service_outcome', 'verdict': 'unsupported',
                    'source_id': 'none', 'source_quote': '',
                    'reason': 'Такое обещание не подтверждено.'}
        result = adjudicate_factual_findings(request, [issue], ask=ask)
        self.assertEqual(captured['findings'][0]['document_context'], issue['evidence_segment'])
        self.assertEqual(result['blocking'][0]['statement_class'], 'service_outcome')

    def test_concrete_unsourced_business_fact_remains_blocking(self):
        request = 'Подготовь публикацию для языкового клуба.'
        issue = {'document': 'calendar', 'type': 'invented_fact',
                 'quote': 'Каждое занятие ведёт сертифицированный преподаватель.',
                 'reason': 'Не подтверждён состав команды.'}
        result = adjudicate_factual_findings(request, [issue], ask=lambda *args: {
            'statement_class': 'operational_process', 'verdict': 'unsupported',
            'source_id': 'none', 'source_quote': '',
            'reason': 'В разрешённых источниках сведений о квалификации преподавателей нет.'})
        self.assertEqual(result['non_business'], [])
        self.assertEqual(result['blocking'][0]['adjudication'], 'unsupported')


if __name__ == '__main__':
    unittest.main()
