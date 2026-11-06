"""Task-scoped content delivery requirements.

Never assume a fixed 39-post benchmark. An explicit quantity is binding; derived
quantities are only used when the request specifies cadence and duration.
A report is successful only when every requested deliverable has been checked.
"""
import re
from collections import Counter
from dataclasses import dataclass
from professional_content import requested_weeks
from publication_schedule import publication_ids
from publication_schema import PUBLICATION_FIELDS

# Share one lexical numeral vocabulary with the period parser. The cadence
# validator, not the tokenizer, rejects counts above the supported frequency.
from professional_content import _COUNT_WORDS as _WORDS


@dataclass(frozen=True)
class PublicationScope:
    weeks: int
    per_week: int
    total: int
    explicit_total: int | None = None
    cadence_source: str = "default"


def _number(s):
    s = str(s).strip().casefold()
    return int(s) if s.isdigit() else _WORDS.get(s)


def publication_scope(request):
    """Derive slots from user request and reject contradictory explicit counts."""
    from task_semantics import current_semantics
    semantic=current_semantics()
    if semantic and semantic.get('schedule'):
        row=semantic['schedule']
        return PublicationScope(row['weeks'],row['per_week'],row['total'],row['total'],'semantic')
    s = re.sub(r'\s+', ' ', request or '').casefold()
    weeks, explicit_period = requested_weeks(s, with_source=True)
    num = r'(\d+|' + '|'.join(re.escape(k) for k in sorted(_WORDS,key=len,reverse=True)) + r')'
    patterns = (
        rf'по\s+{num}\s+(?:публикац\w*|пост\w*|материал\w*)\s+(?:в|на)\s+недел\w*',
        rf'{num}\s+(?:публикац\w*|пост\w*|материал\w*)\s+в\s+недел\w*',
        rf'частот\w*\s*(?:публикац\w*)?\s*[:—-]?\s*{num}\s+(?:публикац\w*|пост\w*)\s+в\s+недел\w*',
        rf'по\s+{num}\s+в\s+недел\w*',
        rf'{num}\s+в\s+недел\w*',
        rf'по\s+{num}\s+(?:публикац\w*|пост\w*|материал\w*)\s+(?:кажд\w*\s+недел\w*|еженедельно)',
        rf'{num}\s+(?:публикац\w*|пост\w*|материал\w*)\s+(?:кажд\w*\s+недел\w*|еженедельно)',
        rf'еженедельно\s+по\s+{num}\s+(?:публикац\w*|пост\w*|материал\w*)',
    )
    counts = []
    for pat in patterns:
        counts.extend(value for found in re.findall(pat, s) if (value := _number(found)) is not None)
    if re.search(r'\bдважды\s+в\s+недел\w*', s):
        counts.append(2)
    if re.search(r'\bтрижды\s+в\s+недел\w*', s):
        counts.append(3)
    if counts and len(set(counts)) > 1:
        raise ValueError('В запросе указаны противоречивые частоты публикаций')
    totals = [_number(x) for x in re.findall(r'(?:всего|ровно|итого|общим\s+числом)\s+'+num+r'\s+(?:публикац\w*|пост\w*|материал\w*)', s)]
    # A standalone "16 публикаций" is an explicit total too; exclude cadence.
    for match in re.finditer(r'(\d+)\s+(?:публикац\w*|пост\w*)', s):
        following=s[match.end():match.end()+18]
        preceding=s[max(0,match.start()-5):match.start()]
        if (re.match(r'\s+(?:(?:в|на)\s+недел|кажд\w*\s+недел|еженедельно)',following)
                or re.search(r'по\s*$', preceding)):
            continue
        totals.append(int(match.group(1)))
    if totals and len(set(totals)) > 1:
        raise ValueError('В запросе указаны противоречивые общие количества публикаций')
    # Explicit quantity phrases must never degrade to the historical 3/week
    # default if the lexical parser does not recognize their numeral.
    for match in re.finditer(r'(?:всего|итого|ровно|общим\s+числом)\s+([а-яё][а-яё\s-]{0,40}?)\s+(?:публикац\w*|пост\w*|материал\w*)',s):
        if _number(match.group(1)) is None:
            raise ValueError('Не распознано явно указанное общее количество публикаций')
    explicit_total = totals[0] if totals else None
    if (not explicit_period and explicit_total is not None and counts
            and explicit_total % counts[0] == 0):
        weeks = explicit_total // counts[0]
        if not 1 <= weeks <= 26:
            raise ValueError(f'Запрошено {weeks} недель; поддерживается от 1 до 26')
    if counts:
        per_week = counts[0]
        cadence_source = "explicit"
    elif explicit_total is not None and explicit_total % weeks == 0:
        per_week = explicit_total // weeks
        cadence_source = "derived_from_total"
    else:
        per_week = 3  # historical default, not a user-provided fact
        cadence_source = "default"
    if not (1 <= per_week <= 7):
        raise ValueError('Для календаря допускается от 1 до 7 публикаций в неделю')
    calculated = weeks * per_week
    if explicit_total is not None and explicit_total != calculated:
        raise ValueError(f'Противоречивые требования: {weeks} недель × {per_week} публикаций = {calculated}, а указано {explicit_total}')
    return PublicationScope(weeks, per_week, calculated, explicit_total, cadence_source)


def delivery_audit(documents, scope, accepted_posts, *, required=('strategy', 'calendar')):
    """Validate the entire requested artifact set, not only post count."""
    problems=[]
    supplied = documents if isinstance(documents,list) else []
    malformed = [d for d in supplied if not isinstance(d,dict) or not isinstance(d.get('kind'),str)]
    if malformed or not isinstance(documents,list):
        problems.append('Некорректная структура документов выдачи')
    kinds=[d['kind'] for d in supplied if isinstance(d,dict) and isinstance(d.get('kind'),str)]
    if len(kinds)!=len(set(kinds)):
        problems.append('Повторяющиеся документы в выдаче; нельзя выбрать единственную утверждённую версию')
    by_kind={d['kind']:d for d in supplied if isinstance(d,dict) and isinstance(d.get('kind'),str)}
    for kind in required:
        item=by_kind.get(kind)
        if item is None:
            problems.append(f'Отсутствует результат: {kind}')
        elif item.get('valid') is not True:
            problems.append(f'Не прошёл проверку результат: {kind}')
        elif not isinstance(item.get('content'),str) or not item['content'].strip():
            problems.append(f'Пустой результат: {kind}')
    if 'calendar' in required:
        if not isinstance(accepted_posts, dict):
            problems.append('Контрольная точка публикаций имеет неверную структуру')
        else:
            counts=Counter()
            expected=set(publication_ids(scope.weeks,scope.per_week))
            received=set(accepted_posts)
            if received != expected:
                missing=expected-received
                unexpected=received-expected
                if missing:
                    problems.append('Отсутствуют обязательные календарные слоты: '+', '.join(sorted(missing)))
                if unexpected:
                    problems.append('Лишние или неверные календарные слоты: '+', '.join(sorted(str(x) for x in unexpected)))
            if len(accepted_posts) != scope.total:
                problems.append(f'Проверено {len(accepted_posts)} из {scope.total} публикаций')
            for ident, post in accepted_posts.items():
                match=re.fullmatch(r'(\d+)/([^/\s]+)',str(ident))
                if not match or not (1 <= int(match.group(1)) <= scope.weeks):
                    problems.append(f'Неверный идентификатор или неделя публикации: {ident}')
                    continue
                counts[int(match.group(1))] += 1
                if not isinstance(post,dict) or post.get('id') != ident:
                    problems.append(f'Неверная структура/идентификатор публикации: {ident}')
                    continue
                missing=[field for field in ('topic',)+PUBLICATION_FIELDS
                         if not isinstance(post.get(field),str) or not post[field].strip()]
                if missing:
                    problems.append(f'Незаполненные поля публикации {ident}: '+', '.join(missing))
            for week in range(1,scope.weeks+1):
                if counts[week] != scope.per_week:
                    problems.append(f'Неделя {week}: принято {counts[week]} из {scope.per_week} публикаций')
    return problems



def requested_media_assets(request):
    """Only explicit requests for actual generated media, not storyboard mentions."""
    from task_semantics import current_semantics
    semantic=current_semantics()
    if semantic is not None:return semantic['requested_media']
    s = re.sub(r'\s+', ' ', request or '').casefold()
    return bool(re.search(r'(?:создай|сгенерируй|подготовь|сделай|отрендери\w*)\s+'
                          r'(?:реальн\w*\s+|готов\w*\s+|отдельн\w*\s+)*'
                          r'(?:фотографи\w*|изображени\w*|картинк\w*|видеоролик\w*|видео)', s))


def delivery_report(request, scope, documents, accepted_posts):
    """Inventory EVERY produced content component; production briefs aren't media."""
    docs = {d['kind']: d for d in (documents if isinstance(documents,list) else [])
            if isinstance(d, dict) and isinstance(d.get('kind'),str)}
    accepted_posts = accepted_posts if isinstance(accepted_posts,dict) else {}
    dimensions = {field: sum(isinstance(post, dict) and
                             isinstance(post.get(field), str) and bool(post[field].strip())
                             for post in accepted_posts.values())
                  for field in PUBLICATION_FIELDS}
    requested_media = requested_media_assets(request)
    report = {
        'scope': {'weeks': scope.weeks, 'per_week': scope.per_week, 'planned_total': scope.total,
                  'cadence_source': scope.cadence_source},
        'deliverables': {kind: ('validated' if docs.get(kind,{}).get('valid') is True else 'incomplete')
                         for kind in ('strategy', 'calendar')},
        'checked_posts': len(accepted_posts),
        'content_components_checked': dimensions,
        'production_materials': 'requirements_only',
        'rendered_media': 'not_created' if requested_media else 'not_requested',
    }
    report['unfulfilled'] = delivery_audit(documents, scope, accepted_posts)
    if requested_media:
        report['unfulfilled'].append('Запрошенные готовые фото/видео не созданы; сценарий и список материалов их не заменяют')
    return report
