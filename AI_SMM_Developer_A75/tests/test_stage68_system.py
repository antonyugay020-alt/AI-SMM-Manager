from plan_review_fixture import with_plan_review
"""Stage 68 release gates: quality, metadata, task isolation, recovery, refusal."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import compound_tasks as ct
from editorial_guard import add_production_materials, editorial_risks, copy_too_similar
from semantic_editor import audit_post
from semantic_brief import _platform_name


REQUEST = ('Разработай стратегию и контент-план на 13 недель: 39 публикаций, 3 в неделю. '
           'Не придумывай адрес, цены, гарантии, перечень реально оказываемых услуг.')


def slots(ask, model, request, strategy, brief, weeks, on_week=None, existing_slots=None):
    all_slots = list(existing_slots or [])
    for w in range(len(all_slots)//3 + 1, weeks + 1):
        for d in ('Пн','Ср','Пт'):
            all_slots.append({'week':w, 'day':d, 'topic':f'Практическая разборная тема {w} {d}',
                              'brief':'Сравнение возможностей', 'format':'Reels',
                              'goal':'Полезный разбор', 'audience':'Покупатели', 'platform':'Instagram'})
        if on_week: on_week(w, all_slots)
    return all_slots


def writer(ask, model, request, known, strategy, subset, all_topics, feedback=''):
    results = []
    for item in subset:
        ident=f"{item['week']}/{item['day']}"
        results.append({'id':ident, 'topic':item['topic'],
                        'caption':(f"Тема {ident}: {item['topic']}. " + 'Перед выбором оцените условия использования и ограничения. '
                                   'Не делайте выводы по одной фотографии; сравните ключевые параметры и '
                                   'задайте уточняющие вопросы. Проверьте факты в актуальных документах. '*3),
                        'visual':('Сцена 1: показать сравниваемые параметры на нейтральных макетах. '
                                  'Сцена 2: крупный план списка вопросов. Сцена 3: финальная таблица.'),
                        'cta':'Сохраните список проверок для самостоятельного сравнения.',
                        'kpi':'Сохранения', 'measurement':'Данные Instagram', 'audience':'Покупатели',
                        'materials':''})
    return results


class Stage68SystemTests(unittest.TestCase):
    def test_metadata_completion_never_claims_existing_images(self):
        post, filled = add_production_materials({'materials':''},
                        {'topic':'Выбор ухода за тканью','format':'Reels'})
        self.assertTrue(filled)
        self.assertIn('Необходимо подготовить',post['materials'])
        self.assertIn('с владельцем',post['materials'])
        self.assertNotIn('у нас есть',post['materials'].casefold())

    def test_risks_from_real_stage67_patterns(self):
        self.assertTrue(editorial_risks({'caption':'Сушите джинсы на батарее, чтобы не выцветали.'}, REQUEST))
        self.assertTrue(editorial_risks({'caption':'Нужна помощь с подгонкой? Мы поможем.'}, REQUEST))
        self.assertFalse(editorial_risks({'caption':'Способ ухода проверяйте по ярлыку конкретной вещи.'}, REQUEST))

    def test_near_duplicate_copy_rejected(self):
        cap='Практический разбор размера изделия; три подробных шага по выбору и проверке износа. '*6
        changed=cap.replace('размера','параметра').replace('износа','состава')
        self.assertTrue(copy_too_similar(changed,[cap]))
        self.assertFalse(copy_too_similar('Уникальный вопрос о цвете', ['Совершенно другой вопрос о форме']))

    def test_editor_fails_closed_when_response_broken(self):
        args=('model',REQUEST,{}, {'week':1,'day':'Пн','topic':'Сравнение'},
              {'id':'1/Пн','topic':'Сравнение'})
        self.assertTrue(audit_post(lambda *a: 'not json',*args)[1])
        self.assertTrue(audit_post(lambda *a: '{"approved":"yes"}',*args)[1])
        self.assertTrue(audit_post(lambda *a: '{"approved":false,"issues":["фактический риск"]}',*args)[1])
        good, problems=audit_post(lambda *a: '{"approved":true,"issues":[],"revised_post":null}',*args)
        self.assertFalse(problems)
        self.assertEqual(good['id'],'1/Пн')
        inconsistent='{"approved":true,"issues":["нерешённый риск"],"revised_post":null}'
        self.assertTrue(audit_post(lambda *a: inconsistent,*args)[1])
        wrong='{"approved":true,"issues":[],"revised_post":{"id":"2/Пт","topic":"Новая тема","caption":"Исправленный текст"}}'
        normalized, problems = audit_post(lambda *a: wrong,*args)
        self.assertIsNone(normalized)
        self.assertTrue(problems)
        self.assertIn('собственную правку',problems[0])

    def test_channels_array_display_normalization(self):
        self.assertEqual(_platform_name(['Instagram']), 'Instagram')
        self.assertEqual(_platform_name(['Instagram','VK']),'Instagram, VK')

    def test_all_39_persist_and_resume_even_without_model_materials(self):
        calls=[]
        def counted(*args,**kwargs):
            calls.extend(f"{x['week']}/{x['day']}" for x in args[5]);return writer(*args,**kwargs)
        def mock_llm(model,system,user,stage='x',report=None):
            if 'независимая редактура' in stage:
                return '{"approved":true,"issues":[]}'
            return '# Стратегия\n' + 'Проверенный фрагмент стратегии. '*130
        with tempfile.TemporaryDirectory() as folder:
            ctx={'project_workspace':folder}
            with patch.object(ct,'analyze',return_value={'channels':['Instagram']}), \
                 patch.object(ct,'_ask',side_effect=with_plan_review(mock_llm)), \
                 patch.object(ct,'validate_document',return_value=(True,'')), \
                 patch.object(ct,'plan_slots',side_effect=slots), \
                 patch.object(ct,'write_week',side_effect=counted), \
                 patch.object(ct,'copy_too_similar',return_value=False), \
                 patch.object(ct,'editorial_audit',side_effect=lambda *a,**k:[]):
                first=ct.execute_compound(REQUEST,{'plan_reviewer_model':'independent-reviewer', **(ctx)})
                self.assertTrue(first['documents'][-1]['valid'])
                self.assertEqual(len(calls),39)
                checkpoint=next((Path(folder)/'drafts').glob('publication_progress_*.json'))
                data=json.loads(checkpoint.read_text(encoding='utf-8'))
                self.assertEqual(data['completed'],39)
                self.assertTrue(all(p['materials'] for p in data['posts'].values()))
                self.assertTrue(all(p['_editorial_review'] == 'semantic_editor_v1' for p in data['posts'].values()))
                calls.clear()
                second=ct.execute_compound(REQUEST,{'plan_reviewer_model':'independent-reviewer', **(ctx)})
                self.assertTrue(second['documents'][-1]['valid'])
                self.assertFalse(calls)
                third=ct.execute_compound(REQUEST+' Новый бизнес',{'plan_reviewer_model':'independent-reviewer', **(ctx)})
                self.assertTrue(third['documents'][-1]['valid'])
                self.assertEqual(len(calls),39)
                self.assertEqual(len(list((Path(folder)/'drafts').glob('publication_progress_*.json'))),2)

    def test_duplicate_model_ids_reconciled_by_exact_topic_and_partial_resume(self):
        # Three model outputs may all reuse the same id; exact topics are the
        # trustworthy slot keys. The editor refuses one post on the first run.
        attempts = {'runs':0,'batch_ids':[]}
        def shuffled(ask,model,request,known,strategy,subset,all_topics,feedback=''):
            attempts['batch_ids'].extend(f"{s['week']}/{s['day']}" for s in subset)
            posts=writer(ask,model,request,known,strategy,subset,all_topics,feedback)
            for p in posts:
                p['id']=f"{subset[0]['week']}/Ср"  # wrong and duplicated
            return posts
        def check(ask, model, request, facts, slot, post, previous_posts=()):
            if attempts['runs']==0 and slot['week']==4 and slot['day']=='Пт':
                return None,['Требуются подтверждённые исходные факты']
            return post,[]
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(ct,'analyze',return_value={'channels':'Instagram','channels_basis':'recommendation'}), \
                 patch.object(ct,'_ask',side_effect=with_plan_review('Стратегия '*350)), \
                 patch.object(ct,'validate_document',return_value=(True,'')), \
                 patch.object(ct,'plan_slots',side_effect=slots), \
                 patch.object(ct,'write_week',side_effect=shuffled), \
                 patch.object(ct,'write_single',side_effect=ValueError('Ожидаем повторный запуск')), \
                 patch.object(ct,'audit_post',side_effect=check), \
                 patch.object(ct,'copy_too_similar',return_value=False), \
                 patch.object(ct,'editorial_audit',side_effect=lambda *a,**k:[]):
                first=ct.execute_compound(REQUEST, {'plan_reviewer_model':'independent-reviewer', **({'project_workspace':folder})})
                self.assertFalse(first['documents'][-1]['valid'])
                checkpoint=next((Path(folder)/'drafts').glob('publication_progress_*.json'))
                data=json.loads(checkpoint.read_text(encoding='utf-8'))
                self.assertEqual(data['completed'],38)
                self.assertNotIn('4/Пт',data['posts'])
                attempts['batch_ids'].clear()
                attempts['runs']=1
                second=ct.execute_compound(REQUEST, {'plan_reviewer_model':'independent-reviewer', **({'project_workspace':folder})})
                self.assertTrue(second['documents'][-1]['valid'], second['documents'][-1]['reason'])
                self.assertEqual(attempts['batch_ids'], ['4/Пт'])
                self.assertEqual(json.loads(checkpoint.read_text(encoding='utf-8'))['completed'],39)
                log=next((Path(folder)/'drafts').glob('publication_diagnostics_*.jsonl')).read_text(encoding='utf-8')
                self.assertIn('id_reconciled',log)

    def test_editor_rejection_cannot_be_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(ct,'analyze',return_value={'channels':'Instagram'}), \
                 patch.object(ct,'_ask',side_effect=with_plan_review('Стратегия '*350)), \
                 patch.object(ct,'validate_document',return_value=(True,'')), \
                 patch.object(ct,'plan_slots',side_effect=slots), \
                 patch.object(ct,'write_week',side_effect=writer), \
                 patch.object(ct,'write_single',side_effect=ValueError('нет ответа')), \
                 patch.object(ct,'copy_too_similar',return_value=False), \
                 patch.object(ct,'audit_post',return_value=(None,['Содержит ложное обещание'])), \
                 patch.object(ct,'editorial_audit',side_effect=lambda *a,**k:[]):
                result=ct.execute_compound(REQUEST,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':folder})})
            self.assertFalse(result['documents'][-1]['valid'])
            progress=next((Path(folder)/'drafts').glob('publication_progress_*.json'))
            self.assertEqual(json.loads(progress.read_text(encoding="utf-8"))['completed'],0)

if __name__=='__main__':unittest.main()
