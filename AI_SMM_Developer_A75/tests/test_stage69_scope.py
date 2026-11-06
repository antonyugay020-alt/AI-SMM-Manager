from plan_review_fixture import with_plan_review
"""Task-wide content completeness and regression tests, not fixed 39/39 tests."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import compound_tasks as ct
from content_requirements import publication_scope, delivery_report, requested_media_assets
from editorial_guard import editorial_risks
from semantic_brief import plan_slots
from agent_runtime import validate_document


def good_post(slot):
    ident=f"{slot['week']}/{slot['day']}"
    return dict(id=ident, topic=slot['topic'],
        caption=(f'Задача {ident}: оцените потребности, выясните условия использования и проверьте три конкретных признака. '
                 'Первый шаг — составить список требований. Второй — сопоставить варианты по ясным критериям. '
                 'Третий — проверить технические ограничения. Сравните результаты перед выбором. '
                 'Запишите свои выводы, чтобы принимать решение по требованиям, а не по рекламным обещаниям.'),
        visual=('Слайд 1: показать вопрос на экране. Слайд 2: три значимых критерия с иллюстрацией. '
                'Слайд 3: сравнение вариантов. Слайд 4: контрольная памятка.'),
        cta='Сохраните чек-лист до следующего выбора.', kpi='Сохранения',
        audience='Целевая аудитория из запроса', measurement='Instagram Insights',
        materials='Изготовить схемы и макеты для каждого сравнительного шага')


def mini_plan(ask, model, request, strategy, brief, weeks, per_week=3,
              on_week=None, existing_slots=None):
    days={2:['Вт','Пт'],3:['Пн','Ср','Пт'],4:['Пн','Вт','Чт','Пт']}[per_week]
    result=list(existing_slots or [])
    for week in range(len(result)//per_week+1, weeks+1):
        for day in days:
            result.append(dict(week=week,day=day,topic=f'Практический выбор для ситуации {week} {day}',
                               format='Карусель',platform='Instagram',goal='Польза',
                               audience='Аудитория',brief='Конкретное сравнение'))
        if on_week:on_week(week,result)
    return result


class Stage69ScopeTests(unittest.TestCase):
    def test_count_from_task_not_fixed(self):
        cases=((13,3,39), (4,2,8), (6,4,24), (2,1,2))
        for weeks,frequency,total in cases:
            with self.subTest(weeks=weeks,frequency=frequency):
                task=f'Создай стратегию и контент-план на {weeks} недель, по {frequency} публикации в неделю, всего {total} публикаций'
                scope=publication_scope(task)
                self.assertEqual((scope.weeks,scope.per_week,scope.total),(weeks,frequency,total))

    def test_inconsistent_task_not_executed(self):
        with self.assertRaisesRegex(ValueError,'Противоречивые'):
            publication_scope('4 недели, 2 публикации в неделю, всего 9 публикаций')
        with tempfile.TemporaryDirectory() as home:
            result=ct.execute_compound('Составь стратегию и контент-план на 4 недели, '
                                       'по 2 публикации в неделю, всего 9 публикаций',
                                       {'plan_reviewer_model':'independent-reviewer', **({'project_workspace':home})})
            self.assertTrue(result.get('error'))
            self.assertFalse(result['documents'])

    def test_dynamic_weekdays_and_restored_prefix(self):
        def ask(m, system, user):
            d=json.loads(user)
            week=d['week'];count=2
            return json.dumps({'posts':[{'topic':[['Подготовка списка условий покупки автомобиля','Проверка кузова машины перед осмотром'],['Планирование бюджета семейного отпуска','Проверка визового режима перед поездкой'],['Выбор материалов для изготовления шкафа','Оценка удобства кухни перед ремонтом'],['Выбор игрушки для домашней кошки','Обзор безопасности поводка для собаки']][week-1][i],
                                        'format':'Карусель','goal':'Объяснить различия'}
                                       for i in range(count)]},ensure_ascii=False)
        first=plan_slots(ask,'m','проект','стратегия',{'channels':'Instagram'},2,per_week=2)
        self.assertEqual([x['day'] for x in first], ['Вт','Пт']*2)
        more=plan_slots(ask,'m','проект','стратегия',{'channels':'Instagram'},4,
                        per_week=2,existing_slots=first)
        self.assertEqual(more[:4],first)
        self.assertEqual(len(more),8)

    def test_full_content_manifest_and_real_media_distinction(self):
        scope=publication_scope('2 недели, 2 публикации в неделю, всего 4 публикации')
        posts={f'1/{day}':good_post({'week':1,'day':day,'topic':'Работа с разными вводными'})
               for day in ['Вт','Пт']}
        documents=[dict(kind='strategy',valid=True,content='Описание стратегии'),
                   dict(kind='calendar',valid=False,content='Черновик')]
        report=delivery_report('Разработай стратегию, контент-план и создай фотографии для 2 недель, '
                               'по 2 публикации в неделю, всего 4 публикации',scope,documents,posts)
        self.assertEqual(report['checked_posts'],2)
        self.assertEqual(report['content_components_checked']['visual'],2)
        self.assertEqual(report['rendered_media'],'not_created')
        self.assertGreaterEqual(len(report['unfulfilled']),2)

    def test_no_media_inference_from_storyboard(self):
        self.assertFalse(requested_media_assets('Для Reels опиши, какие фото и видео запросить у владельца'))
        self.assertTrue(requested_media_assets('Создай изображения для всех публикаций'))

    def test_no_unverified_expert_service(self):
        request='Не придумывай ассортимент магазина, цены и дополнительные услуги.'
        self.assertTrue(editorial_risks({'cta':'Напишите и получите совет от эксперта!'},request))

    def test_strategy_depth_checks_original_requirements(self):
        req='разработай стратегию. Исходное задание: Нужны аудитория, бюджет, KPI и план действий'
        self.assertFalse(validate_document(req,'Стратегия продвижения. '*70)[0])
        good=('Цели и аудитория: план сегментирования. Позиционирование и каналы продвижения. '
              'KPI: измеряем обращения и конверсию. Бюджет распределяется на тестовые объявления. '
              'План действий: этапы внедрения по неделям и оценка реакции. ')*11
        self.assertTrue(validate_document(req,good)[0])

    def test_complete_strategy_is_not_rejected_by_arbitrary_1100_character_floor(self):
        req=('разработай стратегию. Исходное задание: Нужны аудитория, цели, площадка, '
             'метрики и план действий')
        body=(
            '# SMM-стратегия\n\n'
            '## Цели и задачи\nОбъяснить выбор и получить вопросы.\n\n'
            '## Целевая аудитория\nНачинающие клиенты, которым сложно выбрать.\n\n'
            '## Стратегическое направление\nДавать проверяемые ориентиры без обещаний.\n\n'
            '## Канал и продвижение\nПубликовать контент в Telegram и собирать отклик.\n\n'
            '## Контентные направления\nКритерии выбора и ответы на частые вопросы.\n\n'
            '## Метрики и измерение\nВопросы — считать входящие вопросы по публикациям.\n\n'
            '## План действий\nПодготовить темы, опубликовать их, оценить отклик.\n\n'
            '## Риски и данные для уточнения\nНеизвестны цены и адрес.')
        self.assertLess(len(body),1100)
        self.assertTrue(validate_document(req,body)[0])

    def test_strategy_mechanism_may_be_described_as_content_or_distribution(self):
        req='разработай стратегию. Исходное задание: Нужны аудитория, KPI и план действий'
        body=('Цели и аудитория: сегменты и потребности. Позиционирование и контентные направления. '
              'Публикации адаптируются под площадку и распределяются по форматам. KPI: обращения и охват. '
              'План действий: этапы внедрения по неделям. ')*35
        self.assertTrue(validate_document(req,body)[0])

    def test_dynamic_pipeline_4_posts_per_week(self):
        request='Разработай стратегию и контент-план на 2 недели, по 4 публикации в неделю, всего 8 публикаций'
        def llm(m,sys,usr,stage='-',report=None):
            if 'независимая редактура' in stage:
                return json.dumps(dict(approved=True,issues=[]))
            return '# Стратегия\n' + ('Цель — конкретное действие. Аудитория и каналы. '\
                       'Расходы и бюджет. KPI. Этапы реализации. '*40)
        with tempfile.TemporaryDirectory() as work:
            with patch.object(ct,'analyze',return_value={'channels':['Instagram']}), \
                 patch.object(ct,'_ask',side_effect=with_plan_review(llm)), \
                 patch.object(ct,'plan_slots',side_effect=mini_plan), \
                 patch.object(ct,'write_week',side_effect=lambda a,m,r,k,s,slots,t,feedback='':
                              [good_post(x) for x in slots]), \
                 patch.object(ct,'editorial_audit',return_value=[]), \
                 patch.object(ct,'copy_too_similar',return_value=False):
                result=ct.execute_compound(request, {'plan_reviewer_model':'independent-reviewer', **({'project_workspace':work})})
                self.assertTrue(result['documents'][-1]['valid'],result['documents'][-1]['reason'])
                self.assertEqual(result['content_delivery']['checked_posts'],8)
                self.assertEqual(result['content_delivery']['scope']['per_week'],4)
                self.assertFalse(result['content_delivery']['unfulfilled'])
                file=next((Path(work)/'drafts').glob('content_delivery_*.json'))
                self.assertEqual(json.loads(file.read_text(encoding="utf-8"))['checked_posts'],8)


    def test_safe_missing_audience_and_measurement_completed_from_slot(self):
        from editorial_guard import add_production_materials
        slot={'week':1,'day':'Вт','topic':'Проверка условий выбора','format':'Stories',
              'audience':'Молодые семьи'}
        result,changed=add_production_materials({'kpi':'Количество сохранений','caption':'Полезная информация',
                           'visual':'Экран 1: условия','materials':'Схема для экрана'},slot)
        self.assertTrue(changed)
        self.assertEqual(result['audience'],'Молодые семьи')
        self.assertIn('Instagram',result['measurement'])
        self.assertEqual(result['materials'],'Схема для экрана')
        # No business-specific metadata may be invented when the planner has none.
        other,_=add_production_materials({'kpi':'Выручка', 'materials':'Фото'},
                                        {'topic':'Тема','format':'Reels'})
        self.assertFalse(other.get('audience'))
        self.assertFalse(other.get('measurement'))

    def test_legacy_editor_checkpoint_is_preserved_without_implicit_reauditing(self):
        import hashlib
        from semantic_editor import POLICY_VERSION
        task='Разработай стратегию и контент-план на 2 недели, по 4 публикации в неделю, всего 8 публикаций'
        policy_checks=[]
        def llm(m,sys,usr,stage='-',report=None):
            if 'независимая редактура' in stage:
                policy_checks.append(stage)
                return json.dumps(dict(approved=True,issues=[]))
            return '# Стратегия\n' + ('Цели, аудитория, позиционирование, каналы, KPI, '
                   'бюджет и этапы реализации. ' * 40)
        with tempfile.TemporaryDirectory() as home:
            sig_source=home+'\n'+task+'\n2\n4'
            sig=hashlib.sha256(sig_source.encode('utf-8')).hexdigest()
            folder=Path(home)/'drafts';folder.mkdir()
            initial={}
            for w in (1,2):
                for d in ('Пн','Вт','Чт','Пт'):
                    slot={'week':w,'day':d,'topic':f'Практический выбор для ситуации {w} {d}',
                          'audience':'Аудитория','format':'Карусель'}
                    item=good_post(slot);item['_editorial_review']='semantic_editor_v1'
                    initial[item['id']]=item
            (folder/f'publication_progress_{sig[:16]}.json').write_text(
               json.dumps({'signature':sig,'total':8,'completed':8,'posts':initial}),encoding='utf-8')
            with patch.object(ct,'analyze',return_value={'channels':'Instagram','channels_basis':'recommendation'}), \
                 patch.object(ct,'_ask',side_effect=with_plan_review(llm)), \
                 patch.object(ct,'plan_slots',side_effect=mini_plan), \
                 patch.object(ct,'write_week',side_effect=AssertionError('old posts should be retained')), \
                 patch.object(ct,'copy_too_similar',return_value=False), \
                 patch.object(ct,'editorial_audit',return_value=[]):
                result=ct.execute_compound(task,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':home})})
            self.assertEqual(len(policy_checks),0)
            self.assertFalse(result['documents'][-1]['valid'])
            progress=json.loads((folder/f'publication_progress_{sig[:16]}.json').read_text(encoding="utf-8"))
            self.assertEqual(progress['completed'],8)
            self.assertEqual(progress['posts'],initial)  # Preserve approved bytes, flag stale policy separately

if __name__=='__main__':unittest.main()
