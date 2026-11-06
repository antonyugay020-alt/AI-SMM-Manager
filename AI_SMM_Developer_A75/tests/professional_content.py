"""Deterministic editorial checks and a bounded plan for long SMM calendars.

These checks are intentionally structural; they never certify professional quality.
"""
import re
from collections import Counter


_COUNT_WORDS = {'одну':1,'один':1,'одно':1,'две':2,'два':2,'двух':2,
                'три':3,'четыре':4,'пять':5,'шесть':6,'семь':7,
                'восемь':8,'девять':9,'десять':10,'одиннадцать':11,'двенадцать':12,
                'тринадцать':13,'четырнадцать':14,'пятнадцать':15,
                'шестнадцать':16,'семнадцать':17,'восемнадцать':18,
                'девятнадцать':19,'двадцать':20,
                **{'двадцать '+word:20+number for word,number in (
                    ('один',1),('одну',1),('два',2),('две',2),('три',3),
                    ('четыре',4),('пять',5),('шесть',6),('семь',7),
                    ('восемь',8),('девять',9))}}


def requested_weeks(request, *, with_source=False):
    from task_semantics import current_semantics
    semantic=current_semantics()
    if semantic and semantic.get('schedule'):
        weeks=semantic['schedule']['weeks']
        return (weeks,True) if with_source else weeks
    """Never silently change an explicit duration; default only if omitted.

    26 is the supported production limit, not a reason to truncate a task.
    A nonnumeric request is reported to the caller when it exceeds limits.
    """
    s = str(request or '').casefold()
    token = r'(\d+|' + '|'.join(re.escape(k).replace(r'\ ',r'\s+') for k in sorted(_COUNT_WORDS,key=len,reverse=True)) + r')'
    def number(value):
        return int(value) if value.isdigit() else _COUNT_WORDS.get(' '.join(value.split()))
    def resolve(pattern, multiply=1):
        matches=[]
        for hit in re.finditer(pattern,s):
            value=number(hit.group(1))
            if value is not None: matches.append(round(value*multiply))
        return matches
    explicit_weeks=resolve(r'\b'+token+r'\s*недел\w*')
    explicit_months=resolve(r'\b'+token+r'\s*месяц\w*', 4.33)
    if not explicit_weeks and re.search(r'\b(?:на|за|сроком|периодом|течение)\s+[а-яё]+(?:\s+[а-яё]+)?\s+недел\w*',s):
        raise ValueError('Срок в неделях указан словами, но его нельзя однозначно разобрать; не подставляю 4 недели')
    if not explicit_months and re.search(r'\b(?:на|за|сроком|периодом|течение)\s+[а-яё]+(?:\s+[а-яё]+)?\s+месяц\w*',s):
        raise ValueError('Срок в месяцах указан словами, но его нельзя однозначно разобрать; не подставляю 4 недели')
    if explicit_weeks and explicit_months and (
            len(set(explicit_weeks)) != 1 or len(set(explicit_months)) != 1
            or abs(explicit_weeks[0] - explicit_months[0]) > 1):
        raise ValueError('Противоречивые сроки в неделях и месяцах')
    if explicit_weeks:
        if len(set(explicit_weeks))>1: raise ValueError('Противоречивые сроки в неделях')
        count=explicit_weeks[0]
    elif explicit_months:
        if len(set(explicit_months))>1: raise ValueError('Противоречивые сроки в месяцах')
        count=explicit_months[0]
    else:
        count=4
    if not 1 <= count <= 26:
        raise ValueError(f'Запрошено {count} недель; поддерживается от 1 до 26, срок не сокращён автоматически')
    return (count,bool(explicit_weeks or explicit_months)) if with_source else count


def count_calendar_entries(body):
    """Count distinct publication rows, excluding header/separator lines."""
    entries=[]
    for line in (body or '').splitlines():
        if not line.lstrip().startswith('|'):
            continue
        cells=[c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells)<5 or all(re.fullmatch(r'[-:\s]+', c or '-') for c in cells):
            continue
        if re.search(r'^(дата|неделя(?:/день)?|день|период|месяц)$', cells[0],re.I):
            continue
        entries.append(cells)
    return entries


def editorial_audit(body, weeks, per_week=3):
    rows=count_calendar_entries(body)
    issues=[]
    minimum=weeks*per_week
    if len(rows)<minimum:
        issues.append(f'В календаре {len(rows)} публикаций, необходимо минимум {minimum} для {weeks} недель при {per_week} публикациях в неделю.')
    themes=[]
    for row in rows:
        # Do not count the date/format as part of the theme.
        topic = re.sub(r'[^а-яёa-z0-9]+',' ',row[2].casefold()).strip() if len(row)>2 else ''
        if topic: themes.append(topic)
    repeats=[theme for theme,n in Counter(themes).items() if n>1 and len(theme)>8]
    if repeats:
        issues.append('Повторяются темы: '+', '.join(repeats[:5]))
    # Document size is not a completeness signal. A short, explicit brief with
    # two fully populated posts can be complete, while a long document can be
    # empty of usable deliverables. Completeness is checked from the requested
    # slot count here and from each publication's required fields upstream.
    return issues


def week_ranges(weeks, chunk=4):
    return [(start,min(weeks,start+chunk-1)) for start in range(1,weeks+1,chunk)]
