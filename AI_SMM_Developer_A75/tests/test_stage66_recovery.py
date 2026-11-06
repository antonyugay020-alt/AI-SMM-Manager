import json
import unittest
from semantic_brief import plan_slots

class TopicFallbackTests(unittest.TestCase):
    def test_recover_week_from_repeated_batches(self):
        calls=[]
        def ask(model, system, user):
            data=json.loads(user)
            if 'day' not in data:
                return json.dumps({'posts':[{'topic':'Повторяющаяся тема для всех публикаций'}]*3},ensure_ascii=False)
            calls.append(data['day'])
            return json.dumps({'posts':[{'topic':f'Полезная уникальная тема недели {data["week"]} дня {data["day"]}'}]},ensure_ascii=False)
        saved=[]
        result=plan_slots(ask,'m','бизнес','стратегия',{'channels':'Instagram'},2,on_week=lambda w,s:saved.append((w,len(s))))
        self.assertEqual(len(result),6)
        self.assertEqual(len({s['topic'] for s in result}),6)
        self.assertEqual(saved,[(1,3),(2,6)])
        self.assertEqual(len(calls),5)

    def test_reject_unfixable_week_without_fake_topics(self):
        saved=[]
        def ask(model,system,user):
            return json.dumps({'posts':[{'topic':'одинаковая повторяющаяся тема'}]*3},ensure_ascii=False)
        with self.assertRaises(ValueError):
            plan_slots(ask,'m','бизнес','стратегия',{},1,on_week=lambda w,s:saved.append(s))
        self.assertEqual(saved,[])

if __name__=='__main__': unittest.main()
