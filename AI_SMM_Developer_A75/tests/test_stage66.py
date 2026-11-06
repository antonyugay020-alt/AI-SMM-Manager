import unittest
import tempfile
from pathlib import Path
from task_routing import resolve_request
from semantic_brief import plan_slots

class LifecycleTests(unittest.TestCase):
    def test_explicit_resume(self):
        self.assertEqual(resolve_request('Продолжи задание', 'старое'), ('старое','resume'))
    def test_new_request_overrides_pending(self):
        self.assertEqual(resolve_request('Разработай стратегию для туристического агентства', 'автодетейлинг'), ('Разработай стратегию для туристического агентства','new'))
    def test_question_not_resume(self):
        self.assertEqual(resolve_request('Какие документы готовы?', 'старое')[1], 'new')
    def test_no_pending(self):
        self.assertEqual(resolve_request('продолжи задание')[1], 'resume_missing')
    def test_week_checkpoint_resume(self):
        calls=[]
        def ask(m,sys,user):
            import json
            week=json.loads(user)['week']; calls.append(week)
            return json.dumps({'posts':[{'topic':f'Уникальная полезная тема номер {week} раздел {i}', 'format':'Reels'} for i in range(3)]})
        partial=plan_slots(ask,'m','задание','стратегия',{'channels':'Instagram'},2)
        self.assertEqual(len(partial),6)
        calls.clear()
        final=plan_slots(ask,'m','задание','стратегия',{'channels':'Instagram'},3,existing_slots=partial)
        self.assertEqual(calls,[3]); self.assertEqual(len(final),9)
    def test_repeated_topics_not_silently_accepted(self):
        def ask(m,sys,user):
            return '{"posts":[{"topic":"Одинаковая тема про правильный уход"},{"topic":"Одинаковая тема про правильный уход"},{"topic":"Одинаковая тема про правильный уход"}]}'
        with self.assertRaises(ValueError):
            plan_slots(ask,'m','задание','стратегия',{},1)

if __name__=='__main__': unittest.main()
