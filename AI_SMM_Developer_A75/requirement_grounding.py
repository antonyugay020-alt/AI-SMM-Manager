"""Host-side grounding for claims that content is missing from a deliverable.

A citation proves where an instruction appears, but not that it asks for the
particular missing detail. This small, conservative check rejects requirement
claims whose cited instruction is prohibitive or semantically unrelated.
"""
from __future__ import annotations

import re

_NEGATIVE = re.compile(
    r"\b(?:не|нельзя|запрещ\w*|запрет\w*|без|don't|do\s+not|must\s+not|"
    r"cannot|can't|not\s+to|avoid|prohibit\w*|without)\b", re.I)
_STOP = set((
    'который которая которые которое этих этот эта это для при под над между или и а но '
    'необходимо отсутствует отсутствуют указано указывать добавлено добавить включить включено '
    'нужно требование требовал требовалось прямо запрос пользователь пользователя задании запросе '
    'there these those that this with from into each every must should need required include included '
    'provide about which what where when how missing absent specify specified requested requirement '
    'the and for into that this are was were have has your you user asked state state whether content '
).split())


def _terms(text: str) -> set[str]:
    tokens = re.findall(r"[a-zа-яё]{4,}|\b[A-Z]{2,8}\b", str(text or ''), re.I)
    result = set()
    for token in tokens:
        value = token.casefold().replace('ё', 'е')
        # Prefix normalization is intentionally shallow: it tolerates inflection
        # without making a single broad word count as proof of entailment.
        stem = value[:5] if re.search(r'[а-я]', value) else value[:6]
        if value in _STOP or any(stem.startswith(s) for s in (
                'указ', 'треб', 'польз', 'отсут', 'инфор', 'подтв', 'конкр',
                'request', 'specif', 'missin', 'absen', 'include')):
            continue
        if len(stem) >= 4:
            result.add(stem)
    return result


def requirement_quote_applies(requirement_quote: str, issue_reason: str) -> bool:
    """Require a positive instruction and more than incidental word overlap."""
    quote = str(requirement_quote or '').strip()
    reason = str(issue_reason or '').strip()
    if not quote or not reason or _NEGATIVE.search(quote):
        return False
    quoted_terms = _terms(quote)
    issue_terms = _terms(reason)
    overlap = quoted_terms & issue_terms
    # One shared term such as "method" may refer to different things (e.g.
    # a measurement method versus a teaching method). Require two independent
    # anchors for longer descriptions; short, field-specific requirements can
    # be grounded by one distinctive term.
    threshold = 1 if len(issue_terms) <= 3 else 2
    return len(overlap) >= threshold
