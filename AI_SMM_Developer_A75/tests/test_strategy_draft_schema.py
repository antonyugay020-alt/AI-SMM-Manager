import json
import unittest

from compound_tasks import _STRATEGY_DRAFT_SCHEMA, _render_strategy_draft


class StructuredStrategyDraftTests(unittest.TestCase):
    def valid_payload(self):
        return {
            'objectives':[
                'Сформулировать задачи коммуникации на основе цели и исходных ограничений проекта.',
                'Определить, какие действия аудитории можно измерять без произвольных числовых обещаний.'
            ],
            'audience':'Описать только сегменты и потребности, которые пользователь прямо сообщил в задании.',
            'strategy_direction':'Рекомендовать проверяемые направления коммуникации и явно обозначать их как гипотезы.',
            'channel_plan':'Работать только с указанной площадкой и не приписывать ей неподтверждённые свойства.',
            'content_pillars':[
                'Публиковать образовательные материалы, связанные с подтверждёнными услугами проекта.',
                'Проверять вовлекающие вопросы по соответствию цели и потребностям аудитории.',
                'Использовать отдельные тематические рубрики без выдуманных отзывов и результатов.'
            ],
            'metrics':[
                {'name':'Охват','method':'Сверять охват публикаций по доступной статистике выбранной площадки.'},
                {'name':'Вовлечённость','method':'Считать реакции и комментарии относительно охвата публикаций.'},
                {'name':'Входящие запросы','method':'Фиксировать число относящихся к теме сообщений за период.'}
            ],
            'implementation_steps':[
                'Уточнить факты о проекте и ограничения, которые должны соблюдаться в публикациях.',
                'Подготовить несколько тем и проверить каждую на соответствие цели и аудитории.',
                'Создать тексты и визуальные задания без заявлений о неподтверждённых услугах.',
                'После публикации собрать значения метрик и сравнить их с выбранной исходной базой.'
            ],
            'risks_unknowns':[
                'Неизвестные условия проекта нужно уточнить до того, как превращать их в обещания.',
                'Без исторических данных нельзя назначить обоснованные целевые значения показателей.'
            ]
        }

    def test_schema_renderer_produces_all_strategy_sections(self):
        payload=self.valid_payload()
        self.assertLessEqual(set(_STRATEGY_DRAFT_SCHEMA['required']),set(payload))
        rendered=_render_strategy_draft(json.dumps(payload,ensure_ascii=False))
        for heading in ('Цели и задачи','Целевая аудитория','Стратегическое направление',
                        'Канал и продвижение','Контентные направления',
                        'Метрики и измерение','План действий','Риски и данные для уточнения'):
            self.assertIn(heading,rendered)
        self.assertGreater(len(rendered),1100)

    def test_incomplete_or_unstructured_response_is_rejected(self):
        with self.assertRaises(ValueError):
            _render_strategy_draft('обычный текст без структурированных разделов')
        payload=self.valid_payload()
        payload['metrics']=[]
        with self.assertRaises(ValueError):
            _render_strategy_draft(json.dumps(payload,ensure_ascii=False))

    def test_concise_local_model_fields_pass_shape_validation_for_semantic_review(self):
        payload={
            'objectives':['Получить вопросы','Объяснить подготовку'],
            'audience':'Взрослые новички',
            'strategy_direction':'Публиковать полезные пояснения',
            'channel_plan':'Вести рубрику ВКонтакте',
            'content_pillars':['Первый урок','Частые вопросы','Словарные подсказки'],
            'metrics':[
                {'name':'Вопросы','method':'Подсчёт сообщений и комментариев'},
                {'name':'Охват','method':'Сверка охвата в статистике'},
                {'name':'Реакции','method':'Подсчёт реакций публикаций'}],
            'implementation_steps':['Выбрать тему','Подготовить текст','Добавить визуал','Проверить отклик'],
            'risks_unknowns':['Стоимость не указана','Расписание не указано']}
        rendered=_render_strategy_draft(json.dumps(payload,ensure_ascii=False))
        self.assertIn('Взрослые новички',rendered)
        self.assertIn('Стоимость не указана',rendered)

    def test_channel_name_only_is_preserved_for_independent_usefulness_review(self):
        payload=self.valid_payload()
        payload['channel_plan']='Instagram'
        rendered=_render_strategy_draft(json.dumps(payload,ensure_ascii=False),
                                        channel_context='Instagram')
        self.assertIn('## Канал и продвижение\nInstagram',rendered)
        self.assertNotIn('Планировать публикации',rendered)

    def test_channel_autofill_does_not_replace_nontrivial_model_plan(self):
        payload=self.valid_payload()
        payload['channel_plan']='Вести Instagram по заданной частоте и оценивать отклик по доступным показателям.'
        rendered=_render_strategy_draft(json.dumps(payload,ensure_ascii=False),
                                        channel_context='Instagram')
        self.assertIn(payload['channel_plan'],rendered)

    def test_provider_equivalent_shapes_compile_to_the_same_strategy_contract(self):
        payload={
            'project_name':'Проверяемый проект',
            'task_analysis':{'unknowns':['стоимость']},
            'objectives':['Сформировать реалистичные ожидания до осмотра техники'],
            'audience':[{'segment':'Владельцы неисправной техники',
                         'needs':'Понять порядок первичного осмотра'}],
            'strategy_direction':['Объяснять последовательность диагностики и согласования'],
            'channel_plan':{'platform':'ВКонтакте',
                            'approach':'Публиковать информационные посты в сообществе'},
            'content_pillars':[{'pillar':'Процесс работы',
                                'description':'Разъяснение этапов первичного осмотра'}],
            'metrics':[{'metric':'Запросы в сообщения',
                        'measurement':'Подсчет входящих сообщений в статистике'}],
            'implementation_steps':['Подготовить два информационных поста'],
            'risks_unknowns':['Неизвестна стоимость диагностики'],
            'materials':['Подтвержденный прайс-лист'],
        }
        rendered=_render_strategy_draft(json.dumps(payload,ensure_ascii=False))
        self.assertIn('Владельцы неисправной техники',rendered)
        self.assertIn('Запросы в сообщения',rendered)
        self.assertIn('Подсчет входящих сообщений в статистике',rendered)
        self.assertIn('Неизвестна стоимость диагностики',rendered)
        self.assertNotIn('Проверяемый проект',rendered)

    def test_strategy_does_not_require_arbitrary_three_metrics_or_two_risks(self):
        payload=self.valid_payload()
        payload['objectives']=payload['objectives'][:1]
        payload['content_pillars']=payload['content_pillars'][:1]
        payload['metrics']=payload['metrics'][:1]
        payload.pop('risks_unknowns')
        rendered=_render_strategy_draft(json.dumps(payload,ensure_ascii=False))
        self.assertIn('## Метрики и измерение',rendered)
        self.assertIn('## План действий',rendered)
        self.assertNotIn('## Риски и данные для уточнения',rendered)

    def test_dynamic_platform_key_in_channel_mapping_is_preserved(self):
        payload=self.valid_payload()
        payload['channel_plan']={
            'instagram':'Публиковать визуальные материалы и принимать вопросы в комментариях.'}
        rendered=_render_strategy_draft(json.dumps(payload,ensure_ascii=False),
                                        channel_context='Instagram')
        self.assertIn('instagram:',rendered.casefold())
        self.assertIn('принимать вопросы в комментариях',rendered)


if __name__=='__main__':
    unittest.main()
