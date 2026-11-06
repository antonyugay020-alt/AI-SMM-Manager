"""Regression tests for task isolation, clarification and persistent checkpoints."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from task_routing import resolve_request
from task_context import update_facts, load_facts
from semantic_brief import plan_slots

class LifecycleRegression(unittest.TestCase):
    def test_pending_cannot_replace_new_business(self):
        old='Разработай стратегию и контент-план для автодетейлинга'
        new='Разработай стратегию и контент-план для туристического агентства'
        self.assertEqual(resolve_request(new,old,'Часть документов не создана'),(new,'new'))
    def test_explicit_resume(self):
        self.assertEqual(resolve_request('продолжи задание','old')[0],'old')
    def test_clarification_requires_real_question(self):
        self.assertEqual(resolve_request('Instagram','old','На какой площадке?')[1],'clarification')
        self.assertEqual(resolve_request('Instagram','old','Часть документов не создана.')[1],'new')
    def test_new_task_facts_reset(self):
        db=sqlite3.connect(':memory:')
        db.execute('CREATE TABLE ai_task_facts (project_id TEXT PRIMARY KEY, facts_json TEXT NOT NULL)')
        update_facts(db,'p','Бизнес: автодетейлинг\nБюджет: 150000 тенге')
        update_facts(db,'p','Бизнес: туристическое агентство\nБюджет: 160000 тенге',reset=True)
        self.assertEqual(load_facts(db,'p')['business'],'туристическое агентство')
        self.assertEqual(load_facts(db,'p')['budget'],'160000 тенге')
        db.close()
    def test_checkpoint_skips_valid_weeks(self):
        def ask(model,system,user):
            week=json.loads(user)['week']
            return json.dumps({'posts':[{'topic':f'Уникальный полезный материал {week} пункт {i}'} for i in range(3)]})
        brief={'channels':'Instagram','channels_basis':'recommendation'}
        prior=plan_slots(ask,'model','request','strategy',brief,3)
        calls=[]
        def ask_next(model,system,user):
            calls.append(json.loads(user)['week'])
            return ask(model,system,user)
        result=plan_slots(ask_next,'model','request','strategy',brief,5,existing_slots=prior)
        self.assertEqual(calls,[4,5]);self.assertEqual(len(result),15)

if __name__=='__main__':unittest.main()
