"""R9 feedback: randomized strategy + calendar briefs must reach compound flow."""
import unittest

from compound_tasks import is_compound_request
from random_acceptance_tasks import generate_acceptance_tasks


class RandomizedBriefRoutingRegressionTests(unittest.TestCase):
    def test_all_randomly_generated_briefs_route_as_compound_document_tasks(self):
        for seed in range(40):
            for task in generate_acceptance_tasks(seed, count=5):
                with self.subTest(seed=seed, task=task['name']):
                    self.assertTrue(is_compound_request(task['request']))
                    self.assertIn('SMM-стратег', task['request'])
                    self.assertIn('контент-план', task['request'])

    def test_compound_detection_does_not_depend_on_imperative_verb(self):
        requests = (
            'Мне нужна практичная SMM-стратегия и контент-план для Telegram',
            'Подготовить SMM стратегию и контент план для мастерской',
            'Нужен контент-план и стратегия продвижения для студии',
        )
        for request in requests:
            with self.subTest(request=request):
                self.assertTrue(is_compound_request(request))

    def test_single_artifact_requests_remain_outside_compound_route(self):
        self.assertFalse(is_compound_request('Разработай SMM-стратегию для школы'))
        self.assertFalse(is_compound_request('Составь контент-план для мастерской'))

    def test_random_frame_variants_have_grammatically_matching_nouns(self):
        # Randomized dimensions may consume additional RNG draws over time;
        # grammar must hold across the generated sample, not require one old
        # seed to choose a particular sentence frame.
        requests = [task['request'] for seed in range(40)
                    for task in generate_acceptance_tasks(seed, 5)]
        self.assertTrue(any('Мне нужна практичная SMM-стратегия' in item for item in requests))
        self.assertFalse(any('Нужна практичная SMM-стратегию' in item for item in requests))

    def test_r9_random_gui_seed_now_routes_before_worker_launch(self):
        r9_seed = 10305060045860723960 ^ 0xA75A75A75
        request = generate_acceptance_tasks(r9_seed, count=1)[0]['request']
        self.assertTrue(is_compound_request(request))


if __name__ == '__main__':
    unittest.main()
