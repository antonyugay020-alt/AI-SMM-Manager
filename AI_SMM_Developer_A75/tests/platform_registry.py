"""Canonical platform names shared by request analysis and content planning.

Platform labels are routing metadata, never evidence about a business. Only a
literal match in the user's request is marked explicit; model proposals remain
recommendations and are shown as such in generated calendars.
"""
from __future__ import annotations

import re


_PLATFORM_ALIASES = (
    ('Instagram', (r'instagram', r'инстаграм')),
    ('Telegram', (r'telegram', r'телеграм(?:м)?')),
    ('ВКонтакте', (r'вконтакте', r'вконтаке', r'vkontakte', r'vk')),
    ('TikTok', (r'tiktok', r'тикток')),
    ('YouTube', (r'youtube', r'you\s*tube', r'ютуб')),
    ('Facebook', (r'facebook', r'фейсбук')),
    ('LinkedIn', (r'linkedin', r'линкедин')),
    ('Twitter', (r'twitter', r'твиттер')),
    ('Pinterest', (r'pinterest', r'пинтерест')),
    ('Threads', (r'threads', r'тредс')),
    ('Одноклассники', (r'одноклассники', r'одноклассниках')),
)


def explicit_platforms(request: str) -> list[str]:
    """Return distinct recognized platforms in the order named by the user."""
    if not isinstance(request, str):
        return []
    hits=[]
    for canonical, aliases in _PLATFORM_ALIASES:
        for alias in aliases:
            pattern=r'(?<![\w])'+alias+r'(?![\w])'
            for match in re.finditer(pattern,request,re.I):
                hits.append((match.start(),canonical))
    found=[]
    for _,canonical in sorted(hits):
        if canonical not in found:
            found.append(canonical)
    return found


def canonical_platform_name(value: str) -> str:
    """Normalize only a complete label; preserve unknown names and prose."""
    if not isinstance(value, str):
        raise TypeError('Название площадки должно быть строкой')
    text = value.strip()
    for canonical, aliases in _PLATFORM_ALIASES:
        if any(re.fullmatch(alias, text, re.I) for alias in aliases):
            return canonical
    return text


def canonicalize_platforms(value) -> list[str]:
    """Normalize model-returned platform labels without inventing aliases."""
    values=value if isinstance(value,list) else [value]
    result=[]
    for raw in values:
        text=str(raw or '').strip()
        if not text:
            continue
        matches=explicit_platforms(text)
        canonical=matches[0] if len(matches)==1 else text
        if canonical not in result:
            result.append(canonical)
    return result


def platform_label(platform: str, basis: str = 'explicit') -> str:
    value=str(platform or '').strip()
    if basis=='recommendation' and value:
        return value+' (предложение AI)'
    return value
