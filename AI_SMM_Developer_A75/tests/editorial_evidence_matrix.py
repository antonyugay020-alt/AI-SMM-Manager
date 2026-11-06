# -*- coding: utf-8 -*-
"""Evidence matrix for independently checking *absence* claims made by an LLM.

A quotation proves that a row exists, not that some field is absent. This module
matches an editor's missing-field assertion to complete, original Markdown
rows, checks the corresponding field, and records a narrowly disproved claim.
It never approves a document as semantically correct and never suppresses a
quality, business-fact, safety, or unsupported-claim finding.
"""
from __future__ import annotations
import re
from typing import Any
from publication_schema import publication_field_type

SLOT_RE = re.compile(r'^\s*\d+\s*/\s*[^|\s]{1,20}\s*$')
ABSENCE_RE = re.compile(r'отсутств\w*|\bнет\b|не\s+(?:указан\w*|заполнен\w*|представлен\w*|содержит\w*)',re.I)


def _cells(line: str) -> list[str]:
    # A text \\| inside a caption is not a Markdown table delimiter.
    parts = re.split(r'(?<!\\)\|', line.strip())
    if parts and parts[0]=='': parts=parts[1:]
    if parts and parts[-1]=='': parts=parts[:-1]
    return [s.strip().replace('\\|','|') for s in parts]


def publication_rows(calendar: str) -> list[dict[str, Any]]:
    """Only trust rows with the expected named columns, not arbitrary `|` text."""
    if not isinstance(calendar,str):
        return []
    headers=None
    rows=[]
    for line in calendar.splitlines():
        if not line.lstrip().startswith('|'):
            continue
        parts=_cells(line)
        if len(parts)<5:
            continue
        if publication_field_type(parts[0])=='slot':
            headers=[x.casefold() for x in parts]
            continue
        if headers and len(parts)==len(headers) and SLOT_RE.fullmatch(parts[0]):
            rows.append({'slot':parts[0], 'raw':line,
                         'fields':{headers[i]:parts[i] for i in range(len(parts))},
                         'typed_fields':{publication_field_type(headers[i]):parts[i]
                                         for i in range(len(parts))}})
    return rows


def _cell(row: dict, keywords: tuple[str,...]) -> str | None:
    for head, value in row['fields'].items():
        if any(k in head for k in keywords):
            return value
    return None


def structural_digest(calendar: str) -> str:
    """Summarise *only field presence*, never infer business facts from text."""
    rows=publication_rows(calendar)
    if not rows: return 'Структурированная таблица публикаций не обнаружена.'
    summary=[]
    for row in rows:
        parts=[row['slot']]
        for label,words in (
            ('текст',('текст','подпись','caption')),
            ('визуал',('визуал','сценар')),
            ('CTA',('cta','призыв')),
            ('KPI',('kpi','метрик','показател')),
        ):
            value=_cell(row,words)
            parts.append(f'{label}=' + ('заполнено' if value and value.strip() else 'пусто') if value is not None else f'{label}=столбца нет')
        summary.append('; '.join(parts))
    return '\n'.join(summary)


def disproved_missing_field_claim(issue: dict, calendar: str) -> bool:
    """True iff complete source row *directly refutes* this specific absence claim.

    Presence and quality are separate dimensions: a short caption disproves
    literal absence, while an independent quality finding may still assess it.
    """
    if not isinstance(issue,dict) or issue.get('document')!='calendar' or issue.get('type')!='missing_required_content':
        return False
    reason=str(issue.get('reason') or '').casefold()
    if not ABSENCE_RE.search(reason):
        return False
    quote=str(issue.get('quote') or '').strip()
    rows=publication_rows(calendar)
    matches=[row for row in rows if quote and (quote in row['raw'] or row['raw'] in quote)]
    if len(matches)!=1: return False
    row=matches[0]
    if re.search(r'способ\w*\s+измер|измерен|отслеживан',reason):
        value=_cell(row,('метод измерения','способ измерения','measurement'))
        return bool(value and value.strip())
    if re.search(r'\b(?:cta|призыв)\b',reason):
        value=_cell(row,('cta','призыв'))
        return bool(value and len(value.strip())>=8)
    if re.search(r'визуал|сценари|кадр|съ[её]мк',reason):
        value=_cell(row,('визуал','сценар'))
        return bool(value and len(value.strip())>=60)
    if re.search(r'\bkpi\b|метрик|показател',reason):
        value=_cell(row,('kpi','метрик','показател'))
        return bool(value and len(value.strip())>=15)
    if re.search(r'\bтекст\w*|подпис|caption|реплик',reason):
        value=_cell(row,('текст','подпись','caption'))
        if not value or not value.strip():
            return False
        if re.search('уникальн|повтор|одинаков', reason):
            fingerprints=[re.sub(r'\W+',' ',(_cell(r,('текст','подпись','caption')) or '').casefold()).strip()
                          for r in rows]
            return bool(fingerprints[rows.index(row)]) and len(set(fingerprints))==len(fingerprints)
        return True
    return False


def disproved_document_role_claim(issue: dict, request: str, calendar: str) -> bool:
    """A per-publication requirement belongs in the calendar unless brief says otherwise.

    For example, an editor must not demand every storyboard inside the high-level
    strategy when all requested storyboards are present in the calendar.
    Only mechanically verifiable presence is eligible for dismissal.
    """
    if not isinstance(issue,dict) or issue.get('document')!='strategy' or issue.get('type')!='missing_required_content':
        return False
    reason=str(issue.get('reason') or '').casefold()
    if not ABSENCE_RE.search(reason): return False
    if not re.search(r'публикаци\w*|\bпост\w*|сторис|reels|визуальн\w*\s+сценари\w*',reason):
        return False
    brief=str(request or '').casefold()
    # The user's *explicit* allocation of fields overrides our general
    # document responsibilities.
    if re.search(r'(?:в\s+стратеги\w*|стратегия\s+должн\w*).{0,130}'
                 r'(?:визуал|сценари|публикаци|cta|текст|kpi)', brief):
        return False
    rows=publication_rows(calendar)
    if not rows: return False
    checks=(
        ('визуал|сценари|кадр',('визуал','сценар'),60),
        ('текст|caption|подпис',('текст','подпись','caption'),1),
        ('\\bcta\\b|призыв',('cta','призыв'),8),
        ('\\bkpi\\b|метрик',('kpi','метрик','показател'),15),
    )
    for expression,headings,min_chars in checks:
        if re.search(expression,reason):
            return all(len((_cell(row,headings) or '').strip())>=min_chars for row in rows)
    return False


def disproved_unrequested_contact_link(issue: dict, request: str) -> bool:
    """A reviewer cannot make an external link a prerequisite without user intent."""
    if not isinstance(issue,dict) or issue.get('document')!='calendar' or issue.get('type')!='missing_required_content':
        return False
    reason=str(issue.get('reason') or '').casefold()
    if not re.search(r'ссылк\w*|url|в\s+биографи\w*|link\s+in\s+bio',reason):
        return False
    brief=str(request or '').casefold()
    # An address being unknown is not authorization to invent one, nor a request
    # to force a link. Only explicit instructions to add a link make it required.
    requested=re.search(r'(?:добав\w*|размест\w*|укаж\w*|включ\w*|использ\w*|обязательн\w*)[^.!?\n]{0,100}(?:ссылк\w*|url|link)'
                        r'|(?:ссылк\w*|url|link)[^.!?\n]{0,100}(?:добав\w*|размест\w*|укаж\w*|включ\w*|обязательн\w*)',brief)
    return requested is None


def disproved_user_stated_fact(issue: dict, request: str) -> bool:
    """Dismiss a factual accusation only when the brief positively authorizes its claim.

    A related industry term is not enough: concrete prices, guarantees, dates,
    availability, testimonials and outcomes remain restricted. Service and booking
    claims must be explicitly present in a positive clause.
    """
    if not isinstance(issue,dict) or issue.get('type') not in ('invented_fact','contradiction'):
        return False
    reason=str(issue.get('reason') or '').casefold()
    if not re.search(r'не\s+(?:подтвержд\w*|указан\w*|сообщен\w*)|неизвестн\w*|выдуманн\w*|не\s+соответств\w*|противореч\w*|ограничени\w*\s+(?:пользовател|запрос)',reason):
        return False
    claims=re.findall(r"[«“\"]([^»”\"]+)[»”\"]",str(issue.get('reason') or ''))
    if not claims:
        claims=[str(issue.get('quote') or '')]
    source=str(request or '').casefold().replace('ё','е')
    forbidden=('бесплатн','скидк','акци','цен','стоимост','рубл','тенге','доллар',
               'гарантир','гарант','отзыв','клиентск','результат','процент','выручк',
               'адрес','срок','безопасност','эффективност','комфорт')
    stop={'запишитесь','запишись','записаться','обращайтесь','предлагаем','предлагает',
          'предлагаю','приходите','получите','можете','можно','пожалуйста','клиентам',
          'клиентов','компании','компания','вещам','вашим','вашего','ваша','ваши','вместе'}
    # Evidence must co-occur in a single affirmative business-fact clause.
    # Matching each word somewhere in the whole request can combine an
    # audience desire with a separate service description and falsely certify
    # an unsupported business outcome.
    clauses=re.split(r'(?<=[.!?;])\s+|\n+',source)
    support_clauses=[]
    for clause in clauses:
        if re.search(r'\b(?:аудитори\w*|клиент\w*|люд\w*|потребност\w*)\b.{0,100}'
                     r'\b(?:хот\w*|жел\w*|стрем\w*|важн\w*|интерес\w*)',clause):
            continue
        if re.search(r'\b(?:цель|задача|желани\w*|потребност\w*)\b',clause):
            continue
        if re.search(r'(?:не\s+(?:придумывай|выдумывай|обещай|подтвержден\w*)|'
                     r'неизвестн\w*|не\s+определен\w*|нет\s+(?:данн\w*|информаци\w*))',clause):
            continue
        support_clauses.append(clause)
    def supported_in_one_clause(tokens):
        for clause in support_clauses:
            words=re.findall(r'[a-zа-я]{4,}',clause)
            stems={word[:7] for word in words}
            if all(token[:7] in stems for token in tokens):
                return True
        return False
    for claim in claims:
        normalized=str(claim).casefold().replace('ё','е')
        if any(token in normalized for token in forbidden):
            continue
        tokens=[t for t in re.findall(r'[a-zа-я]{4,}',normalized) if t not in stop]
        if not tokens or not supported_in_one_clause(tokens):
            continue
        # A booking CTA is authorized only if the brief positively says that
        # the business accepts or schedules appointments.
        if re.search(r'\bзапиш(?:итесь|ись|аться)\b',normalized):
            if not re.search(r'(?:принима\w*[^.!?]{0,100}|работа\w*[^.!?]{0,100}|запис\w*[^.!?]{0,70})'
                             r'(?:по\s+)?предварительн\w*\s+запис',source):
                continue
        return True
    return False


def disproved_calendar_format_strategy_claim(issue: dict, calendar: str) -> bool:
    """A calendar may specify post formats and on-screen copy beyond the strategy."""
    if not isinstance(issue,dict) or issue.get('document')!='calendar':
        return False
    reason=str(issue.get('reason') or '').casefold()
    if not re.search(r'стратеги\w*\s+не\s+(?:указан\w*|содержит\w*|описан\w*)',reason):
        return False
    if not re.search(r'формат|текстов\w*\s+слайд|слайд|титр|визуальн\w*\s+сценари',reason):
        return False
    quote=str(issue.get('quote') or '').strip()
    rows=publication_rows(calendar)
    return bool(quote and any(quote in row['raw'] or row['raw'] in quote for row in rows)
                and all((_cell(row,('визуал','сценар')) or '').strip() for row in rows))
