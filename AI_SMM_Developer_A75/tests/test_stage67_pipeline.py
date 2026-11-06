from plan_review_fixture import with_plan_review
"""Offline integration tests for Stage 67 task continuity and honesty."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import compound_tasks as ct
from semantic_brief import plan_slots, topic_repeats


REQUEST = ('Разработай стратегию и контент-план для малого бизнеса на 13 недель, '
           '39 публикаций, по 3 в неделю. Не выдумывай цены или услуги.')


def generate_slots(ask, model, request, strategy, brief, weeks, on_week=None, existing_slots=None):
    slots = list(existing_slots or [])
    for week in range(len(slots)//3+1, weeks+1):
        if week == 8 and 'FAIL_WEEK_EIGHT' in request:
            raise ValueError('Неделя 8: модель повторила уже принятую тему')
        for d in ('Пн', 'Ср', 'Пт'):
            slots.append(dict(week=week, day=d, topic=f'Тема про реальный полезный выбор {week} {d}',
                              brief='Проверяемый подход без вымышленных сведений',
                              format='Карусель', platform='Instagram',
                              goal='Обучение', audience='Покупатели'))
        if on_week: on_week(week, slots)
    return slots


def fake_write(ask, model, request, known, strategy, slots, all_topics, feedback=''):
    return [dict(id=f"{s['week']}/{s['day']}", topic=s['topic'],
                 caption=('Для этой ситуации проверьте исходные условия и сравните несколько критериев. '
                          + f"Вариант {s['week']} {s['day']}: "
                          + 'Уточните характеристики по подтверждённым материалам. '
                          + 'Посмотрите состав, размеры и ограничения применения перед решением. '*4),
                 visual=('Слайд 1: ключевой вопрос. Слайд 2: критерий качества с пояснением. '
                         'Слайд 3: способ проверки. Слайд 4: честный вывод.'),
                 cta='Сохраните памятку для дальнейшего сравнения.',
                 kpi='Количество сохранений', measurement='Статистика Instagram',
                 audience='Покупатели', materials='Схемы и подтверждённые характеристики')
            for s in slots]


class Stage67PipelineTests(unittest.TestCase):
    def test_adaptive_sampling_changes_only_topic_planning(self):
        with patch.object(ct,'request_chat', return_value={'message':{'content':'{}'}}) as client:
            ct._ask('model','JSON','request',stage='планирование тем')
            self.assertEqual(client.call_args.args[0]['options']['temperature'],0.55)
            ct._ask('model','JSON','request',stage='генерация публикаций')
            self.assertEqual(client.call_args.args[0]['options']['temperature'],0.25)

    def test_lexical_duplicate_and_related_topics(self):
        self.assertTrue(topic_repeats('Как подготовить дом к питомцу с ограниченными возможностями',
                                      ['Как подготовить дом к появлению питомца с ограниченными возможностями']))
        self.assertFalse(topic_repeats('Как выбирать игрушки для собаки', ['Как подготовить дом к появлению кошки']))

    def test_planner_rejects_repeating_model_without_fake_topics(self):
        saved = []
        def ask(model, system, user):
            meta = json.loads(user)
            week = meta['week']
            if week == 2:
                return json.dumps({'posts':[{'topic':'Как проверить техническое состояние до покупки'}]*3})
            return json.dumps({'posts':[{'topic':['Проверка безопасности при покупке', 'Сравнение вариантов самостоятельного применения', 'Измерение удобства без ложных обещаний'][i] + f' {week}'} for i in range(3)]})
        with self.assertRaisesRegex(ValueError, 'Неделя 2'):
            plan_slots(ask, 'model', 'Проверка', 'Стратегия', {'channels':'Instagram','channels_basis':'recommendation'}, 3, on_week=lambda w,s:saved.append(len(s)))
        self.assertEqual(saved, [3])

    def test_partial_plan_creates_and_checkpoints_real_posts(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(ct,'analyze',return_value={'channels':'Instagram'}), \
                 patch.object(ct,'_ask',side_effect=with_plan_review('# Стратегия\n'+'Подробная стратегия. '*160)), \
                 patch.object(ct,'validate_document',return_value=(True,'')), \
                 patch.object(ct,'plan_slots',side_effect=generate_slots), \
                 patch.object(ct,'write_week',side_effect=fake_write), \
                 patch.object(ct,'editorial_audit',side_effect=lambda *a, **k: []), \
                 patch.object(ct,'copy_too_similar',return_value=False), \
                 patch.object(ct,'audit_post',side_effect=lambda ask,model,request,facts,slot,post,previous_posts=(): (post,[])):
                result=ct.execute_compound(REQUEST+' FAIL_WEEK_EIGHT',{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':td})})
            docs=result['documents']
            self.assertTrue(docs[0]['valid'])
            self.assertFalse(docs[1]['valid'])
            self.assertIn('Неделя 8', docs[1]['reason'])
            self.assertIn('Неделя 8 — ещё не спланирована', docs[1]['content'])
            self.assertIn('Неделя 1', docs[1]['content'])
            progress=next((Path(td)/'drafts').glob('publication_progress_*.json'))
            data=json.loads(progress.read_text(encoding='utf8'))
            self.assertEqual(data['completed'],21)
            self.assertEqual(len(data['posts']),21)
            trace=next((Path(td)/'drafts').glob('publication_diagnostics_*.jsonl')).read_text(encoding='utf8')
            self.assertIn('topic_planning_degraded',trace)
            self.assertIn('week_done',trace)
            self.assertIn('calendar_validation',trace)

    def test_resume_reuses_all_verified_posts_after_planner_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(ct,'analyze',return_value={'channels':'Instagram'}), \
                 patch.object(ct,'_ask',side_effect=with_plan_review('# Стратегия\n'+'Раздел стратегии. '*160)), \
                 patch.object(ct,'validate_document',return_value=(True,'')), \
                 patch.object(ct,'write_week',side_effect=fake_write), \
                 patch.object(ct,'editorial_audit',side_effect=lambda *a, **k: []), \
                 patch.object(ct,'copy_too_similar',return_value=False), \
                 patch.object(ct,'audit_post',side_effect=lambda ask,model,request,facts,slot,post,previous_posts=(): (post,[])):
                original=generate_slots
                attempts={'n':0}
                def recover(*args,**kwargs):
                    attempts['n']+=1
                    if attempts['n']==1:
                        return original(*args[:3], args[3],args[4],args[5],on_week=kwargs['on_week'],existing_slots=kwargs['existing_slots'])
                    return original(*args,**kwargs)
                with patch.object(ct,'plan_slots',side_effect=recover), \
                     patch.object(ct,'write_week',wraps=fake_write) as writer:
                    bad_request=REQUEST+' FAIL_WEEK_EIGHT'
                    ct.execute_compound(bad_request,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':td})})
                    self.assertEqual(writer.call_count,7)
                    # Same exact task signature, but now the model is healthy.
                    def healthy(*args,**kwargs):
                        return original(*args[:3],args[3],args[4],args[5],
                                        on_week=kwargs['on_week'], existing_slots=kwargs['existing_slots'],)
                    # Simulate recovery by removing synthetic fault from the function behavior.
                    def no_fault(*args, **kwargs):
                        p=list(args)
                        p[2]=p[2].replace('FAIL_WEEK_EIGHT','')
                        return original(*p,**kwargs)
                    with patch.object(ct,'plan_slots',side_effect=no_fault):
                        result=ct.execute_compound(bad_request,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':td})})
                    self.assertEqual(writer.call_count,13, 'Only six missing weeks may be regenerated')
                    self.assertTrue(result['documents'][-1]['valid'])
                    progress=next((Path(td)/'drafts').glob('publication_progress_*.json'))
                    self.assertEqual(json.loads(progress.read_text(encoding='utf8'))['completed'],39)

if __name__ == '__main__': unittest.main()
