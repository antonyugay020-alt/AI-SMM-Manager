import unittest
from strategy_integrity import explicit_period_conflicts, strategy_integrity_issues


class ExplicitSourcePeriodTests(unittest.TestCase):
    def test_rejects_strategy_plan_duration_not_in_request(self):
        request='Подготовь план на одну неделю, две публикации в неделю.'
        for text in ('Срок реализации: 2 недели. Тестируем форматы.',
                     'План действий на две недели. Начнём с аудита.',
                     'Календарь на 3 недели: распределение материалов.'):
            with self.subTest(text=text):
                self.assertTrue(explicit_period_conflicts(text,request))

    def test_actual_strategy_integrity_checks_explicit_period(self):
        request='Сделай контент-план на одну неделю, 2 публикации в неделю.'
        strategy=('## План действий\n'
                  '1. Разработка контент-плана на 2 недели.\n'
                  '## Параметры контентного плана\n'
                  '- Публикационный ритм: 2 публикации в неделю.\n'
                  '- Период: 1 неделя.')
        findings=strategy_integrity_issues(strategy,request,per_week=2)
        self.assertTrue(any('меняет срок' in finding for finding in findings),findings)

    def test_matching_source_period_is_allowed(self):
        request='Составь стратегию на 1 неделю.'
        self.assertEqual(explicit_period_conflicts('Период: 1 неделя. План опубликован.',request),[])

    def test_implicit_default_is_not_enforced_as_source_requirement(self):
        self.assertEqual(explicit_period_conflicts('Срок реализации: 2 недели.',
                                                  'Составь стратегию продвижения.'),[])

    def test_negated_duration_is_not_treated_as_asserted_plan(self):
        self.assertEqual(explicit_period_conflicts('План не на 2 недели, а на одну неделю.',
                                                  'Составь план на 1 неделю.'),[])

if __name__=='__main__':
    unittest.main()
