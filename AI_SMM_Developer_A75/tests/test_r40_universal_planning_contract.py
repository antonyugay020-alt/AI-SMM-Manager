import json
import unittest

from semantic_brief import _topic_candidates, plan_slots
from calendar_planner import formats_for_platform
from publication_schedule import days_for_frequency


class UniversalPlanningContractTests(unittest.TestCase):
    def test_host_owns_platform_format_even_when_model_returns_localized_label(self):
        def ask(model, system, user):
            return json.dumps({'posts': [
                {'topic': 'Как выбрать подходящий формат общения с аудиторией',
                 'format': 'Text post with images'}
            ]}, ensure_ascii=False)

        slots = plan_slots(ask, 'model', 'План для Telegram', 'strategy',
                           {'channels': 'Telegram'}, 1, per_week=1)
        self.assertEqual(slots[0]['format'], formats_for_platform('Telegram')[0])
        self.assertNotEqual(slots[0]['format'], 'Text post with images')

    def test_valid_topic_survives_partial_batch_and_only_missing_slot_is_retried(self):
        calls = []

        def ask(model, system, user):
            data = json.loads(user)
            calls.append(data)
            if len(calls) == 1:
                topics = [
                    {'topic': 'Как подготовиться к первому разговору с аудиторией'},
                    {'topic': 'Как подготовиться к первому разговору с аудиторией'},
                ]
                return json.dumps({'topics': topics}, ensure_ascii=False)
            return json.dumps({'items': [
                {'topic': 'Какие вопросы помогают уточнить ожидания клиента'}
            ]}, ensure_ascii=False)

        slots = plan_slots(ask, 'model', 'План для Telegram', 'strategy',
                           {'channels': 'Telegram'}, 1, per_week=2)
        self.assertEqual(len(slots), 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]['open_slots'], [{'day': days_for_frequency(2)[1],
                                                   'format': formats_for_platform('Telegram')[1]}])
        self.assertEqual(len({slot['topic'] for slot in slots}), 2)

    def test_candidate_normalizer_accepts_object_and_model_envelopes(self):
        self.assertEqual(_topic_candidates('{"topic":"A distinct proposal"}')[0]['topic'],
                         'A distinct proposal')
        self.assertEqual(_topic_candidates('{"results":[{"topic":"Another proposal"}]}')[0]['topic'],
                         'Another proposal')


if __name__ == '__main__':
    unittest.main()
