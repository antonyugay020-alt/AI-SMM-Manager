from plan_review_fixture import with_plan_review
"""Offline system integration tests: no GPU or network required."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from task_routing import resolve_request
from task_context import update_facts, load_facts
from semantic_brief import plan_slots
import compound_tasks as ct
import sqlite3


class SystemTests(unittest.TestCase):
    def test_clarification_and_new_task(self):
        pending='Создай стратегию и контент-план для магазина'
        self.assertEqual(resolve_request('Instagram',pending,'На какой площадке?')[1], 'clarification')
        self.assertIn('Instagram', resolve_request('Instagram',pending,'На какой площадке?')[0])
        self.assertEqual(resolve_request('Разработай стратегию и контент-план для кафе',pending,'На какой площадке?')[1], 'new')
        self.assertEqual(resolve_request('Какие документы готовы?',pending,'На какой площадке?')[1], 'new')
        self.assertEqual(resolve_request('Instagram',pending,'Часть документов не создана.')[1], 'new')

    def test_facts_are_isolated_on_new_task(self):
        db=sqlite3.connect(':memory:')
        db.execute('CREATE TABLE ai_task_facts (project_id TEXT PRIMARY KEY, facts_json TEXT NOT NULL)')
        update_facts(db,'p','Бизнес: магазин цветов\nБюджет: 120000 тенге')
        self.assertIn('магазин цветов',load_facts(db,'p')['business'])
        update_facts(db,'p','Бизнес: туристическое агентство',reset=True)
        facts=load_facts(db,'p')
        self.assertEqual(facts['business'],'туристическое агентство')
        self.assertNotIn('budget',facts)
        db.close()

    def test_partial_week_plan_saved_after_failure(self):
        saved=[]
        def ask(model,sys,user):
            week=json.loads(user)['week']
            if week == 2:
                return json.dumps({'posts':[{'topic':'Повторяющаяся тема о выборе товаров'}]*3})
            return json.dumps({'posts':[{'topic':f'Уникальная тема недели {week} номер {i} с пользой'} for i in range(3)]})
        with self.assertRaises(ValueError):
            plan_slots(ask,'m','новое задание','стратегия',{'channels':'Instagram','channels_basis':'recommendation'},3,on_week=lambda w,s:saved.append((w,list(s))))
        self.assertEqual(len(saved),1)
        self.assertEqual(len(saved[0][1]),3)

    def test_39_post_pipeline_and_resume(self):
        original='Разработай стратегию и контент-план на 13 недель, 3 публикации в неделю для тестового бизнеса'
        with tempfile.TemporaryDirectory() as td:
            ctx={'project_workspace':td,'project':'Тест','task_mode':'new'}
            def fake_slots(ask,model,request,strategy,brief,weeks,on_week=None,existing_slots=None):
                result=list(existing_slots or [])
                for week in range(len(result)//3+1,weeks+1):
                    for day in ('Пн','Ср','Пт'):
                        result.append({'week':week,'day':day,'topic':f'Уникальная тема {week} {day}', 'format':'Reels','platform':'Instagram','goal':'Информирование','audience':'Покупатели','brief':'Сопоставление критериев выбора'})
                    if on_week:on_week(week,result)
                return result
            calls=[]
            def fake_write(ask,model,request,known,strategy,pending,all_topics,feedback=''):
                calls.extend(s['week'] for s in pending)
                return [{'id':f"{s['week']}/{s['day']}",'topic':s['topic'],'caption':(f'Практический текст для {s["week"]}/{s["day"]}. ' + 'Практический совет. '*50),'visual':'Кадр 1. Практический пример','cta':'Сохраните','kpi':'Сохранения','measurement':'Статистика Instagram','audience':'Покупатели','materials':'Съёмка'} for s in pending]
            patches=[patch.object(ct,'analyze',return_value={'channels':'Instagram'}),
                     patch.object(ct,'_ask',side_effect=with_plan_review('# Стратегия\n'+'Раздел и пояснения. '*150)),
                     patch.object(ct,'validate_document',return_value=(True,'')),
                     patch.object(ct,'plan_slots',side_effect=fake_slots),
                     patch.object(ct,'write_week',side_effect=fake_write),
                     patch.object(ct,'validate_single',return_value=[]),
                     patch.object(ct,'critical_issues',return_value=[]),
                     patch.object(ct,'editorial_audit',return_value=[]),
                     patch.object(ct,'copy_too_similar',return_value=False),
                     patch.object(ct,'audit_post',side_effect=lambda ask,model,request,facts,slot,post,previous_posts=(): (post,[]))]
            from contextlib import ExitStack
            with ExitStack() as stack:
                for p in patches:stack.enter_context(p)
                first=ct.execute_compound(original,{'plan_reviewer_model':'independent-reviewer', **(ctx)})
                self.assertTrue(first['documents'][1]['valid'])
                self.assertEqual(len(calls),39)
                progress=list((Path(td)/'drafts').glob('publication_progress_*.json'))[0]
                self.assertEqual(json.loads(progress.read_text(encoding="utf-8"))['completed'],39)
                calls.clear()
                second=ct.execute_compound(original,{'plan_reviewer_model':'independent-reviewer', **(ctx)})
                self.assertEqual(calls,[],'resume must not regenerate accepted posts')
                self.assertTrue(second['documents'][1]['valid'])
                other=ct.execute_compound(original+' для другого бизнеса',{'plan_reviewer_model':'independent-reviewer', **(ctx)})
                self.assertEqual(len(calls),39,'new request must not inherit posts')
                self.assertEqual(len(list((Path(td)/'drafts').glob('publication_progress_*.json'))),2)

if __name__=='__main__': unittest.main()
