import unittest
from task_routing import execution_request, resolve_request

class RoutingIntegration(unittest.TestCase):
    def test_new_task_ignores_pending(self):
        text='Разработай стратегию и контент-план для туристического агентства'
        self.assertEqual(execution_request(text,'стратегия для автодетейлинга',
                         'Часть документов не создана или не прошла проверку.'), (text,'new'))
    def test_explicit_resume_preserves_original(self):
        self.assertEqual(execution_request('продолжи задание','план для аптеки'),
                         ('план для аптеки','resume'))
    def test_clarification_keeps_question_and_answer(self):
        text,mode=execution_request('Instagram','Составь стратегию и контент-план','На какой площадке?')
        self.assertEqual(mode,'clarification')
        self.assertIn('Составь стратегию',text)
        self.assertIn('На какой площадке?',text)
        self.assertIn('Instagram',text)
    def test_long_new_task_not_clarification(self):
        text='Разработай стратегию и контент-план для новой школы на 13 недель'
        self.assertEqual(execution_request(text,'старое задание','Какая площадка?')[1],'new')
    def test_failure_status_not_question(self):
        self.assertEqual(resolve_request('Instagram','старое',
          'Часть документов не создана или не прошла проверку.')[1], 'new')

if __name__=='__main__': unittest.main()
