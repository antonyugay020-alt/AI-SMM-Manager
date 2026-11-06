"""Separate deterministic, offline contract tests from live provider acceptance.

The parent validation process may have API access and qualified live reviewers.
Those credentials and reviewer choices belong to *live acceptance only*.  A
unit-test subprocess must receive a fresh process environment so tests cannot
silently switch to cloud reviewers, incur paid API calls or load stale
qualification certificates.  The live workflow continues to use the parent's
unmodified configuration.
"""
from __future__ import annotations

from collections.abc import Mapping

_SECRET_SUFFIXES = ('_API_KEY', '_TOKEN', '_SECRET', '_PASSWORD')
_RUNTIME_FLAGS = frozenset({
    'AI_SMM_ACCEPTANCE_REVIEWER_MODELS', 'AI_SMM_ACCEPTANCE_AUTHOR_MODELS',
    'AI_SMM_CLOUD_MODELS', 'AI_SMM_ENABLE_OPENAI_API',
    'AI_SMM_ENABLE_OPENAI_ACCEPTANCE', 'AI_SMM_ENABLE_FREE_CLOUD_ACCEPTANCE',
    'AI_SMM_ENABLE_OLLAMA_CLOUD_ACCEPTANCE',
    'AI_SMM_REVIEW_QUALIFICATIONS',
    'AI_SMM_REVIEW_QUALIFICATION_CERTIFICATES',
})


def isolated_contract_test_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Return a copy with live provider and reviewer authorization stripped.

    This is intentionally not an application/provider setting. Only the unit
    test child runs in this environment; never apply it to the live GUI or to
    actual author/reviewer workflows. Tests that need provider credentials
    must inject fake or explicit scoped providers instead.
    """
    result = {}
    for key, value in source.items():
        upper = str(key).upper()
        if (upper in _RUNTIME_FLAGS or
                upper.startswith(('OPENAI_', 'ANTHROPIC_', 'GROQ_', 'TOGETHER_',
                                  'FIREWORKS_', 'DEEPSEEK_', 'CEREBRAS_')) or
                upper.endswith(_SECRET_SUFFIXES) or
                (upper.startswith('AI_SMM_') and any(token in upper for token in ('CERT', 'API_KEY', 'QUALIFICATION')))):
            continue
        result[str(key)] = str(value)
    return result
