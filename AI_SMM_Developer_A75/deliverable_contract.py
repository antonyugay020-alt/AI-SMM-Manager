"""Shared ownership rules for compound strategy and publication deliverables.

The strategy and calendar are separate artifacts in one task. A requirement
that describes each publication belongs to the publication workflow unless the
user explicitly assigns it to the strategy document. This boundary is used by
both generation-time and complete-bundle review.
"""
from __future__ import annotations

import re
import hashlib


REQUIREMENT_FAILURE_TYPES = (
    'verbatim_missing', 'required_content_missing', 'audience_mismatch',
    'objective_mismatch', 'style_mismatch', 'scope_mismatch', 'other_explicit_requirement',
)
_CONTEXT_LABELS = (
    ('audience_fit', r'(?:(?:целевая|основная)\s+)?аудитория|для\s+кого|(?:target\s+)?audience'),
    ('objective_fit', r'(?:цель|задача)(?:\s+(?:контента|поста|публикации|текста))?|goals?|objectives?'),
    ('style_fit', r'тон|стиль|tone|style'),
    ('fact_context', r'подтвержд[её]нн\w*\s+(?:услуг\w*|информаци\w*|факт\w*)|'
                     r'известн\w*\s+(?:сведени\w*|факт\w*)|verified\s+(?:facts|sources)|business\s+context'),
)
_LITERAL_DIRECTIVE = re.compile(
    r'\b(?:дословно|verbatim|упомян(?:и|уть)|назов(?:и|ите)|встав(?:ь|ьте)|'
    r'процитируй\w*|обязательно\s+включ\w*|mention|quote\s+the|'
    r'include\s+(?:the\s+)?(?:phrase|word))\b|'
    r'обязательн\w*[^.!?]{0,60}\b(?:слов\w*|фраз\w*|названи\w*)\b', re.I)


def labelled_context_role(text: str) -> str | None:
    """Recognize a labelled brief field once for review and source authority.

    This only describes the role of the exact field. Literal-copy directives
    remain a separate obligation and do not turn a goal into proof of a fact.
    """
    label = re.match(r'^(?:[-*#]\s*)*([^:：\n—–]{1,60}?)\s*(?:[:：—–]|\s-\s)\s*(.*)$',
                     str(text or '').strip(), re.S)
    if label:
        for role, pattern in _CONTEXT_LABELS:
            if re.fullmatch(pattern, label[1].strip(' \t*_#-'), re.I):
                return role
    return None


def review_requirement_bindings(request: str) -> list[dict]:
    """Issue source-owned IDs and distinguish brief context from literal copy.

    Only explicitly labelled context receives a specialized role. Ambiguous
    prose stays instruction-owned, and explicit mention/quotation requests
    always stay reviewable. Source wording is never rewritten.
    """
    parts = re.split(r'(?<=[.!?;])\s+|\n+', str(request or ''))
    bindings = []
    for part in parts:
        quote = part.strip()
        if not quote:
            continue
        mode = 'instruction'
        literal = bool(_LITERAL_DIRECTIVE.search(quote))
        if not literal:
            mode = labelled_context_role(quote) or mode
        ident = f'R{len(bindings)+1:04d}_' + hashlib.sha256(quote.encode('utf-8')).hexdigest()[:8]
        bindings.append({'id': ident, 'quote': quote, 'mode': mode,
                         'literal_directive': literal})
    return bindings


def bind_requirement_findings(issues, bindings):
    """Ground findings in request roles; never infer content from a model flag."""
    by_id = {item['id']: item for item in bindings}
    actionable, dismissed = [], []
    for issue in issues:
        ident = issue.get('requirement_id')
        failure = issue.get('failure_type')
        if ident not in by_id or failure not in REQUIREMENT_FAILURE_TYPES:
            raise ValueError('Замечание не связано с выданным требованием и типом нарушения')
        binding = by_id[ident]
        grounded = {**issue, 'requirement_quote': binding['quote'],
                    'requirement_mode': binding['mode']}
        if (failure == 'verbatim_missing' and not binding['literal_directive']
                and binding['mode']=='instruction'):
            raise ValueError('Дословность этого требования не запрошена; проверь смысловое выполнение '
                             'и выбери соответствующий тип нарушения либо сними необоснованное замечание')
        if failure == 'verbatim_missing' and not binding['literal_directive']:
            dismissed.append({**grounded,
                'adjudication': 'context_does_not_require_literal_mention',
                'adjudication_reason': 'Дословное воспроизведение именно этого требования не запрошено; смысловое выполнение проверяется отдельно.'})
        else:
            actionable.append(grounded)
    return actionable, dismissed


def planning_request_contract(request: str, total_slots: int | None = None) -> dict:
    """One source-owned objective contract for topic creation and plan review.

    Keep compound objectives intact. An analyser's shorter goal or a proposed
    editorial angle must not replace the user's requested content outcome.
    Unlabelled requirements remain in the contract rather than being guessed.
    """
    if total_slots is not None and (type(total_slots) is not int or total_slots < 1):
        raise ValueError('Нужен положительный объём плана')
    bindings = review_requirement_bindings(request)
    return {'total_slots': total_slots, 'source_requirements': bindings,
            'content_objectives': [b for b in bindings if b['mode'] == 'objective_fit'],
            'coverage_policy': (
                'План в целом должен раскрывать все содержательные цели исходного запроса. '
                'При одном слоте вся задача решается одной публикацией; нельзя заменить '
                'основное содержание подготовкой, общим советом или одним призывом к действию. '
                'При нескольких слотах цели можно распределить между ними. '
                'Конкретный ракурс выбирается из задачи, а не из заранее назначенного шаблона. '
                'Не добавляй факты ради полноты: неизвестный процесс описывай как общий '
                'образовательный пример без привязки к практике компании или как вопрос для уточнения.')}


_PER_PUBLICATION = re.compile(
    r"\b(?:для\s+)?кажд\w*\s+(?:публикац\w*|пост\w*|материал\w*)\b|"
    r"\b(?:each|every)\s+(?:post|publication|material)\b", re.I)
_POST_FIELDS = re.compile(
    r"\b(?:тем\w*|формат\w*|текст\w*|подпис\w*|визуал\w*|сценари\w*|"
    r"cta|призыв\w*|kpi|способ\w*\s+измерен\w*|метод\w*\s+измерен\w*|"
    r"аудитори\w*|материал\w*|topic|caption|visual|measurement|audience|materials)\b", re.I)


def is_publication_bundle_requirement(requirement: str, request: str = "") -> bool:
    """Whether a requirement is assigned to every post in the compound task."""
    text = str(requirement or "").strip()
    if not text or not _PER_PUBLICATION.search(text):
        return False
    if len(_POST_FIELDS.findall(text)) < 2:
        return False
    # Preserve a user's explicit artifact assignment. General "for every
    # publication" requirements are satisfied in the calendar, while a
    # directive that places them inside the strategy stays strategy-owned.
    explicit = re.search(
        r"(?:в\s+стратеги\w*|стратеги\w*\s+(?:должн\w*|должна|должен|включ\w*|содерж\w*))"
        r"[^.!?\n]{0,160}(?:публикац\w*|пост\w*|текст\w*|визуал\w*|cta|kpi)|"
        r"(?:публикац\w*|пост\w*|текст\w*|визуал\w*|cta|kpi)"
        r"[^.!?\n]{0,160}(?:в\s+стратеги\w*|стратеги\w*\s+(?:должн\w*|включ\w*))",
        str(request or ""), re.I)
    return explicit is None


def is_deferred_strategy_publication_requirement(issue: dict, request: str = "") -> bool:
    """Defer calendar-owned completeness findings during strategy-only review."""
    return (isinstance(issue, dict)
            and issue.get("document") == "strategy"
            and issue.get("type") == "missing_required_content"
            and is_publication_bundle_requirement(issue.get("requirement_quote", ""), request))


def editorial_form_bindings(scope=None):
    """Host-issued intrinsic form criteria, separate from content obligations."""
    if scope is not None and not isinstance(scope,dict):
        raise ValueError('Область критериев формы должна быть объектом')
    if isinstance(scope,dict) and any(
            not isinstance(scope.get(key,[]),list) or
            any(not isinstance(name,str) for name in scope.get(key,[]))
            for key in ('available_artifacts','pending_artifacts')):
        raise ValueError('Артефакты области проверки должны быть списками строк')
    strategy_only=(isinstance(scope,dict)
        and scope.get('available_artifacts')==['strategy']
        and 'calendar' in scope.get('pending_artifacts',[]))
    form=('G_strategy_form',
          'Форма текущей SMM-стратегии: разделы о целях, аудитории, подходе, канале, '
          'направлениях контента, метриках и действиях. Готовые тексты отдельных публикаций, '
          'их визуалы, CTA и показатели относятся к отложенному календарю, если пользователь '
          'явно не назначил их стратегии. Полнота публикаций проверяется в их артефакте; '
          'темы и направления стратегии не обязаны быть готовыми постами.') if strategy_only else (
          'G_requested_form',
          'Форма доступного артефакта соответствует запрошенной; наличие других артефактов проверяется отдельно.')
    return [{'id':ident,'quote':quote,'mode':'quality_policy','literal_directive':False}
            for ident,quote in (
                ('G_language','Язык пользовательского текста соответствует явно запрошенному языку, по умолчанию языку исходного задания; имена собственные и обозначения допустимы.'),
                form,
                ('G_requested_tone','Тон и подача доступного текста соответствуют исходному запросу.'),
                ('G_clarity','Текст должен быть понятным и связным; причина должна объяснять конкретную неясность.'),
                ('G_grammar','Отмечай конкретную грамматическую или языковую ошибку.'),
                ('G_repetition','Отмечай бессодержательное повторение, мешающее прочтению.'),
                ('G_editorial_meta','Не вставляй служебные инструкции и описание аудитории из ТЗ как готовую публичную речь для читателя (caption, CTA, реплики). '
                 'Стратегия и внутренние поля планирования вправе описывать аудиторию, цели, производство и измерение; '
                 'это не публичная речь. Оценивай роль конкретного поля и вид артефакта.'))]
