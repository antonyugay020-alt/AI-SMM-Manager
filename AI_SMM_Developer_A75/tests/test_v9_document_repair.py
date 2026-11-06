# -*- coding: utf-8 -*-
"""V9: document-targeted repair derived from actual V8 Windows findings."""
import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path
from strategy_recovery import produce_checked_strategy
from strategy_integrity import strategy_integrity_issues

BRIEF = ('Составь SMM-стратегию: 2 публикации в неделю. '
         'Разговорные занятия английским для взрослых в небольших группах. '
         'Цель — запросы на консультацию. Стоимость неизвестна. Не выдумывай цены и условия.')
BAD = ('# Стратегия\n\n## Цели\nФормировать доверие.\n\n'
       '## Контент\nПубликовать по 1 публикации в неделю. '\
       'Запишитесь на бесплатную консультацию.')
BAD_CADENCE_NORMALIZED = BAD.replace('по 1 публикации в неделю','по 2 публикации в неделю')
GOOD = ('# Стратегия\n\n## Цели\nФормировать доверие.\n\n'
        '## Контент\nПубликовать по 2 публикации в неделю. '\
        'Уточните возможность и условия консультации.')

class StrategyDocumentRepairContract(unittest.TestCase):
    def test_targeted_repair_gets_exact_rejected_document_and_failure(self):
        calls=[]
        def gen(msg,n):
            self.assertEqual(n,1)
            return BAD
        def repair(previous, problems, attempt):
            calls.append((previous,problems,attempt))
            return GOOD
        out=produce_checked_strategy(BRIEF,2,gen,lambda req,text:(True,''),repair=repair)
        self.assertTrue(out[1],out)
        self.assertEqual(out[0],GOOD)
        self.assertEqual(calls[0][0],BAD_CADENCE_NORMALIZED)
        self.assertIn('бесплатную консультацию',calls[0][1])
        self.assertEqual(calls[0][2],2)
        self.assertFalse(strategy_integrity_issues(GOOD,BRIEF,per_week=2))

    def test_bad_targeted_repair_does_not_bypass_validator(self):
        repaired=[]
        generated=[]
        def generate(msg,n):
            generated.append((msg,n))
            return BAD
        out=produce_checked_strategy(BRIEF,2,generate,
            lambda req,text:(True,''),repair=lambda prior,reason,n:(repaired.append(n) or BAD),attempts=3)
        self.assertFalse(out[1]);self.assertEqual(repaired,[2]);self.assertEqual([n for _,n in generated],[1,3])

    def test_existing_strategy_never_passed_to_repair(self):
        def forbidden(*args):self.fail('Do not touch saved documents')
        out=produce_checked_strategy(BRIEF,2,forbidden,lambda req,text:(True,''),
                                     existing=BAD,repair=forbidden)
        self.assertFalse(out[1]);self.assertEqual(out[0],BAD)

    def test_broken_repair_callback_rejected(self):
        with self.assertRaises(ValueError):
            produce_checked_strategy('Task',2,lambda a,b:'x',lambda a,b:(True,''),repair='oops')

    def test_compound_routes_entire_rejected_document_to_targeted_repair(self):
        from compound_tasks import execute_compound
        prompts=[]
        def fake_ask(model,system,user,*,stage='?',report=None):
            if stage == 'генерация SMM-стратегии':
                return BAD
            if stage == 'адресное исправление SMM-стратегии':
                prompts.append((system,user))
                return GOOD
            if stage == 'автоматическое исправление SMM-стратегии':
                return GOOD
            raise AssertionError('No model call expected at '+stage)
        request='Составь SMM-стратегию и контент-план на 1 неделю, по 2 публикации в неделю, всего 2 публикации. '+BRIEF
        with TemporaryDirectory() as temp, \
             patch('compound_tasks.analyze',return_value={'business':'языковые курсы','channels':'Instagram','channels_basis':'recommendation'}), \
             patch('compound_tasks._ask',side_effect=fake_ask), \
             patch('compound_tasks.validate_document',return_value=(True,'')), \
             patch('compound_tasks.plan_slots',return_value=[]), \
             patch('compound_tasks.govern_plan',return_value=([],{},True)):
            ctx={'project':'test','project_workspace':str(Path(temp)/'project'),
                 'category':'языковые курсы','description':request}
            output=execute_compound(request,ctx)
        self.assertEqual(len(prompts),1)
        self.assertIn(BAD_CADENCE_NORMALIZED,prompts[0][1])
        self.assertIn('ИСХОДНОЕ ЗАДАНИЕ',prompts[0][1])
        self.assertEqual(output['documents'][0]['content'],GOOD)
        self.assertTrue(output['documents'][0]['valid'])

if __name__=='__main__':unittest.main()
