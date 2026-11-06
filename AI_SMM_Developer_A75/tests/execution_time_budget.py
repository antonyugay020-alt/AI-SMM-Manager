"""Shared, bounded model-call budgets for live GUI workers.

The transport owns enforcement of per-call limits. The GUI establishes one
budget around the entire dialogue or content operation; it does not create
independent deadlines for each failed model attempt. The existing durable
checkpoints remain authoritative after a deadline expires.
"""
from __future__ import annotations

from contextlib import contextmanager
import math
import os

from ollama_resilience import request_deadline

# Leave time for final SQLite publication and Qt callbacks before the external
# 1500 s full-workflow acceptance watchdog. These are operational bounds,
# not proof that every content task completes within one window.
_DEFAULTS = {
    'dialogue': (180, 15, 180),
    'content': (1140, 300, 180),
}


def _configured_seconds(value, default, minimum, maximum):
    try:
        amount = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(amount):
        return default
    return min(max(amount, minimum), maximum)


def worker_budget(stage: str):
    """One immutable time budget per production worker, not per model retry.

    No API keys, prompts or generated text are written to this policy. A
    timeout raises through the normal worker failure path so the originating
    project receives an auditable failure rather than false completion.
    """
    if stage not in _DEFAULTS:
        raise ValueError('Unknown worker budget stage')
    default, reserve, max_call = _DEFAULTS[stage]
    selected = _configured_seconds(os.environ.get('AI_SMM_' + stage.upper() + '_BUDGET_SECONDS'),
                                   default, 60 if stage=='dialogue' else 420, 1200)
    # The reviewer reserve cannot exceed the whole budget. Smaller explicitly
    # configured windows remain valid and fail closed when time runs out.
    reviewer_reserve = min(reserve, max(0.0, (selected - 60) / 2)) if stage == 'content' else 0.0
    backup_reserve = min(120.0, max(0.0, (selected - reviewer_reserve - 60) / 2)) if stage == 'content' else 0.0
    return dict(seconds=selected, max_call_seconds=min(max_call, selected),
                reviewer_reserve_seconds=reviewer_reserve,
                reviewer_backup_reserve_seconds=backup_reserve,
                retry_timeouts=False, abort_case_on_timeout=True)


@contextmanager
def worker_model_budget(stage: str):
    """Install a deadline in the current QThread; always reset it afterward."""
    options=worker_budget(stage)
    with request_deadline(**options) as state:
        yield state
