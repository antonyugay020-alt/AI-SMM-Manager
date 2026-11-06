import unittest
from unittest.mock import patch

from deliverable_contract import (
    is_deferred_strategy_publication_requirement,
    is_publication_bundle_requirement,
)
from independent_acceptance_editor import call_strategy_editor
from publication_writer import week_to_markdown
from editorial_evidence_matrix import disproved_document_role_claim
from russian_quantity import publication_count, week_count


class DeliverableOwnershipTests(unittest.TestCase):
    REQUEST = (
        'Подготовь стратегию и контент-план на неделю. '
        'Для каждой публикации дай тему, формат, полный текст, визуал, CTA и KPI.'
    )

    def test_publication_fields_are_owned_by_calendar_in_compound_request(self):
        issue = {
            'document': 'strategy',
            'type': 'missing_required_content',
            'quote': 'Образовательный',
            'requirement_quote': 'Для каждой публикации дай тему, формат, полный текст, визуал, CTA и KPI.',
        }
        self.assertTrue(is_publication_bundle_requirement(issue['requirement_quote'], self.REQUEST))
        self.assertTrue(is_deferred_strategy_publication_requirement(issue, self.REQUEST))

    def test_explicit_strategy_assignment_is_not_deferred(self):
        requirement = 'В стратегии для каждой публикации укажи текст и CTA.'
        issue = {'document': 'strategy', 'type': 'missing_required_content',
                 'requirement_quote': requirement}
        request = 'Подготовь стратегию. В стратегии для каждой публикации укажи текст и CTA.'
        self.assertFalse(is_publication_bundle_requirement(requirement, request))
        self.assertFalse(is_deferred_strategy_publication_requirement(issue, request))

    def test_strategy_only_review_defers_bundle_owned_publication_requirement(self):
        issue = {
            'document': 'strategy',
            'type': 'missing_required_content',
            'quote': 'Образовательный',
            'reason': 'Не хватает текста, визуала, CTA и KPI для каждой публикации.',
            'requirement_quote': 'Для каждой публикации дай тему, полный текст, визуал, CTA и KPI.',
        }
        with patch('independent_acceptance_editor._call_editor_once',
                   return_value={'pass': False, 'issues': [issue]}):
            verdict = call_strategy_editor(self.REQUEST, '# Стратегия\nПлан контента.')
        self.assertEqual(verdict, {'pass': True, 'issues': []})

    def test_complete_calendar_can_disprove_strategy_level_absence_claim(self):
        post = {'id': '1/Tue', 'topic': 'A specific topic', 'visual': 'A detailed visual plan. ' * 4,
                'caption': 'A complete publication text.', 'cta': 'Ask a question',
                'kpi': 'Count questions', 'measurement': 'Count comments',
                'audience': 'Adults', 'materials': 'Camera and notebook'}
        slot = {'week': 1, 'day': 'Tue', 'platform': 'Instagram',
                'format': 'Carousel', 'goal': 'Explain'}
        calendar = week_to_markdown([post], [slot])
        issue = {
            'document': 'strategy',
            'type': 'missing_required_content',
            'quote': 'Educational',
            'reason': 'Отсутствует текст, визуал, CTA и KPI для каждой публикации.',
        }
        self.assertTrue(disproved_document_role_claim(issue, self.REQUEST, calendar))


class RussianQuantityTests(unittest.TestCase):
    def test_count_forms(self):
        self.assertEqual([week_count(n) for n in (1, 2, 5, 11, 21)],
                         ['1 неделя', '2 недели', '5 недель', '11 недель', '21 неделя'])
        self.assertEqual([publication_count(n) for n in (1, 2, 5, 11, 21)],
                         ['1 публикация', '2 публикации', '5 публикаций', '11 публикаций', '21 публикация'])


if __name__ == '__main__':
    unittest.main()
