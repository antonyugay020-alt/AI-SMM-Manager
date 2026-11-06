"""Independent deterministic cross-checks for a generated strategy.

These checks deliberately cover narrow, objectively verifiable contradictions.
They do not claim to replace semantic editorial judgment or factual checking.
"""
from __future__ import annotations
import re
import unicodedata


def normalize_unprovided_category(strategy: str, request: str):
    """Remove an inferred category from an unaccepted draft instead of stating unknown metadata."""
    from task_semantics import ai_content_review_active
    if ai_content_review_active():return strategy,[]
    if not isinstance(strategy,str) or not isinstance(request,str):
        raise TypeError('Документ стратегии и запрос должны быть строками')
    pattern=re.compile(
        r'(?im)^(?P<prefix>\s*(?:[-*+]\s*)?(?:\*\*|__)?'
        r'(?:business\s+)?(?:категори\w*|category)(?:\*\*|__)?\s*[:—-]\s*)'
        r'(?P<value>[^\r\n|]+)')
    source=request.casefold().replace('ё','е')
    edits=[]
    def replace(match):
        value=match.group('value').strip().strip('*_`')
        value=re.sub(r'\s*\([^)]*\)\s*$','',value).strip()
        candidate=re.sub(r'\s+',' ',value.casefold().replace('ё','е')).strip(' .,:;—-')
        if candidate and candidate in re.sub(r'\s+',' ',source):
            return match.group(0)
        edits.append({'from':match.group(0).strip(),'to':''})
        return ''
    result=pattern.sub(replace,strategy)
    return result,edits
from factual_claim_audit import scan_unsupported_business_claims

_CADENCE = re.compile(
    r'\b(?:по\s+)?(?P<count>\d+)\s+(?:публикаци\w*\s+)?в\s+недел\w*',
    flags=re.IGNORECASE)
_PERCENTAGE = re.compile(r'(?<!\d)\d{1,3}(?:[.,]\d+)?\s*%')
_SPECULATIVE = re.compile(r'\b(?:гипотез\w*|условн\w*|иллюстративн\w*|'
                          r'пример\w*|предполож\w*|после\s+замера|необходим\w*\s+уточн\w*|'
                          r'если\s+(?:есть|известн\w*|баз\w*|текущ\w*)|(?:неизвестн\w*|нет)\s+(?:баз\w*|данн\w*))\b',re.I)

_CADENCE_PHRASE = re.compile(
    r'(?P<prefix>\bпо\s+)?(?P<count>\d+)\s+(?:публикаци\w*\s+)?в\s+недел\w*', re.I)


def _undecorated_line(line: str) -> str:
    """Strip leading emoji/punctuation used as visual labels, not content."""
    value=str(line).lstrip()
    structure=re.match(r'^(?:(?:#{1,6}\s+)|(?:\d+\s*[.)-]\s+)|(?:[-*+]\s+))+',value)
    prefix=structure.group(0) if structure else ''
    content=value[len(prefix):]
    while content and unicodedata.category(content[0])[0] in {'S','P'} and content[0] not in '#*+-_`':
        content=content[1:].lstrip()
    if prefix:
        return prefix+content
    while value and unicodedata.category(value[0])[0] in {'S','P'} and value[0] not in '#*+-_`':
        value=value[1:].lstrip()
    return value


_POST_HEADING = re.compile(
    r'^\s*(?:[-*+]\s*)?(?:#{1,6}\s*)?(?:\d+\s*[.)-]\s*)?'
    r'(?:\*\*|__|`)?(?:публикация|пост)\s*#?\s*\d+\b',re.I)
_READY_COPY_LABEL = re.compile(
    r'^\s*(?:[-*+]\s*)?(?:#{1,6}\s*)?(?:\*\*|__)?'
    r'(?:(?:уникальный|полный|готовый)\s+){0,2}'
    r'текст(?:\s+(?:публикации|поста))?\s*(?:\*\*|__)?\s*:',re.I)


def _embedded_post_scope(strategy: str):
    """Return a section or list block containing repeated publication-ready copy.

    Labels are normalized for emoji decoration and allow common adjective-order
    variations ("полный уникальный текст" / "уникальный полный текст").
    """
    lines=strategy.splitlines(keepends=True)
    clean=[_undecorated_line(line) for line in lines]
    posts=[i for i,line in enumerate(clean) if _POST_HEADING.match(line)]
    labels=[i for i,line in enumerate(clean) if _READY_COPY_LABEL.match(line)]
    if len(posts)<2 or len(labels)<2:
        return None
    headings=[]
    for index,line in enumerate(clean):
        match=re.match(r'^\s*(#{1,6})\s+(.+?)\s*#*\s*(?:\r?\n)?$',line)
        if match:
            headings.append((index,len(match.group(1)),match.group(2).casefold()))
    candidates=[]
    for pos,(start,level,title) in enumerate(headings):
        end=len(lines)
        for later,other_level,_ in headings[pos+1:]:
            if other_level<=level:
                end=later
                break
        section_posts=sum(bool(_POST_HEADING.match(clean[i])) for i in range(start,end))
        section_labels=sum(bool(_READY_COPY_LABEL.match(clean[i])) for i in range(start,end))
        if section_posts>=2 and section_labels>=2:
            candidates.append((level,end-start,start,end,section_posts,section_labels))
    # A document title such as "SMM strategy" can enclose every nested
    # section. It is not itself the embedded publication block. When the only
    # qualifying section spans the whole document, prefer the narrower
    # numbered-post spans below; this preserves the actual strategy around
    # them instead of refusing to edit an over-broad root section.
    if len(candidates)==1 and candidates[0][2]==0 and candidates[0][3]==len(lines):
        candidates=[]
    if not candidates:
        # Small local models often emit plain section labels such as
        # ``Контент-план:`` instead of Markdown headings. Apply the same
        # structural test to those labels, but only remove the section when it
        # contains multiple numbered posts and multiple ready-copy fields.
        plain_headers=re.compile(
            r'^\s*(?:контент[- ]?план|план\s+публикаци\w*|'
            r'редакционн\w*\s+план|контентн\w*\s+матриц\w*)\s*[:—-]\s*$',re.I)
        plain_boundaries=re.compile(
            r'^\s*(?:примечани\w*|заключени\w*|вывод\w*|ограничени\w*|'
            r'источник\w*|следующ\w*\s+шаг\w*)\s*[:—-]\s*$',re.I)
        for start,line in enumerate(clean):
            if not plain_headers.match(line.strip()):
                continue
            end=next((i for i in range(start+1,len(lines))
                      if plain_boundaries.match(clean[i].strip())),len(lines))
            section_posts=sum(bool(_POST_HEADING.match(clean[i])) for i in range(start,end))
            section_labels=sum(bool(_READY_COPY_LABEL.match(clean[i])) for i in range(start,end))
            if section_posts>=2 and section_labels>=2:
                return {'start':start,'end':end,'posts':section_posts,
                        'labels':section_labels,'heading':'plain'}
        # Some models emit numbered post blocks without a surrounding section
        # heading. Remove only those bounded list blocks; retain later strategy
        # sections and table-based plans for the dedicated calendar stage.
        spans=[]
        plain_boundaries=re.compile(
            r'^\s*(?:примечани\w*|заключени\w*|вывод\w*|ограничени\w*|'
            r'источник\w*|следующ\w*\s+шаг\w*|общ\w*\s+правил\w*|'
            r'общ\w*\s+правил\w*\s+измерени\w*|измерени\w*|'
            r'общ\w*\s+метрик\w*)\s*[:—-]?\s*$',re.I)
        for offset,start in enumerate(posts):
            indent=len(lines[start])-len(lines[start].lstrip())
            next_post=posts[offset+1] if offset+1<len(posts) else len(lines)
            end=next_post
            for i in range(start+1,next_post):
                value=clean[i].strip()
                if not value:
                    continue
                current_indent=len(lines[i])-len(lines[i].lstrip())
                is_nested_bullet=value.startswith(('-','*','+','•','◦'))
                bold_heading=re.match(r'^\s*(?:\*\*|__)(.+?)(?:\*\*|__)\s*[:—-]?(?:\s+.*)?$',value)
                is_named_boundary=bool(bold_heading and plain_boundaries.match(
                    re.sub(r'[*_`]', '', bold_heading.group(1)).strip()))
                if (value.startswith('|') or re.match(r'^#{1,6}\s',value)
                        or is_named_boundary
                        or (current_indent<=indent and not is_nested_bullet)):
                    end=i
                    break
            spans.append((start,end))
        ready_labels=sum(sum(bool(_READY_COPY_LABEL.match(clean[i])) for i in range(start,end))
                         for start,end in spans)
        if len(spans)>=2 and ready_labels>=2:
            return {'start':spans[0][0],'end':spans[-1][1],
                    'posts':len(spans),'labels':ready_labels,'heading':'numbered_posts'}
        return None
    # Section meaning is determined by its contents, not by one expected
    # heading label. This handles matrices, calendars and other editorial
    # outlines while retaining strategic sections without ready-copy fields.
    _,_,start,end,section_posts,section_labels=max(candidates,
        key=lambda item:(item[0],-item[1],item[4]+item[5]))
    return {'start':start,'end':end,'posts':section_posts,'labels':section_labels}


def _publication_word(count: int) -> str:
    from russian_quantity import publication_count
    return publication_count(count)


def normalize_explicit_cadence(strategy: str, per_week: int) -> tuple[str, list[dict]]:
    """Replace only simple affirmative post-rate statements with the user's rate.

    The source value comes from `publication_scope`, which has already resolved
    the user's explicit cadence. Historical comparisons, negations, examples,
    and conditional statements remain untouched and are handled by validation.
    This is a deterministic consistency edit to an unaccepted draft.
    """
    from task_semantics import ai_content_review_active
    if ai_content_review_active():return strategy,[]
    if not isinstance(strategy,str) or type(per_week) is not int or per_week < 1:
        return strategy, []
    edits=[]
    out=[]
    cursor=0
    for match in _CADENCE_PHRASE.finditer(strategy):
        count=int(match.group('count'))
        if count == per_week:
            continue
        start=match.start()
        sentence_start=max(strategy.rfind(x,0,start) for x in ('.','!','?',';','\n'))+1
        sentence_end=min((x for x in (strategy.find('.',match.end()),strategy.find('!',match.end()),
                                       strategy.find('?',match.end()),strategy.find(';',match.end()),
                                       strategy.find('\n',match.end())) if x>=0),default=len(strategy))
        sentence=strategy[sentence_start:sentence_end].casefold()
        if re.search(r'\b(?:не|от|до|вместо|раньше|прежде|гипотез\w*|пример\w*|если|условн\w*)\b',sentence):
            continue
        prefix=match.group('prefix') or ''
        replacement=prefix+_publication_word(per_week)+' в неделю'
        out.append(strategy[cursor:start])
        out.append(replacement)
        cursor=match.end()
        edits.append({'from':match.group(0),'to':replacement})
    if not edits:
        return strategy,[]
    out.append(strategy[cursor:])
    return ''.join(out),edits


def bind_explicit_task_scope(strategy: str, request: str, per_week: int) -> tuple[str, list[dict]]:
    """Carry parsed task scope into the strategy as host-owned contract data.

    Models often omit a rate already present in the request. The workflow has
    parsed that rate for calendar production, so a separate language-model
    paraphrase must not decide whether the strategy reflects the requested
    scope. Add one canonical section for explicit cadence/total constraints;
    do not manufacture a user requirement when the application used its
    historical default.
    """
    from task_semantics import ai_content_review_active
    if ai_content_review_active():return strategy,[]
    if not isinstance(strategy,str) or not isinstance(request,str):
        return strategy, []
    try:
        from content_requirements import publication_scope
        scope=publication_scope(request)
    except (ImportError,TypeError,ValueError):
        return strategy, []
    if scope.cadence_source == 'default' and scope.explicit_total is None:
        return strategy, []
    stated_rates=[int(match.group('count')) for match in _CADENCE.finditer(strategy)]
    if stated_rates and all(rate==scope.per_week for rate in stated_rates):
        return strategy, []
    from russian_quantity import publication_count, week_count
    heading='## Параметры контентного плана'
    if re.search(r'(?im)^\s*##\s+Параметры контентного плана\s*$',strategy):
        return strategy, []
    lines=[heading,
        f'- Публикационный ритм: {publication_count(scope.per_week)} в неделю.',
        f'- Период: {week_count(scope.weeks)}.',
        f'- Всего: {publication_count(scope.total)} за указанный период.']
    return strategy.rstrip()+'\n\n'+'\n'.join(lines)+'\n', [{
        'field':'publication_scope','per_week':scope.per_week,
        'weeks':scope.weeks,'total':scope.total,
        'reason':'bound from parsed task contract'}]


def separate_embedded_publication_section(strategy: str) -> tuple[str, list[dict]]:
    """Remove a clearly bounded calendar section from an unaccepted strategy.

    Compound tasks have separate strategy and calendar artifacts. Small local
    models can follow the original request's post-copy requirements inside
    the strategy despite the strategy prompt. When the scope detector finds
    multiple numbered post blocks with ready-copy labels, the host can safely
    remove the enclosing Markdown calendar section: the dedicated calendar
    stage produces those deliverables from the unchanged request. This is
    applied only to a new/rejected draft, never to an already accepted file.
    """
    from task_semantics import ai_content_review_active
    if ai_content_review_active():return strategy,[]
    if not isinstance(strategy,str):
        return strategy,[]
    scope=_embedded_post_scope(strategy)
    if scope is None or scope['start'] is None:
        return strategy,[]
    lines=strategy.splitlines(keepends=True)
    start,end=scope['start'],scope['end']
    result=''.join(lines[:start]+lines[end:])
    if result.strip():
        return result,[{'start_line':start+1,'end_line':end,
                        'reason':'embedded ready publication section moved to the dedicated calendar stage'}]
    return strategy,[]


# A plan horizon is a source-owned constraint, not a free-form promise.
# Restrict deterministic checks to explicit document-duration claims; semantic
# comparison still belongs to an independent reviewer for all other prose.
_PERIOD_CLAIM = re.compile(
    r'(?i)\b(?:период\w*|срок\w*|длительност\w*|горизонт\w*|'
    r'план\w*|кампани\w*|реализаци\w*|календар\w*|программ\w*|'
    r'действи\w*)\b[^.!?\n]{0,80}?'
    r'\b(?:(?:на|за|в течение|около)\s+)?'
    r'(?P<duration>\d+|одн\w*|две|двух|два|три|четыре|пять|'
    r'шесть|семь|восемь|девять|десять|одиннадцать|двенадцать)\s+недел\w*', re.I)


def explicit_period_conflicts(strategy: str, request: str) -> list[str]:
    """Reject asserted strategy horizon conflicts with an explicit user period.

    This never rewrites a model's draft or infers duration from an implicit
    default; conditional, quoted and negated example periods need a human or
    semantic judge and are not rejected deterministically.
    """
    from professional_content import requested_weeks
    try:
        weeks, explicit=requested_weeks(request,with_source=True)
    except ValueError:
        return ['Невозможно однозначно определить период исходного задания']
    if not explicit:
        return []
    contradictions=[]
    for line in strategy.splitlines():
        for hit in _PERIOD_CLAIM.finditer(line):
            clause=line[max(0,hit.start()-8):hit.end()].casefold()
            if re.search(r'\b(?:не|вместо|пример\w*|гипотетическ\w*|условн\w*|если)\b',clause):
                continue
            try:
                actual,detected=requested_weeks(hit.group(0),with_source=True)
            except ValueError:
                continue
            if detected and actual!=weeks:
                contradictions.append('Стратегия меняет срок исходного задания: '
                    f'указано {actual} нед. вместо {weeks}; цитата: {hit.group(0)!r}')
    return list(dict.fromkeys(contradictions))


def strategy_integrity_issues(strategy: str, request: str, *, per_week: int,
                              evidence_context=None) -> list[str]:
    if not isinstance(strategy,str) or not isinstance(request,str):
        return ['Не удалось проверить параметры стратегии']
    from task_semantics import ai_content_review_active,current_semantics
    issues=explicit_period_conflicts(strategy, request)
    if ai_content_review_active():
        schedule=current_semantics().get('schedule')
        if not strategy.strip():return ['Модель не вернула содержимое стратегии', *issues]
        if schedule and per_week!=schedule['per_week']:
            return ['Нарушены закреплённые параметры исполнителя', *issues]
        return issues
    for match in _CADENCE.finditer(strategy):
        rate=int(match.group('count'))
        if rate != per_week:
            issues.append('Стратегия противоречит частоте из исходного задания: '
                          f'указано {rate} публикаций в неделю вместо {per_week}; '
                          'цитата: '+repr(match.group(0)))
            break
    # A strategy is a separate upstream artifact. Publication-ready copies
    # belong to the task's addressable calendar/post workflow, where every
    # item receives its own content and persistence checks. Detect repeated
    # post-level copy without rejecting a high-level content pillar matrix.
    if _embedded_post_scope(strategy) is not None:
        issues.append('Стратегия содержит готовые тексты нескольких публикаций; оставь в ней стратегические рекомендации, а тексты передай этапу календаря.')
    # Explicit user targets are not hallucinations, and hypotheses may be used
    # in forecasts. A new unsourced numerical growth target must be labelled
    # as a hypothesis in its own sentence/paragraph.
    request_percents={x.replace(' ','') for x in _PERCENTAGE.findall(request)}
    for line in strategy.splitlines():
        percents=_PERCENTAGE.findall(line)
        if not percents:
            continue
        line_low=line.lower()
        if not re.search(r'\b(?:рост|прирост|увеличен\w*|повышен\w*|ориентир\w*|цель\w*|'
                         r'подписчик\w*|охват\w*|запрос\w*|сообщен\w*|конверс\w*)',line_low):
            continue
        if _SPECULATIVE.search(line):
            continue
        if any(value.replace(' ','') not in request_percents for value in percents):
            issues.append('Целевой процент без исходных данных и без явной пометки гипотезы: '+repr(line.strip()[:170]))
            break
    for finding in scan_unsupported_business_claims(request, strategy, '', evidence_context,
                                                     include_semantic_facts=False):
        if finding['document'] == 'strategy':
            issues.append(f"{finding['reason']}: {finding['quote']!r}")
    return list(dict.fromkeys(issues))
