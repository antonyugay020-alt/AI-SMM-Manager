"""Small shared Russian count forms used by generated document renderers."""
from __future__ import annotations


def publication_count(value: int) -> str:
    count = int(value)
    last_two, last = count % 100, count % 10
    noun = ('публикаций' if 11 <= last_two <= 14 or last == 0 or last >= 5
            else 'публикация' if last == 1 else 'публикации')
    return f'{count} {noun}'


def week_count(value: int) -> str:
    count = int(value)
    last_two, last = count % 100, count % 10
    noun = ('недель' if 11 <= last_two <= 14 or last == 0 or last >= 5
            else 'неделя' if last == 1 else 'недели')
    return f'{count} {noun}'
