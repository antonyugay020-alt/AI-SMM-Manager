"""A14 regressions from live Windows cases: distinguish audience research from offers."""
import unittest

from factual_claim_audit import scan_unsupported_business_claims


class BusinessFactScopeA14(unittest.TestCase):
    def test_audience_needs_do_not_turn_into_service_promises(self):
        request = 'Стратегия для языковой школы. Не выдумывай условия занятий и обещания результата.'
        strategy = ('## Потребности аудитории\n'
                    '- Учащимся важны обратная связь и уверенность в разговоре.\n'
                    '## Позиционирование\n'
                    'На занятиях мы даём обратную связь и помогаем почувствовать уверенность.')
        found = scan_unsupported_business_claims(request, strategy, '')
        self.assertEqual(len(found), 2)
        self.assertTrue(any('обратную связь' in issue['quote'] for issue in found))
        self.assertTrue(any('уверенность' in issue['quote'] for issue in found))

    def test_unknown_process_and_resources_are_not_derived_from_industry(self):
        request = 'Студия керамики. Не выдумывай материалы и порядок работы.'
        strategy = ('В мастерской мы начинаем с нуля и сушим изделия перед обжигом.\n'
                    'У нас есть безопасная глина и новое оборудование.')
        found = scan_unsupported_business_claims(request, strategy, '')
        self.assertTrue(any('начинаем' in issue['quote'] for issue in found))
        self.assertTrue(any('безопасная глина' in issue['quote'] for issue in found))

    def test_reader_education_without_company_claim_is_allowed(self):
        strategy = 'Изделия из глины обычно сушат перед обжигом; уточните рекомендации преподавателя.'
        self.assertEqual(scan_unsupported_business_claims('Подготовь полезный материал.', strategy, ''), [])


if __name__ == '__main__':
    unittest.main()
