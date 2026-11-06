import unittest

from content_requirements import publication_scope
from random_acceptance_tasks import (BUSINESSES, generate_acceptance_tasks,
                                    select_acceptance_author_models,
                                    select_profile_author_models,
                                    build_randomized_acceptance_cases,
                                    build_randomized_workflow_cases)
from generic_text_review import select_independent_model, select_independent_reviewers
from compound_retry import run_compound_with_retry
from compound_tasks import is_compound_request


class RandomAcceptanceTaskTests(unittest.TestCase):
    def test_each_author_model_receives_a_distinct_random_brief(self):
        cases=build_randomized_acceptance_cases(825, ['qwen3:8b','groq/model-a','openrouter/vendor/model:free'])
        self.assertEqual(len(cases),3)
        self.assertEqual(len({x['request'] for x in cases}),3)
        self.assertEqual(len({x['category'] for x in cases}),3)
        self.assertEqual([x['author_model'] for x in cases],
                         ['qwen3:8b','groq/model-a','openrouter/vendor/model:free'])
        cases[0]['goal']='mutated'
        self.assertNotEqual(cases[1]['goal'],'mutated')

    def test_each_seed_replays_all_briefs_and_new_seed_changes_them(self):
        first=build_randomized_acceptance_cases(826,['a:1','b:1'])
        replay=build_randomized_acceptance_cases(826,['a:1','b:1'])
        second=build_randomized_acceptance_cases(827,['a:1','b:1'])
        self.assertEqual([x['request'] for x in first],[x['request'] for x in replay])
        self.assertNotEqual([x['request'] for x in first],[x['request'] for x in second])

    def test_randomized_comparison_requires_unique_models(self):
        with self.assertRaises(ValueError): build_randomized_acceptance_cases(1,[])
        with self.assertRaises(ValueError): build_randomized_acceptance_cases(1,['qwen:8b','QWEN:8B'])

    def test_each_run_can_replay_exactly_from_reported_seed(self):
        self.assertEqual(generate_acceptance_tasks(842106, 3),
                         generate_acceptance_tasks(842106, 3))

    def test_different_seeds_change_the_internal_briefs(self):
        first = generate_acceptance_tasks(842106, 3)
        second = generate_acceptance_tasks(842107, 3)
        self.assertNotEqual(first, second)

    def test_briefs_vary_business_channel_cadence_and_meet_content_contract(self):
        cadences=set()
        for seed in range(20):
            tasks = generate_acceptance_tasks(seed, 3)
            self.assertEqual(len({task['category'] for task in tasks}), 3)
            for task in tasks:
                scope = publication_scope(task['request'])
                self.assertEqual(scope.weeks, 1)
                self.assertIn(task['posts'], (1,2))
                cadences.add(task['posts'])
                self.assertEqual(scope.per_week, task['posts'])
                self.assertEqual(scope.total, task['posts'])
                self.assertIn(task['channel'], task['request'])
                self.assertIn(task['audience'], task['request'])
                self.assertIn(task['goal'], task['request'])
                self.assertIn(task['category'], task['request'])
                self.assertIn('без произвольной числовой цели', task['request'])
                self.assertTrue(is_compound_request(task['request']))
        self.assertEqual(cadences,{1,2})
        self.assertGreaterEqual(len(BUSINESSES), 10)

    def test_bad_generator_arguments_fail_closed(self):
        with self.assertRaises(TypeError):
            generate_acceptance_tasks('not-a-seed', 3)
        with self.assertRaises(ValueError):
            generate_acceptance_tasks(7, 101)

    def test_every_installed_model_tag_including_former_reviewer_is_selected(self):
        installed = ['qwen3:8b', 'gemma3:4b', 'gemma3:12b', 'mistral:7b', 'llama3.1:8b']
        chosen = select_acceptance_author_models(installed)
        self.assertEqual(len(chosen), 5)
        self.assertEqual(set(chosen), set(installed))
        self.assertEqual(set(select_acceptance_author_models(installed, excluded_families=('qwen3',))),
                         {'gemma3:4b', 'gemma3:12b', 'mistral:7b', 'llama3.1:8b'})

    def test_fast_profile_compares_openai_with_a_random_local_model(self):
        import random
        installed=['qwen3:8b','llama3.1:8b','gpt-oss:20b-cloud','openai/gpt-6-luna']
        chosen=select_profile_author_models(installed,'fast',chooser=random.Random(3))
        self.assertEqual(len(chosen),2)
        self.assertIn('openai/gpt-6-luna',chosen)
        self.assertEqual(len(set(chosen)),2)
        self.assertTrue(set(chosen).issubset(installed))
        local=[name for name in chosen if '/' not in name and
               not name.casefold().endswith((':cloud','-cloud'))]
        self.assertEqual(len(local),1)

    def test_fast_profile_compares_ollama_cloud_with_a_random_local_model(self):
        import random
        installed=['qwen3:8b','llama3.1:8b','mistral:7b','gpt-oss:20b-cloud']
        chosen=select_profile_author_models(installed,'fast',chooser=random.Random(4))
        self.assertEqual(len(chosen),2)
        self.assertTrue(any(name.casefold().endswith((':cloud','-cloud')) for name in chosen))
        self.assertTrue(any('/' not in name and
                            not name.casefold().endswith((':cloud','-cloud')) for name in chosen))
        self.assertEqual(len(set(chosen)),2)
        self.assertTrue(set(chosen).issubset(installed))

    def test_fast_cloud_only_pool_remains_usable(self):
        selected=select_profile_author_models(['gemma4:cloud','gpt-oss:20b-cloud'],'fast')
        self.assertEqual(set(selected),{'gemma4:cloud','gpt-oss:20b-cloud'})

    def test_fast_single_author_pool_remains_usable(self):
        self.assertEqual(select_profile_author_models(['openai/test-model'],'fast'),['openai/test-model'])

    def test_full_profile_keeps_complete_model_coverage(self):
        import random
        installed=['qwen3:8b','llama3.1:8b','openai/gpt-6-luna']
        self.assertEqual(set(select_profile_author_models(installed,'full',chooser=random.Random(5))),
                         set(installed))

    def test_mixed_workflow_sample_covers_compound_and_standalone_routes(self):
        models=['qwen3:8b','llama3.1:8b']
        first=build_randomized_workflow_cases(9123,models)
        replay=build_randomized_workflow_cases(9123,models)
        self.assertEqual([x['request'] for x in first],[x['request'] for x in replay])
        self.assertEqual({x['workflow_type'] for x in first},
                         {'compound_strategy_calendar','standalone_post'})
        self.assertEqual(len({x['request'] for x in first}),2)
        standalone=next(x for x in first if x['workflow_type']=='standalone_post')
        self.assertIn('один',standalone['request'].casefold())
        self.assertIn('неподтверждённые свойства бизнеса',standalone['request'].casefold())
        self.assertIn('подтверждённую услугу',standalone['request'].casefold())
        self.assertIn('не выдумывай позиции',standalone['request'].casefold())
        from compound_tasks import is_compound_request
        self.assertFalse(is_compound_request(standalone['request']))

    def test_each_author_gets_a_reviewer_from_a_different_model_family(self):
        installed = ['qwen3:8b', 'gemma3:4b', 'gemma3:12b', 'mistral:7b', 'llama3.1:8b']
        for author in installed:
            reviewer = select_independent_model(author, candidates=installed)
            self.assertNotEqual(reviewer.split(':', 1)[0], author.split(':', 1)[0])
            self.assertIn(reviewer, installed)

    def test_multi_model_comparison_uses_distinct_reviewer_families_when_possible(self):
        import random
        installed = ['qwen3:8b', 'gemma3:4b', 'mistral:7b', 'llama3.1:8b', 'deepseek-r1:8b']
        authors = sorted(installed)
        assigned = select_independent_reviewers(authors,candidates=installed,
                                                chooser=random.Random(20261024))
        self.assertEqual(set(assigned),set(authors))
        self.assertEqual(len({m.split(':',1)[0] for m in assigned.values()}),len(authors))
        for author,reviewer in assigned.items():
            self.assertNotEqual(author.split(':',1)[0],reviewer.split(':',1)[0])

    def test_selected_test_model_reaches_production_executor(self):
        seen = []
        def execute(request, context, model='default', progress=None):
            seen.append(model)
            return {'action': 'blocked'}
        run_compound_with_retry(execute, 'task', {}, max_attempts=1, model='mistral:7b')
        self.assertEqual(seen, ['mistral:7b'])


if __name__ == '__main__':
    unittest.main()
