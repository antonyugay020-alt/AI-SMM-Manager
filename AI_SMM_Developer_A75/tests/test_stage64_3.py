import json
import unittest
from semantic_brief import plan_slots

class TopicRecoveryTests(unittest.TestCase):
    def test_retries_duplicate_week_without_losing_previous(self):
        calls=[]
        def ask(model, system, user):
            data=json.loads(user); calls.append((data['week'],data['retry']))
            w=data['week']
            if w==2 and data['retry']==1:
                topics=['Тема уникальная номер 1']*3
            else:
                topics=[f'Полезная конкретная тема недели {w} номер {i}' for i in range(3)]
            return json.dumps({'posts':[{'topic':t,'format':'Reels'} for t in topics]},ensure_ascii=False)
        slots=plan_slots(ask,'model','request','strategy',{'channels':'Instagram'},3)
        self.assertEqual(len(slots),9)
        self.assertEqual(calls,[(1,1),(2,1),(2,2),(3,1)])
        self.assertEqual(len({s['topic'] for s in slots}),9)

    def test_fails_explicitly_after_three_invalid_attempts(self):
        def ask(*args):
            return json.dumps({'posts':[{'topic':'одно'}]*3},ensure_ascii=False)
        with self.assertRaisesRegex(ValueError,'после 3 попыток'):
            plan_slots(ask,'model','request','strategy',{'channels':'Instagram','channels_basis':'recommendation'},1)

if __name__=='__main__': unittest.main()
