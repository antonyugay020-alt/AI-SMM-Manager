"""Deterministic, confirmation-gated project data controls (no LLM deletion claims)."""
import re

KINDS = {'стратег': 'strategy', 'контент-план': 'calendar', 'контент план': 'calendar', 'планов': 'calendar', 'планы': 'calendar'}

def parse_command(message):
    s = re.sub(r'\s+', ' ', message.casefold()).strip()
    if not re.search(r'\b(удали|удалить|очисти|очистить|убери|убрать|сбрось|сбросить|сотри|стереть)\b', s):
        return None
    if re.search(r'(весь проект|всё в проекте|все в проекте|проект полностью|полностью очист|начать с нуля|начнём с нуля|очист.*проект|сброс.*проект)', s):
        return {'scope': 'project'}
    if re.search(r'(истори[юя] (?:чата|переписки)|чат полностью|очисти чат)', s) and not re.search(r'документ|стратег|план|исходник',s):
        return {'scope': 'chat'}
    if re.search(r'(исходник|исходн\w* файл|материал)', s):
        return {'scope': 'assets'}
    for needle, kind in KINDS.items():
        if needle in s:
            return {'scope': 'documents', 'kind': kind, 'query': s}
    if re.search(r'документ|файл плана',s):
        return {'scope': 'documents', 'kind': None, 'query': s}
    return None

def choose_documents(rows, query):
    """rows ordered like UI: (id, kind, title, created_at). Never guess ambiguities."""
    q = query.casefold()
    if re.search(r'\b(все|всё|всех)\b', q):
        return rows
    words = {'перв': 0, 'втор': 1, 'трет': 2, 'четверт': 3, 'пят': 4}
    for prefix, index in words.items():
        if re.search(r'\b'+prefix+r'\w*\b', q):
            return [rows[index]] if len(rows) > index else []
    if 'последн' in q or 'новую' in q:
        return [max(rows, key=lambda x:x[3])] if rows else []
    # Match by document title, including a useful partial title provided by the user.
    matched = [r for r in rows if len(r[2]) >= 5 and r[2].casefold() in q]
    if not matched:
        candidates = [r for r in rows if len(r[2]) >= 5 and any(
            len(token) >= 5 and token in q for token in re.findall(r'[\w-]+',r[2].casefold())
            if token not in ('проект','стратегия','контент-план','контент','план'))]
        if len(candidates)==1: matched=candidates
    if len(matched) == 1:
        return matched
    # A unique document of the requested type is unambiguous.
    if len(rows) == 1:
        return rows
    # No implicit delete of a random document.
    return []
