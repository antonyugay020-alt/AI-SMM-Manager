import json
import unittest

from claim_support import _is_planning_metadata
from editorial_claim_policy import BUSINESS_FACT_POLICY
from publication_writer import write_week, write_single, repair_single, repair_fields
from semantic_editor import audit_post


class A56ArchitectureContracts(unittest.TestCase):
    def test_deliverable_schema_is_planning_metadata_across_domains(self):
        samples = [
            'Каждый пост содержит тему, текст, визуальный сценарий, CTA, KPI, способ измерения, аудиторию и материалы.',
            'Для каждой публикации нужно включить тему, текст, визуал, аудиторию и необходимые материалы.',
            'Each post must contain a topic, caption, visual, KPI and audience.',
        ]
        for claim in samples:
            with self.subTest(claim=claim):
                self.assertTrue(_is_planning_metadata({'quote': claim}))

    def test_publication_generation_and_repairs_do_not_use_generated_strategy_as_evidence(self):
        sentinel = 'UNTRUSTED_STRATEGY_SENTINEL'
        slot = {'week': 1, 'day': 'Пн', 'format': 'Пост', 'platform': 'Instagram',
                'topic': 'Проверяемые сведения', 'brief': 'Задание', 'goal': 'Информировать',
                'audience': 'Читатели'}
        original = {'id': '1/Пн', 'topic': slot['topic'], 'visual': 'Кадр 1. Титр и подпись.',
                    'caption': 'Исходный текст', 'cta': 'Задайте вопрос', 'kpi': 'Вопросы',
                    'audience': 'Читатели', 'measurement': 'Подсчёт вопросов', 'materials': 'Макет'}
        prompts = []

        def ask(model, system, user):
            prompts.append((system, user))
            if len(prompts) == 1:
                return '{"posts":[]}'
            if len(prompts) == 2:
                return json.dumps(original, ensure_ascii=False)
            if len(prompts) == 3:
                return json.dumps(original, ensure_ascii=False)
            return '{"replacements":{"P001":"Исправленный текст"}}'

        write_week(ask, 'model', 'Исходный запрос', {}, sentinel, [slot])
        write_single(ask, 'model', 'Исходный запрос', {}, sentinel, slot)
        repair_single(ask, 'model', 'Исходный запрос', {}, sentinel, slot, original,
                      ['неподтверждённое утверждение'])
        repair_fields(ask, 'model', 'Исходный запрос', {}, sentinel, slot, original,
                      ['caption: неподтверждённое утверждение'])
        self.assertEqual(len(prompts), 4)
        self.assertTrue(all(sentinel not in user for _, user in prompts))
        self.assertTrue(all('Исходный запрос' in user for _, user in prompts))

    def test_semantic_quality_rubric_allows_value_within_verified_information(self):
        captured = {}

        def ask(model, system, user):
            captured['system'] = system
            return '{"approved":true,"issues":[]}'

        post = {'id': '1/Пн', 'topic': 'Вопросы перед выбором',
                'visual': 'Постановочный макет карточки с вопросом на экране.',
                'caption': 'Какие сведения вам важно уточнить перед выбором?',
                'cta': 'Напишите вопрос', 'kpi': 'Количество вопросов',
                'audience': 'Заинтересованные читатели', 'measurement': 'Подсчёт вопросов',
                'materials': 'Макет карточки'}
        accepted, issues = audit_post(ask, 'model', 'Подготовить публикацию с вопросом.',
                                      {'task_analysis': {'unknowns': ['условия услуги']}},
                                      {'week': 1, 'day': 'Пн', 'format': 'Пост'}, post)
        self.assertFalse(issues)
        self.assertEqual(accepted, post)
        self.assertIn('в пределах подтверждённых сведений', captured['system'])
        self.assertNotIn('реальные действия', captured['system'])
        self.assertIn('не требуй бизнес-процесс', captured['system'])

    def test_category_is_not_inferred_from_services(self):
        self.assertIn('не выводи отдельную категорию бизнеса', BUSINESS_FACT_POLICY.casefold())


if __name__ == '__main__':
    unittest.main()
