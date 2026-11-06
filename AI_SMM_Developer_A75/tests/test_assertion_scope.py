import unittest

from assertion_scope import classify_obvious_non_business


class AssertionScopeTests(unittest.TestCase):
    def test_strategy_intent_in_narrative_is_not_business_fact(self):
        item = {
            'quote': 'Данная SMM-стратегия направлена на повышение осведомленности аудитории.',
            'field_role': 'Заключение',
            'field_type': 'document_narrative',
        }
        result = classify_obvious_non_business(item)
        self.assertEqual(result['statement_class'], 'planning_constraint')

    def test_channel_recommendation_from_r19_gui_is_planning_not_a_business_fact(self):
        item = {
            'quote': 'Работа с ВКонтакте направлена на построение доверительных отношений через тексты и визуалы.',
            'field_role': 'Канал и продвижение',
            'field_type': 'document_narrative',
            'section_path': ['Стратегия SMM', 'Канал и продвижение'],
        }
        result = classify_obvious_non_business(item)
        self.assertEqual(result['statement_class'], 'planning_constraint')

    def test_kpi_channel_mechanics_and_unknown_preparation_notes_are_not_business_claims(self):
        cases=(
            ('- **Количество запросов в Direct** — подсчет сообщений с вопросами о консультации',
             'Метрики и KPI','Цели и измерение'),
            ('ВКонтакте — платформа, на которой можно достичь целевой аудитории и показать контент',
             'Каналы и обоснование','Каналы'),
            ('Instagram: использование визуального формата для демонстрации признаков износа и текстовых пояснений для привлечения внимания аудитории',
             'Каналы и обоснование','Каналы'),
            ('Описание первого занятия в студии керамики (необходимо уточнить подтвержденную информацию)',
             'Материалы для подготовки','Ресурсы'),
            ('Мониторинг комментариев и ответы на вопросы аудитории',
             'План реализации','Стратегия'),
        )
        for quote,role,section in cases:
            with self.subTest(quote=quote):
                result=classify_obvious_non_business({'quote':quote,'field_role':role,
                    'field_type':'document_narrative','section_path':['SMM стратегия',section]})
                self.assertEqual(result['statement_class'],'planning_constraint')

    def test_markdown_metric_label_for_interactions_is_a_measurement_not_a_business_claim(self):
        result=classify_obvious_non_business({
            'quote':'- **Количество взаимодействий** — Instagram Insights',
            'field_type':'document_narrative',
            'section_path':['Стратегия SMM','Метрики и измерение'],
        })
        self.assertEqual(result['statement_class'],'planning_constraint')
        for quote in ('Мы отвечаем на все обращения в течение часа.',
                      'Компания гарантирует рост числа обращений.'):
            with self.subTest(quote=quote):
                self.assertIsNone(classify_obvious_non_business({
                    'quote':quote,'field_type':'document_narrative',
                    'section_path':['Стратегия SMM','Метрики и измерение']}))
        for quote in ('Мы ответим на вопросы в Direct и запишем вас на консультацию.',
                      'В мастерской всегда ремонтируют любую модель обуви.'):
            with self.subTest(quote=quote):
                self.assertIsNone(classify_obvious_non_business({'quote':quote,
                    'field_type':'document_narrative','section_path':['Метрики и KPI']}))

    def test_strategy_recommendations_with_channel_labels_are_not_company_process_claims(self):
        cases=(
            ('ВКонтакте: использовать текстовые посты с иллюстрациями и короткие видео-инструкции.',
             'Канал и продвижение'),
            ('Тестировать два подхода к объяснению процесса диагностики.',
             'Стратегическое направление'),
            ('Взаимодействовать через комментарии и личные сообщения.',
             'Канал и продвижение'),
        )
        for text,role in cases:
            with self.subTest(text=text):
                result=classify_obvious_non_business({'quote':text,'field_role':role,
                    'field_type':'document_narrative','section_path':['Стратегия SMM',role]})
                self.assertEqual(result['statement_class'],'planning_constraint')
        for text in ('Мы отвечаем на вопросы в комментариях.',
                     'В мастерской выполняют ремонт любой сложности.'):
            with self.subTest(text=text):
                self.assertIsNone(classify_obvious_non_business({'quote':text,
                    'field_type':'document_narrative','section_path':['Стратегия SMM','Канал и продвижение']}))

    def test_channel_usage_and_content_distribution_are_planning_but_company_actions_remain_reviewable(self):
        cases = (
            'Использование Instagram для предоставления информации о процессе ухода и ответов на вопросы владельцев',
            'Использование Instagram для демонстрации процесса ухода и ответов на вопросы владельцев',
            'Instagram: публикация постов для охвата местных жителей и прием запросов через личные сообщения',
        )
        for text in cases:
            with self.subTest(text=text):
                result = classify_obvious_non_business({
                    'quote': text,
                    'field_type': 'document_narrative',
                    'field_role': 'Каналы и продвижение',
                    'section_path': ['Стратегия SMM', 'Каналы и продвижение'],
                })
                self.assertEqual(result['statement_class'], 'planning_constraint')
        for text in (
            'Компания публикует в Instagram материалы об уходе и отвечает на вопросы владельцев.',
            'Мы ответим на вопросы владельцев в Instagram.',
        ):
            with self.subTest(text=text):
                self.assertIsNone(classify_obvious_non_business({
                    'quote': text,
                    'field_type': 'document_narrative',
                    'field_role': 'Каналы и продвижение',
                    'section_path': ['Стратегия SMM', 'Каналы и продвижение'],
                }))

    def test_bare_content_pillar_is_editorial_direction_but_advantage_claim_stays_reviewable(self):
        item = {
            'quote': 'Общая поддержка и понимание трудностей',
            'field_role': 'Контентные направления',
            'field_type': 'document_narrative',
            'section_path': ['Стратегия SMM', 'Контентные направления'],
        }
        result = classify_obvious_non_business(item)
        self.assertEqual(result['statement_class'], 'creative_direction')
        claim = {**item, 'quote': 'Преимущество малых групп гарантирует быстрый результат'}
        self.assertIsNone(classify_obvious_non_business(claim))

    def test_educational_propositions_do_not_become_company_claims(self):
        examples = (
            'Изношенные тормозные колодки снижают эффективность торможения.',
            'Регулярная проверка велосипеда — залог безопасности и долговечности.',
            'Важно соблюдать технику, чтобы изделие получилось аккуратным и прочным.',
            'Диагностика велосипеда — это проверка, которая помогает выявить потенциальные проблемы.',
        )
        for text in examples:
            with self.subTest(text=text):
                result = classify_obvious_non_business({'quote': text, 'field_type': 'caption'})
                self.assertEqual(result['statement_class'], 'general_information')

    def test_impersonal_explanations_route_as_general_information_but_company_promises_do_not(self):
        examples = (
            'Это нужно, чтобы внутри не осталось пузырьков воздуха, иначе при обжиге изделие может треснуть.',
            'Такая проверка помогает заметить износ до поездки.',
            'Подобный шаг может снизить риск ошибки при переносе файлов.',
        )
        for text in examples:
            with self.subTest(text=text):
                result = classify_obvious_non_business({'quote': text, 'field_type': 'caption'})
                self.assertEqual(result['statement_class'], 'general_information')
        for text in (
            'В нашей мастерской такая проверка помогает избежать поломки.',
            'Мы гарантируем, что такая проверка исключит риск поломки.',
        ):
            with self.subTest(text=text):
                self.assertIsNone(classify_obvious_non_business({'quote': text, 'field_type': 'caption'}))

    def test_consumer_instructions_are_general_guidance_but_service_promises_remain_reviewable(self):
        examples = (
            'Перед переносом файлов подготовьте их по категориям: документы, фото и видео.',
            'Подготовьте список программ и вопросов перед консультацией.',
            'Before moving files, sort them by type and check what you need.',
        )
        for text in examples:
            with self.subTest(text=text):
                result=classify_obvious_non_business({'quote':text,'field_type':'caption'})
                self.assertIn(result['statement_class'],('general_information','planning_constraint'))
        for text in ('Мы подготовим список для вас и перенесём файлы.',
                     'Запишитесь к нам на консультацию.'):
            with self.subTest(text=text):
                self.assertIsNone(classify_obvious_non_business(
                    {'quote':text,'field_type':'caption'}))

    def test_explicit_business_actor_remains_evidence_reviewable(self):
        examples = (
            'Мы начинаем с раскатывания теста.',
            'В нашей мастерской снимаем мерки и подгоняем одежду.',
            'Мы помогаем каждому клиенту стать увереннее.',
        )
        for text in examples:
            with self.subTest(text=text):
                self.assertIsNone(classify_obvious_non_business(
                    {'quote': text, 'field_type': 'caption'}))

    def test_question_presupposing_company_action_requires_semantic_review(self):
        result = classify_obvious_non_business({
            'quote': 'Как мы помогаем быть увереннее?', 'field_type': 'caption'})
        self.assertIsNone(result)

    def test_strategy_narrative_describes_editorial_intent_without_claiming_a_service(self):
        item = {
            'quote': 'Мы предоставляем информацию о процессе занятий в серии публикаций.',
            'field_role': 'Стратегическое резюме',
            'field_type': 'document_narrative',
            'section_path': ['Контентная стратегия'],
        }
        result = classify_obvious_non_business(item)
        self.assertEqual(result['statement_class'], 'creative_direction')

    def test_customer_response_promise_in_strategy_remains_reviewable(self):
        item = {
            'quote': 'В стратегии мы ответим на ваши вопросы и поможем с записью.',
            'field_role': 'Стратегическое резюме',
            'field_type': 'document_narrative',
            'section_path': ['Контентная стратегия'],
        }
        self.assertIsNone(classify_obvious_non_business(item))

    def test_neutral_cta_invites_questions_without_promising_fulfillment(self):
        for text in ('Напишите нам', 'Задайте нам вопрос', 'Спросите нас о записи'):
            with self.subTest(text=text):
                result = classify_obvious_non_business({
                    'quote': text, 'field_type': 'cta', 'field_role': 'CTA'})
                self.assertEqual(result['statement_class'], 'invitation_cta')

    def test_informational_invitation_in_ready_copy_is_not_a_business_process_claim(self):
        result = classify_obvious_non_business({
            'quote': 'Узнайте, как проходит занятие и как задать вопросы о записи.',
            'field_type': 'caption', 'field_role': 'Готовый текст'})
        self.assertEqual(result['statement_class'], 'audience_prompt')

    def test_informational_invitation_does_not_hide_company_action_or_process(self):
        for text in (
            'Узнайте, как мы начинаем урок с повторения и переходим к практике.',
            'На каждом уроке мы начинаем с повторения, затем переходим к практике.',
            'Узнайте, как мы поможем записаться и ответим на все вопросы.',
        ):
            with self.subTest(text=text):
                self.assertIsNone(classify_obvious_non_business({
                    'quote': text, 'field_type': 'caption', 'field_role': 'Готовый текст'}))

    def test_reader_question_in_public_comments_is_not_a_company_claim(self):
        text = 'Напиши в комментариях или задай вопрос в директе.'
        result = classify_obvious_non_business({
            'quote': text, 'field_type': 'caption', 'field_role': 'Текст/ключевые реплики'})
        self.assertEqual(result['statement_class'], 'audience_prompt')

    def test_company_reply_promise_after_question_invite_remains_reviewable(self):
        text = 'Задайте вопросы в комментариях — мы ответим на всё, что вас интересует.'
        self.assertIsNone(classify_obvious_non_business({
            'quote': text, 'field_type': 'caption', 'field_role': 'Текст/ключевые реплики'}))

    def test_internal_verification_note_in_cta_field_is_planning_not_claim(self):
        for text in ('Уточнить информацию о записи', 'Подтвердить стоимость у владельца',
                     'Согласовать реальные фотографии до публикации'):
            with self.subTest(text=text):
                result = classify_obvious_non_business({
                    'quote': text, 'field_type': 'cta', 'field_role': 'CTA'})
                self.assertEqual(result['statement_class'], 'planning_constraint')

    def test_strategy_steps_and_unknown_data_notes_are_planning(self):
        cases=(
            ('Публикация контента в соответствии с расписанием', ['Стратегия SMM','План действий']),
            ('Мониторинг статистики и анализ отклика', ['Стратегия SMM','План действий']),
            ('Нет информации о цене, адресе и длительности процедуры',
             ['Стратегия SMM','Риски и данные для уточнения']),
        )
        for quote,sections in cases:
            with self.subTest(quote=quote):
                result=classify_obvious_non_business({
                    'quote':quote,'field_type':'document_narrative',
                    'field_role':sections[-1],'section_path':sections})
                expected=('knowledge_boundary' if quote.startswith('Нет информации')
                          else 'planning_constraint')
                self.assertEqual(result['statement_class'],expected)

    def test_bare_unknown_labels_in_risk_section_are_not_asserted_business_facts(self):
        for quote in ('- Расписание','- Материалы','- Сложные виды ремонта'):
            with self.subTest(quote=quote):
                result=classify_obvious_non_business({'quote':quote,
                    'field_type':'document_narrative','field_role':'Риски и данные для уточнения',
                    'section_path':['Стратегия SMM','Риски и данные для уточнения']})
                self.assertEqual(result['statement_class'],'planning_constraint')

    def test_explicit_company_commitment_in_strategy_steps_stays_reviewable(self):
        result=classify_obvious_non_business({
            'quote':'Мы будем отвечать на вопросы в комментариях',
            'field_type':'document_narrative','field_role':'План действий',
            'section_path':['Стратегия SMM','План действий']})
        self.assertIsNone(result)

    def test_positioning_still_requires_source_when_written_as_business_claim(self):
        for text in ('Мы подгоняем и ремонтируем вещи по фигуре, чтобы они снова сидели идеально.',
                     'Наша услуга позволяет не покупать новую одежду.'):
            with self.subTest(text=text):
                self.assertIsNone(classify_obvious_non_business({
                    'quote': text, 'field_type': 'document_narrative',
                    'field_role': 'Ценностное предложение'}))

    def test_cta_response_promise_is_not_waived(self):
        self.assertIsNone(classify_obvious_non_business({
            'quote': 'Напишите нам — расскажем подробнее!',
            'field_type': 'cta', 'field_role': 'CTA'}))

    def test_markdown_fences_and_list_numbers_are_not_claims(self):
        for text in ('```markdown', '```', '1.', '27)', '---'):
            with self.subTest(text=text):
                result=classify_obvious_non_business({'quote':text,'field_type':'document_narrative'})
                self.assertEqual(result['statement_class'],'formatting_only')

    def test_long_audience_questions_unknown_availability_and_cautions_are_not_business_claims(self):
        cases=(
            ('Что вы замечаете в состоянии тормозных колодок или при торможении — '
             'есть ли что-то, о чём хочется спросить?', 'caption', 'audience_prompt'),
            ('Если хотите обсудить свой вопрос на консультации, уточните, доступна ли она.',
             'caption', 'audience_prompt'),
            ('Оставьте вопрос в комментариях; можно спросить и о том, доступна ли консультация по этой теме.',
             'cta', 'audience_prompt'),
            ('Возможность обсудить вопрос на консультации нужно уточнить отдельно — '
             'не будем считать её доступность заранее.', 'caption', 'audience_prompt'),
            ('Опишите свои наблюдения своими словами: это не заменяет диагностику.',
             'caption', 'general_information'),
        )
        for text,field_type,expected in cases:
            with self.subTest(text=text):
                result=classify_obvious_non_business({'quote':text,'field_type':field_type,
                    'field_role':field_type,'section_path':['Контент-план','Неделя 1']})
                self.assertIsNotNone(result)
                self.assertEqual(result['statement_class'],expected)

    def test_long_question_does_not_hide_a_company_promise(self):
        for text in ('Когда мы ответим на все вопросы о консультации?',
                     'Гарантируем ли мы результат за один день?'):
            with self.subTest(text=text):
                self.assertIsNone(classify_obvious_non_business({'quote':text,
                    'field_type':'caption','field_role':'Текст/ключевые реплики'}))

    def test_r42_random_strategy_recommendations_are_not_misclassified_as_business_facts(self):
        cases=(
            ('Регулярная диагностика: образовательные вопросы о том, что владельцу важно знать о диагностике; '
             'конкретные ответы и утверждения о процессе публиковать только после подтверждения.',
             ['Стратегия SMM','Контентные направления']),
            ('Тестировать вовлекающую подачу: побуждать владельцев делиться вопросами о диагностике и записи, '
             'не обещая ответа, доступности услуги или конкретного следующего шага.',
             ['Стратегия SMM','Стратегическое направление']),
            ('Чередовать информативный и вовлекающий тип материала при частоте две публикации в неделю.',
             ['Стратегия SMM','Стратегическое направление']),
            ('В материалах отделять подтверждённый список услуг от образовательных тем, требующих проверки; '
             'вопросы о записи учитывать как отдельный тип входящих обращений.',
             ['Стратегия SMM','Канал и продвижение']),
        )
        for text,sections in cases:
            with self.subTest(text=text):
                result=classify_obvious_non_business({'quote':text,
                    'field_type':'document_narrative','field_role':sections[-1],
                    'section_path':sections})
                self.assertIsNotNone(result)
                self.assertEqual(result['statement_class'],'planning_constraint')

    def test_random_strategy_scope_still_blocks_explicit_service_and_reply_promises(self):
        for text in ('Мы ответим на вопросы о наличии.',
                     'Мастерская гарантирует ремонт за один день.',
                     'В нашей мастерской ремонтируют любые велосипеды.'):
            with self.subTest(text=text):
                self.assertIsNone(classify_obvious_non_business({'quote':text,
                    'field_type':'document_narrative',
                    'section_path':['Стратегия SMM','Редакционные направления']}))


    def test_perfective_infinitives_in_strategy_action_sections_are_planning(self):
        for text in ('Опубликовать их в ВКонтакте',
                     'Разместить материалы в выбранном канале',
                     'Проанализировать отклик после публикации'):
            with self.subTest(text=text):
                result=classify_obvious_non_business({'quote':text,
                    'field_type':'document_narrative','field_role':'План действий',
                    'document':'strategy','section_path':['Стратегия SMM','План действий']})
                self.assertEqual(result['statement_class'],'planning_constraint')
        for text in ('Компания публикует материалы в ВКонтакте.',
                     'Мы размещаем материалы в выбранном канале.'):
            with self.subTest(text=text):
                self.assertIsNone(classify_obvious_non_business({'quote':text,
                    'field_type':'document_narrative','field_role':'План действий',
                    'document':'strategy','section_path':['Стратегия SMM','План действий']}))


if __name__ == '__main__':
    unittest.main()
