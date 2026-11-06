import unittest

from strategy_integrity import bind_explicit_task_scope


class TaskScopeBindingTests(unittest.TestCase):
    def test_explicit_scope_is_bound_as_host_owned_strategy_data(self):
        strategy='## Цели и задачи\nСформировать полезную коммуникацию.'
        request='План на 2 недели: по 4 публикации в неделю, всего 8 публикаций.'
        bound, changes=bind_explicit_task_scope(strategy,request,4)
        self.assertIn('4 публикации в неделю',bound)
        self.assertIn('2 недели',bound)
        self.assertIn('8 публикаций',bound)
        self.assertEqual(changes[0]['field'],'publication_scope')

    def test_derived_total_is_bound_and_binding_is_idempotent(self):
        strategy='Стратегия полезных публикаций.'
        request='Подготовь 6 публикаций на 3 недели.'
        bound,_=bind_explicit_task_scope(strategy,request,2)
        same,changes=bind_explicit_task_scope(bound,request,2)
        self.assertIn('2 публикации в неделю',bound)
        self.assertIn('3 недели',bound)
        self.assertIn('6 публикаций',bound)
        self.assertEqual(same,bound)
        self.assertEqual(changes,[])

    def test_application_default_is_not_misrepresented_as_user_requirement(self):
        strategy='Стратегия полезных публикаций.'
        bound,changes=bind_explicit_task_scope(strategy,'Разработай стратегию.',3)
        self.assertEqual(bound,strategy)
        self.assertEqual(changes,[])


if __name__=='__main__':
    unittest.main()
