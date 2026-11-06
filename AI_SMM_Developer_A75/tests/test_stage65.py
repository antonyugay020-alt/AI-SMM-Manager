import unittest
from semantic_brief import plan_slots

class Stage65RecoveryTests(unittest.TestCase):
    def test_resumes_accepted_weeks_without_regeneration(self):
        calls=[]; snapshots=[]
        def ask(model,system,user):
            import json
            data=json.loads(user)
            calls.append(data['week'])
            w=data['week']
            return json.dumps({'posts':[{'topic':f'Уникальная тема недели {w} вариант {i} для клиентов', 'format':'Reels'} for i in range(3)]},ensure_ascii=False)
        first=plan_slots(ask,'model','задание','стратегия',{'channels':['Instagram']},2,on_week=lambda w,s:snapshots.append(list(s)))
        self.assertEqual(calls,[1,2])
        calls.clear()
        second=plan_slots(ask,'model','задание','стратегия',{'channels':['Instagram']},3,existing_slots=snapshots[-1])
        self.assertEqual(calls,[3])
        self.assertEqual(second[:6],first)

    def test_rejects_invalid_checkpoint_prefix(self):
        calls=[]
        def ask(model,system,user):
            import json
            w=json.loads(user)['week'];calls.append(w)
            return json.dumps({'posts':[{'topic':f'Содержательная тема {w} и публикация {i}', 'format':'Reels'} for i in range(3)]},ensure_ascii=False)
        bad=[{'week':1,'day':'Пн','topic':'Одна тема повторяется'}, {'week':1,'day':'Ср','topic':'Одна тема повторяется'}, {'week':1,'day':'Пт','topic':'Третья тема для людей'}]
        result=plan_slots(ask,'model','задание','стратегия',{'channels':['Instagram']},1,existing_slots=bad)
        self.assertEqual(calls,[1]);self.assertEqual(len(result),3)

if __name__=='__main__': unittest.main()
