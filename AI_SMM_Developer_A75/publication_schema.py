"""Canonical publication fields shared by generation, rendering and review.

The post object is the source of truth. Markdown is only a presentation/export
format and must retain one column per field so downstream review cannot merge
materials, measurement, audience, KPI or publishable copy.
"""
from __future__ import annotations

import re

POST_FIELDS = ('topic', 'visual', 'caption', 'cta', 'kpi', 'audience',
               'measurement', 'materials')
PUBLICATION_FIELDS = ('caption', 'visual', 'cta', 'kpi', 'audience',
                      'measurement', 'materials')

_GENERATION_DESCRIPTIONS = {
    'id': 'Точный идентификатор утверждённого задания.',
    'topic': 'Точная утверждённая тема публикации.',
    'caption': 'Полный готовый текст для читателя с полезным содержанием по теме; не описание будущего текста.',
    'cta': 'Призыв к действию в соответствии с исходным запросом, без неподтверждённых обещаний.',
    'kpi': 'Наблюдаемый показатель результата; не выдуманная целевая цифра.',
    'audience': 'Аудитория именно этой публикации.',
    'measurement': 'Конкретный способ подсчёта выбранного KPI.',
    'materials': 'Что нужно подготовить, запросить или подтвердить до публикации; не утверждение о наличии файлов.',
}


def publication_response_schema(*, batch=False, count=None):
    """One generation contract for both batches and individual publications.

    The model describes visual scenes separately from the host-owned format.
    Model-boundary normalization renders these scenes into canonical text;
    neither the schema nor normalization certifies editorial quality.
    """
    properties = {field: {'type': 'string', 'minLength': 1, 'description': description}
                  for field, description in _GENERATION_DESCRIPTIONS.items()}
    properties['visual'] = {
        'type': 'object', 'additionalProperties': False, 'required': ['scenes'],
        'description': 'Будущий постановочный сценарий или макет. Укажи содержание и композицию кадров; название формата не является сценарием.',
        'properties': {'scenes': {
            'type': 'array', 'minItems': 1,
            'items': {'type': 'object', 'additionalProperties': False,
                      'required': ['description'], 'properties': {
                          'description': {'type': 'string', 'minLength': 1,
                                          'description': 'Что и как показать или снять в этом кадре/макете; не выдавать постановку за существующий материал.'},
                          'text': {'type': 'string', 'description': 'Надпись в кадре, если она нужна.'},
                      }},
        }},
    }
    post = {'type': 'object', 'additionalProperties': False,
            'required': ['id', *POST_FIELDS], 'properties': properties}
    if not batch:
        return post
    posts = {'type': 'array', 'items': post}
    if count is not None:
        posts.update(minItems=count, maxItems=count)
    return {'type': 'object', 'additionalProperties': False,
            'required': ['posts'], 'properties': {'posts': posts}}

CALENDAR_COLUMNS = (
    ('Неделя/день', 'slot'),
    ('Площадка и формат', 'platform_format'),
    ('Конкретная тема', 'topic'),
    ('Цель', 'goal'),
    ('Сценарий/визуал', 'visual'),
    ('Текст/ключевые реплики', 'caption'),
    ('CTA', 'cta'),
    ('KPI', 'kpi'),
    ('Метод измерения', 'measurement'),
    ('Целевая аудитория', 'audience'),
    ('Необходимые материалы', 'materials'),
)

_FIELD_PATTERNS = (
    ('slot', r'недел\w*\s*/\s*д\w+|week\s*/\s*day|publication\s+slot'),
    ('platform_format', r'площад\w*|формат|platform|format|channel'),
    ('topic', r'тем\w*|topic'),
    ('goal', r'цель(?:ю|и|ей)?|задач\w*|goal|objective'),
    ('visual', r'визуал\w*|сценар\w*|storyboard|visual'),
    ('kpi', r'\bkpi\b|метрик\w*|показател\w*|лайк\w*|комментари\w*|'
            r'сохранени\w*|подписчик\w*|просмотр\w*|охват\w*|вовлеч\w*|'
            r'обращени\w*|конверси\w*|engagement|impressions|reach|views|clicks'),
    ('caption', r'ключев\w*\s+реплик\w*|готов\w*\s+текст|полны\w*\s+текст|'
                r'текст\w*|подпис(?:ь|и|ью|ях|ями)\b|caption|copy'),
    ('cta', r'\bcta\b|призыв\w*|call\s*to\s*action'),
    ('measurement', r'метод\w*\s+измерен\w*|способ\w*\s+измерен\w*|'
                    r'измерен\w*|measurement'),
    ('audience', r'аудитори\w*|audience'),
    ('materials', r'необходим\w*\s+материал\w*|нужн\w*\s+материал\w*|'
                  r'материал\w*|resources?'),
)

_COMPILED = tuple((field, re.compile(pattern, re.I)) for field, pattern in _FIELD_PATTERNS)


def publication_field_type(label: str) -> str:
    """Map a displayed field label to its canonical post-schema name."""
    text = re.sub(r'\s+', ' ', str(label or '')).strip()
    if text in POST_FIELDS or text in {'slot','platform_format','goal','document_narrative'}:
        return text
    for field, pattern in _COMPILED:
        if pattern.search(text):
            return field
    return 'document_narrative'


def is_planning_field(field_type: str) -> bool:
    """True for fields that describe a proposed publication plan.

    Caption and CTA remain claim-reviewable: they can contain unsupported
    service availability, business actions, guarantees or outcomes.
    """
    return field_type in {'slot', 'platform_format', 'topic', 'goal', 'visual',
                          'kpi', 'measurement', 'audience', 'materials'}


def publication_structure_issues(post,slot=None):
    """Validate canonical data, without judging publication meaning or style."""
    if not isinstance(post,dict):return ['Публикация должна быть объектом']
    issues=[]
    for key in ('id',*POST_FIELDS):
        if not isinstance(post.get(key),str) or not post[key].strip():
            issues.append('пустое или некорректное поле '+key)
    if slot is not None:
        if (not isinstance(slot,dict) or type(slot.get('week')) is not int or slot['week']<1
                or not isinstance(slot.get('day'),str) or not slot['day'].strip()
                or not isinstance(slot.get('topic'),str)):
            return issues+['Некорректный утверждённый слот']
        if post.get('id')!=str(slot['week'])+'/'+slot['day']:issues.append('публикация принадлежит другому слоту')
        if post.get('topic')!=slot['topic']:issues.append('тема публикации не соответствует утверждённому плану')
    return issues
