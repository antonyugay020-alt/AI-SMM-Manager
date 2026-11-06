"""Process-local, explicit provider credentials; never mutate os.environ.

Passing an explicit environment mapping is a testable isolated dependency and
must not accidentally consult the process-global session key.
"""
from __future__ import annotations
import os
from threading import RLock

_LOCK = RLock()
_OPENAI_KEY: str | None = None


def set_openai_key(key: str) -> None:
    value = str(key or '').strip()
    if not value or '\n' in value or '\r' in value:
        raise ValueError('Нужен непустой ключ OpenAI без переносов строк')
    global _OPENAI_KEY
    with _LOCK:
        _OPENAI_KEY = value


def clear_openai_key() -> None:
    global _OPENAI_KEY
    with _LOCK:
        _OPENAI_KEY = None


def get_openai_key(*, environ=None) -> str:
    if environ is not None:
        return str(environ.get('OPENAI_API_KEY', '') or '').strip()
    with _LOCK:
        if _OPENAI_KEY is not None:
            return _OPENAI_KEY
    return str(os.environ.get('OPENAI_API_KEY', '') or '').strip()
