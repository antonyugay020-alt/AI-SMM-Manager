# -*- coding: utf-8 -*-
"""Build varied, synthetic end-to-end briefs for live acceptance runs.

Each run supplies a fresh seed. Keeping the seed makes a failed task reproducible
without reusing one memorized industry or wording pattern on every run.
"""
from __future__ import annotations

import random
import secrets
import copy
import hashlib
from russian_quantity import publication_count


BUSINESSES = (
    {
        'category': 'мастерская ремонта велосипедов', 'brand': 'Веломир',
        'offer': 'диагностика велосипедов, замена тормозных колодок и настройка переключателей',
        'audiences': ('взрослые владельцы городских велосипедов', 'люди, которые ездят на велосипеде на работу', 'новички, недавно купившие городской велосипед'),
        'goals': ('объяснить пользу регулярной диагностики и получить вопросы о записи', 'помочь заметить признаки износа и привести обращения на консультацию', 'снизить тревогу перед первым обращением в мастерскую'),
        'unknown': 'цены, адрес, сроки ремонта, отзывы и гарантии',
    },
    {
        'category': 'студия керамики', 'brand': 'Тёплая глина',
        'offer': 'занятия по ручной лепке для взрослых новичков',
        'audiences': ('взрослые, которые хотят попробовать керамику впервые', 'люди, ищущие спокойное творческое занятие после работы', 'начинающие, которым интересно сделать первую вещь руками'),
        'goals': ('показать, как устроено первое занятие, и собрать вопросы о записи', 'объяснить этапы ручной лепки без обещаний готового результата', 'помочь новичку понять, чего ожидать от творческого занятия'),
        'unknown': 'расписание, цены, адрес, размер групп и отзывы',
    },
    {
        'category': 'небольшая языковая школа', 'brand': 'Диалог',
        'offer': 'разговорные занятия английским для взрослых начинающего уровня в небольших группах',
        'audiences': ('работающие взрослые, которым не хватает разговорной практики', 'начинающие, которым сложно заговорить на английском', 'взрослые ученики, возвращающиеся к изучению языка'),
        'goals': ('показать формат разговорной практики и получить запросы на консультацию', 'объяснить, как подготовиться к первому занятию, и вызвать вопросы', 'сформировать доверие без обещаний быстрого результата'),
        'unknown': 'расписание, стоимость, адрес, квалификация преподавателей и отзывы',
    },
    {
        'category': 'питомник комнатных растений', 'brand': 'Лист рядом',
        'offer': 'комнатные растения и базовые рекомендации по уходу при покупке',
        'audiences': ('люди, которые впервые выбирают комнатное растение', 'жители квартир с небольшим количеством естественного света', 'начинающие любители растений, которым трудно выбрать неприхотливый вариант'),
        'goals': ('помочь выбрать растение под условия дома и получить вопросы о наличии', 'объяснить базовый уход без универсальных обещаний', 'собрать обращения от людей, которые не знают, с чего начать'),
        'unknown': 'цены, адрес, текущие остатки, отзывы и обещания приживаемости',
    },
    {
        'category': 'ателье по ремонту одежды', 'brand': 'Стежок',
        'offer': 'подгонка одежды по фигуре и мелкий ремонт швов',
        'audiences': ('люди, которым нужно продлить срок службы любимой одежды', 'покупатели, которым требуется подогнать вещь по фигуре', 'жители района, у которых накопилась одежда для мелкого ремонта'),
        'goals': ('объяснить, какие задачи можно обсудить с мастером, и получить запросы', 'показать ценность ремонта одежды без обещаний по срокам', 'побудить уточнить возможность ремонта конкретной вещи'),
        'unknown': 'цены, адрес, сроки, отзывы и перечень сложных видов ремонта',
    },
    {
        'category': 'мастерская по ремонту бытовой техники', 'brand': 'Точный мастер',
        'offer': 'первичная диагностика и ремонт мелкой бытовой техники по согласованию',
        'audiences': ('люди, у которых перестала работать небольшая бытовая техника', 'покупатели, которые хотят понять порядок диагностики до обращения', 'семьи, ищущие способ оценить возможность ремонта прибора'),
        'goals': ('объяснить порядок первичного обращения и получить вопросы о диагностике', 'помочь подготовить описание неисправности перед консультацией', 'сформировать реалистичные ожидания до осмотра техники'),
        'unknown': 'цены, адрес, сроки, перечень обслуживаемых брендов и гарантии',
    },
    {
        'category': 'школа рисования для взрослых', 'brand': 'Линия',
        'offer': 'вводные занятия по рисунку для взрослых без предварительной подготовки',
        'audiences': ('взрослые, которые давно хотели начать рисовать', 'новички, переживающие, что у них нет художественного опыта', 'люди, ищущие творческое занятие для себя'),
        'goals': ('объяснить, как проходит вводное занятие, и получить вопросы о записи', 'снять барьер «я не умею рисовать» без обещаний результата', 'показать процесс обучения начинающим на понятных примерах'),
        'unknown': 'расписание, цены, адрес, материалы и отзывы',
    },
    {
        'category': 'небольшая кофейня', 'brand': 'Крошка кофе',
        'offer': 'кофейные напитки и выпечка собственного приготовления',
        'audiences': ('люди, которые ищут место для короткой паузы в течение дня', 'жители района, выбирающие кофе навынос', 'гости, которым интересно сочетать напиток с выпечкой'),
        'goals': ('показать ассортимент без вымышленных цен и получить вопросы о наличии', 'помочь выбрать напиток по вкусовым предпочтениям', 'познакомить аудиторию с повседневной атмосферой кофейни'),
        'unknown': 'цены, адрес, часы работы, состав конкретной выпечки и отзывы',
    },
    {
        'category': 'сервис по уходу за домашними животными', 'brand': 'Лапа и хвост',
        'offer': 'гигиенический уход и бережное расчёсывание собак по предварительной записи',
        'audiences': ('владельцы собак, которые выбирают первый груминг', 'люди, чьим питомцам трудно даётся расчёсывание дома', 'владельцы собак, желающие заранее узнать о процедуре ухода'),
        'goals': ('объяснить этапы гигиенического ухода и получить вопросы о записи', 'снизить неопределённость перед первым визитом питомца', 'показать, как подготовить собаку к процедуре без гарантий поведения'),
        'unknown': 'цены, адрес, длительность процедуры, отзывы и медицинские обещания',
    },
    {
        'category': 'мастерская по ремонту обуви', 'brand': 'Второй шаг',
        'offer': 'замена набоек и мелкий ремонт обуви после осмотра',
        'audiences': ('люди, которые хотят отремонтировать любимую пару обуви', 'покупатели, не уверенные, подлежит ли обувь ремонту', 'жители района, которым важно продлить использование повседневной обуви'),
        'goals': ('объяснить, какие детали мастер может оценить при осмотре', 'побудить прислать вопрос о возможном ремонте конкретной пары', 'рассказать о простых признаках износа без обещаний результата'),
        'unknown': 'цены, адрес, сроки, отзывы и возможность ремонта каждой модели',
    },
    {
        'category': 'магазин товаров для творчества', 'brand': 'Цветной ящик',
        'offer': 'материалы для рисования и простые наборы для начинающих',
        'audiences': ('люди, которые выбирают первые материалы для рисования', 'родители, ищущие творческое занятие дома', 'начинающие художники, которым сложно сравнить виды материалов'),
        'goals': ('помочь разобраться в назначении материалов и получить вопросы о наличии', 'собрать запросы на рекомендации для первого набора', 'объяснить разницу между форматами материалов без превосходных обещаний'),
        'unknown': 'цены, адрес, остатки, отзывы и гарантии результата от набора',
    },
    {
        'category': 'сервис по настройке домашних компьютеров', 'brand': 'Понятный компьютер',
        'offer': 'настройка программ и перенос пользовательских файлов по согласованию',
        'audiences': ('люди, которым нужна помощь с новым домашним компьютером', 'пользователи, которым сложно подготовить файлы к переносу', 'семьи, желающие навести порядок в базовых настройках устройств'),
        'goals': ('объяснить, что подготовить к первичной консультации', 'помочь описать задачу до обращения в сервис', 'получить вопросы о безопасной организации переноса файлов'),
        'unknown': 'цены, адрес, сроки, отзывы и абсолютные гарантии сохранности данных',
    },
)

CHANNELS = ('Instagram', 'Telegram', 'ВКонтакте')
FRAMES = (
    'Составь прикладную SMM-стратегию и контент-план',
    'Подготовь практичную SMM-стратегию и контент-план',
    'Разработай SMM-стратегию и контент-план',
    'Мне нужна практичная SMM-стратегия и контент-план',
    'Сделай понятную SMM-стратегию и контент-план',
)
POST_FIELDS = (
    'тему, формат, полный уникальный текст, сценарий визуала, CTA, KPI и способ измерения',
    'отдельную тему и готовый текст, идею визуального сопровождения, призыв к действию и метрику результата',
    'цель публикации, неповторяющийся текст, план визуала, CTA и конкретный способ оценить отклик',
    'разные темы, законченные тексты для публикации, описание визуала, CTA, KPI и материалы для подготовки',
    'тему, подходящий формат, текст без заглушек, сценарий иллюстрации, CTA и измеримый показатель',
)
STYLE_NOTES = (
    'Пиши ясно и естественно, избегай рекламных клише.',
    'Сделай подачу конкретной и разговорной, не используй общие обещания.',
    'Дай полезные детали и короткие понятные формулировки.',
    'Сохрани уважительный тон и не дави на читателя.',
    'Чередуй информативную и вовлекающую подачу материалов.',
)
SINGLE_POST_FRAMES = (
    'Напиши один готовый пост для {channel}',
    'Подготовь одну публикацию для {channel}',
    'Составь самостоятельный пост для канала {channel}',
    'Сделай один полезный материал для {channel}',
)
POST_DELIVERABLES = (
    'Заверши текст естественным приглашением задать вопрос о возможности обращения.',
    'Заверши мягким призывом описать свою ситуацию в сообщении.',
    'Заверши вопросом к читателю, который помогает начать разговор без обещаний.',
    'Заверши коротким и ненавязчивым CTA по теме публикации.',
)


def generate_acceptance_tasks(seed: int, count: int = 3) -> list[dict]:
    """Return distinct businesses and independently randomized brief details."""
    if type(seed) is not int:
        raise TypeError('seed must be an integer')
    if type(count) is not int or not 1 <= count <= 100:
        raise ValueError('count must be between 1 and 100')
    rng = random.Random(seed)
    shuffled = rng.sample(BUSINESSES, len(BUSINESSES))
    businesses = [shuffled[index % len(shuffled)] for index in range(count)]
    tasks = []
    for index, business in enumerate(businesses, 1):
        channel = rng.choice(CHANNELS)
        # Exercise both single-slot and multi-slot calendars. The small range
        # bounds live inference cost; the complete 1..7 schedule is covered by
        # the code-level planner contract tests.
        posts = rng.choice((1, 2))
        audience = rng.choice(business['audiences'])
        goal = rng.choice(business['goals'])
        frame = rng.choice(FRAMES)
        fields = rng.choice(POST_FIELDS)
        style = rng.choice(STYLE_NOTES)
        format_requirement = rng.choice((
            'Добавь самостоятельную рубрику с вопросом к аудитории.',
            'Чередуй обучающую и сервисную цель публикаций.',
            'Для каждого материала укажи необходимый исходник для визуала.',
            'Укажи критерий редакторской проверки каждого текста.',
            'Отдельно обозначь факты, которые перед публикацией нужно уточнить у клиента.',
        ))
        scenario_id=hashlib.sha256(f'{seed}:{index}:{channel}:{business["category"]}'.encode('utf-8')).hexdigest()[:16]
        project = f"qa_{index:02d}_{business['category'].split()[0]}"
        sentences = (
            f"{frame} для {channel} на одну неделю: "
            f"{publication_count(posts)} в неделю, всего {publication_count(posts)}. Проект «{business['brand']}» — "
            f"{business['category']}; в работе есть только следующая подтверждённая информация: "
            f"{business['offer']}. Целевая аудитория: {audience}. Задача контента: {goal}. "
            f"Считай неизвестными {business['unknown']}; не придумывай их, отзывы, акции, "
            f"гарантии, цифры или свойства, которых нет в исходных данных. "
            f"Для каждой публикации укажи {fields}. Для KPI выбери показатель и способ "
            f"измерения без произвольной числовой цели, пока в брифе нет исходной базы. {format_requirement} {style}"
        )
        tasks.append({
            'name': project,
            'category': business['category'],
            'channel': channel,
            'posts': posts,
            'audience': audience,
            'goal': goal,
            'request': sentences,
            'scenario_id': scenario_id,
            'format_requirement': format_requirement,
        })
    return tasks


def select_acceptance_author_models(installed_models, count: int | None = None, *, chooser=None,
                                    excluded_families=()) -> list[str]:
    """Randomly select each installed author model tag once for live tasks.

    Every installed model tag is included by default. A reviewer is selected
    separately for each author so the original writer family can be compared
    too. Optional family exclusions remain available for controlled tests.
    """
    if count is not None and (type(count) is not int or count < 1):
        raise ValueError('count must be a positive integer or None')
    excluded = {str(item).strip().split(':', 1)[0].casefold() for item in excluded_families}
    by_name = {}
    for raw in installed_models or []:
        name = str(raw).strip()
        family = name.split(':', 1)[0].casefold()
        if name and family not in excluded:
            by_name.setdefault(name.casefold(), name)
    choices = list(by_name.values())
    if count is None:
        count = len(choices)
    if count > 100:
        raise ValueError('Не поддерживается более 100 моделей в одном сравнительном прогоне')
    if len(choices) < count:
        raise ValueError(f'Нужно минимум {count} разных установленных моделей-авторов '
                         f'помимо семейства независимого редактора; доступно {len(choices)}')
    randomizer = chooser or secrets.SystemRandom()
    randomizer.shuffle(choices)
    return choices[:count]


def select_profile_author_models(installed_models, profile, *, chooser=None):
    """Choose a transparent randomized model sample for quick or full runs.

    Quick runs include a configured API author or an Ollama Cloud author when
    one is available, paired with one random local Ollama author. This gives a
    useful provider comparison without paying for a full all-model run. When
    no cloud author is configured, keep the existing randomized local sample.
    Full runs preserve complete installed-model coverage.
    """
    pool=select_acceptance_author_models(installed_models,chooser=chooser)
    name=str(profile or 'full').strip().casefold()
    if name=='full':
        return pool
    if name!='fast':
        raise ValueError('AI_SMM_TEST_PROFILE должен быть fast или full')
    openai=[model for model in pool if model.casefold().startswith('openai/')]
    ollama_cloud=[model for model in pool if '/' not in model and
                  model.casefold().endswith((':cloud','-cloud'))]
    cloud=openai or ollama_cloud
    if cloud:
        local=[model for model in pool if '/' not in model and
               not model.casefold().endswith((':cloud','-cloud'))]
        if not local:
            return [cloud[0],*[model for model in pool if model!=cloud[0]][:1]]
        # Both lists inherit the seeded shuffle above, so provider and local
        # model selection remain randomized and exactly replayable.
        return [cloud[0],local[0]]
    if not pool:
        raise ValueError('Быстрый профиль требует хотя бы одну доступную модель')
    candidates=list(pool)
    randomizer=chooser or secrets.SystemRandom()
    randomizer.shuffle(candidates)
    return candidates[:2]


def build_randomized_acceptance_cases(seed, author_models):
    """Give each author model its own fresh brief from one replayable run seed.

    The benchmark compares models across a varied task set; it deliberately
    does not reuse one brief across all authors. Business categories are
    sampled without replacement when possible, while channel, audience, goal,
    wording, and style are randomized independently for each case.
    """
    models = [str(model).strip() for model in author_models or []]
    if not models or any(not model for model in models):
        raise ValueError('Для randomized benchmark нужна хотя бы одна модель-автор')
    if len({model.casefold() for model in models}) != len(models):
        raise ValueError('В randomized benchmark не должно быть повторных моделей-авторов')
    briefs = generate_acceptance_tasks(seed, count=len(models))
    cases = []
    for model, brief in zip(models, briefs):
        case = copy.deepcopy(brief)
        case['author_model'] = model
        cases.append(case)
    return cases


def build_randomized_workflow_cases(seed, author_models, *, ensure_workflow_coverage=False):
    """Create a mixed sample across distinct supported production workflows.

    A fast run with two authors covers both the compound strategy/calendar
    pipeline and the standalone-writing route used by the GUI. Which model
    receives which workflow is randomized and replayable from the run seed.
    Every brief is independently generated from a different synthetic brief.
    """
    models=[str(model).strip() for model in author_models or []]
    if not models or any(not model for model in models):
        raise ValueError('Для mixed workflow benchmark нужна хотя бы одна модель')
    if len({model.casefold() for model in models})!=len(models):
        raise ValueError('В mixed workflow benchmark не должно быть повторных моделей')
    if type(seed) is not int:
        raise TypeError('seed must be an integer')
    if ensure_workflow_coverage and len(models)==1:
        models=models*2  # Two different briefs/workflows, one eligible author; never two author identities.
    cases=generate_acceptance_tasks(seed,len(models))
    randomizer=random.Random(seed ^ 0xA75F10A)
    workflow_types=['compound_strategy_calendar','standalone_post']
    while len(workflow_types)<len(models):
        workflow_types.extend(['compound_strategy_calendar','standalone_post'])
    workflow_types=workflow_types[:len(models)]
    randomizer.shuffle(workflow_types)
    randomizer.shuffle(cases)
    result=[]
    for index,(model,case,workflow_type) in enumerate(zip(models,cases,workflow_types),1):
        case=copy.deepcopy(case)
        case['name']=f'qa_{index:02d}_{workflow_type}_{model.split(":",1)[0].replace("/","_")}_{case["scenario_id"][:8]}'
        case['workflow_type']=workflow_type
        case['author_model']=model
        if workflow_type=='standalone_post':
            business=next((item for item in BUSINESSES if item['category']==case['category']),BUSINESSES[0])
            frame=randomizer.choice(SINGLE_POST_FRAMES)
            deliverable=randomizer.choice(POST_DELIVERABLES)
            style=randomizer.choice(STYLE_NOTES)
            case['posts']=1
            case['request']=(
                f"{frame.format(channel=case['channel'])} для проекта «{business['brand']}», "
                f"который работает как {business['category']}. Подтверждённая услуга: {business['offer']}. "
                f"Аудитория: {case['audience']}. Цель поста: {case['goal']}. "
                f"Создай один завершённый текст публикации с конкретной темой, полезным содержанием "
                f"и ясным языком. Ограничь результат одной публикацией и не добавляй другие deliverables, "
                f"цены, адреса, сроки, отзывы, гарантии или неподтверждённые свойства бизнеса. "
                f"Название проекта, его категорию, аудиторию и подтверждённую услугу из этого задания "
                f"можно использовать в тексте. Неизвестны: {business['unknown']}. "
                f"Если задача просит показать ассортимент или варианты, а конкретные позиции не перечислены в задании, "
                f"опиши только подтверждённые категории и предложи уточнить наличие конкретного варианта; "
                f"не выдумывай позиции и не требуй их перечисления как обязательного условия. "
                f"{deliverable} {case['format_requirement']} {style}"
            )
        result.append(case)
    return result
