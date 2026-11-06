"""A15 contracts taken from the user's actual Windows A15 Ollama run."""
import unittest

from factual_claim_audit import scan_unsupported_business_claims
from windows_acceptance import acceptance_status


class WindowsA14RootCauseRegressions(unittest.TestCase):
    def test_missing_developer_suite_blocks_acceptance_without_false_test_failure(self):
        checks={'package_integrity':{'status':'PASS'},
                'developer_test_source':{'status':'NOT_TESTED'},
                'unit_tests':{'status':'NOT_TESTED'}}
        self.assertEqual(acceptance_status(checks,('package_integrity','unit_tests')),'BLOCKED')
        checks['unit_tests']={'status':'FAIL'}
        self.assertEqual(acceptance_status(checks,('package_integrity','unit_tests')),'BLOCKED')
        checks['unit_tests']={'status':'PASS'}
        self.assertEqual(acceptance_status(checks,('package_integrity','unit_tests')),'PASS')

    def test_copied_user_restrictions_are_not_reported_as_business_claims(self):
        request = ('Мастерская делает диагностику и замену колодок. Нельзя придумывать цены, '
                   'адреса и обещания полной безопасности.')
        strategy = ('## Подтверждённые вводные\nДиагностика и замена тормозных колодок.\n'
                    '## Ограничения\nНельзя упоминать цены и адреса, обещать полную безопасность.\n'
                    '## Позиционирование\nИнформировать о пользе профилактики без обещаний комфорта, обратной связи или безопасности.')
        self.assertEqual(scan_unsupported_business_claims(request, strategy, ''), [])

    def test_confirmed_service_description_is_not_misclassified_as_unknown_process(self):
        request = 'Разговорные занятия английским для взрослых начинающего уровня в небольших группах.'
        text = 'Мы предлагаем разговорные занятия английским для взрослых начинающего уровня в небольших группах.'
        self.assertEqual(scan_unsupported_business_claims(request, text, ''), [])

    def test_telegram_feedback_means_channel_interaction_not_service_benefit(self):
        text = 'Telegram позволяет публиковать визуальный контент и получать обратную связь от аудитории.'
        self.assertEqual(scan_unsupported_business_claims('Нужны вопросы аудитории в Telegram.', text, ''), [])

    def test_specific_unconfirmed_process_and_service_benefits_remain_blocked(self):
        request = 'Студия ручной лепки для взрослых новичков. Не выдумывай этапы и обратную связь.'
        text = ('Мы начинаем с подготовки материалов, затем переходим к лепке, формовке и сушке. '
                'На занятиях преподаватель даёт обратную связь.')
        found = scan_unsupported_business_claims(request, text, '')
        reasons = ' '.join(item['reason'] for item in found)
        self.assertIn('процесс', reasons)
        self.assertIn('обратной связи', reasons)

    def test_unknowns_listed_for_confirmation_is_not_mistaken_for_a_promise(self):
        strategy = ('## Дополнительные материалы\n- **Необходимо подтвердить:**\n'
                    '  - Комфортность занятий\n  - Обратная связь\n  - Количество студентов')
        self.assertEqual(scan_unsupported_business_claims('Проверь неизвестные условия.', strategy, ''), [])

    def test_steps_presented_as_an_actual_class_are_not_generic_education(self):
        request = 'Керамическая студия проводит ручную лепку. Этапы не сообщены.'
        claimed = 'В студии вы узнаете, как подготовить материалы, создать изделие и пройти формовку и сушку.'
        generic = 'При ручной лепке изделия из глины обычно формуют и сушат перед обжигом.'
        self.assertTrue(scan_unsupported_business_claims(request, claimed, ''))
        self.assertEqual(scan_unsupported_business_claims(request, generic, ''), [])


if __name__ == '__main__':
    unittest.main()
