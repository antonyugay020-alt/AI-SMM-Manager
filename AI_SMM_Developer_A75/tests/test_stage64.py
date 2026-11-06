import json
import unittest
from semantic_brief import analyze, plan_slots
from professional_content import requested_weeks

class Stage64Tests(unittest.TestCase):
    def test_exact_weeks_priority(self):
        self.assertEqual(requested_weeks('3 месяца — 12 недель'),12)
        self.assertEqual(requested_weeks('3 месяца — 13 недель'),13)
    def test_semantic_analyzer_does_not_require_business_dictionary(self):
        def ask(model,system,user):
            self.assertIn('ВЕСЬ',system)
            self.assertIn('архитектора',user)
            return json.dumps({'intent':'контент-план','business':'личный бренд архитектора',
                               'audience':'владельцы квартир','requirements':['не выдумывать адрес']},ensure_ascii=False)
        result=analyze(ask,'mock','Разработай контент-план для личного бренда архитектора. Аудитория: владельцы квартир.')
        self.assertEqual(result['business'],'личный бренд архитектора')
    def test_plan_slots_no_preset_industry(self):
        def ask(model,system,user):
            week=json.loads(user)['week']
            return json.dumps({'posts':[{'topic':f'Уникальная идея {week} номер {i}',
              'brief':f'Сценарий {i}', 'goal':'охват','audience':'покупатели',
              'format':['Reels','Карусель','Stories'][i]} for i in range(3)]},ensure_ascii=False)
        slots=plan_slots(ask,'mock','Задание','Стратегия',{'channels':'Instagram'},2)
        self.assertEqual(len(slots),6)
        self.assertEqual(len({s['topic'] for s in slots}),6)

if __name__=='__main__': unittest.main()
