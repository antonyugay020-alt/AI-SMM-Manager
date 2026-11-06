"""Host-owned execution contract and stage outcome.

This module deliberately has no model, Qt or storage dependencies. It binds a
result to one task and one immutable requested output scope. Drafts and reviews
may be reused only by the matching task. Model-produced PASS labels never
create permission to mark a task complete: storage and independent acceptance
are checked by completion_gate separately.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Mapping

_TASK_RE = re.compile(r'^[0-9a-f]{32}$')
_STATES = frozenset({'blocked_input', 'blocked_generation', 'blocked_review',
                     'blocked_content', 'ready_for_persistence'})


@dataclass(frozen=True)
class ExecutionContract:
    task_id: str
    request_sha256: str
    weeks: int
    per_week: int
    required_total: int

    @classmethod
    def create(cls, task_id: str, request: str, weeks: int, per_week: int):
        if not isinstance(task_id, str) or not _TASK_RE.fullmatch(task_id):
            raise ValueError('Task identity must be a 32-digit lowercase hex string')
        if not isinstance(request, str) or not request.strip():
            raise ValueError('Original task request is required')
        if (type(weeks) is not int or type(per_week) is not int
                or not 1 <= weeks <= 26 or not 1 <= per_week <= 7):
            raise ValueError('Unsupported publication scope')
        return cls(task_id, hashlib.sha256(request.encode('utf-8')).hexdigest(),
                   weeks, per_week, weeks * per_week)

    def proof(self) -> dict:
        return {'schema_version': 1, 'task_id': self.task_id,
                'request_sha256': self.request_sha256,
                'scope': {'weeks': self.weeks, 'per_week': self.per_week,
                          'planned_total': self.required_total}}

    def matches(self, payload: Mapping) -> bool:
        if not isinstance(payload, Mapping) or set(payload) != set(self.proof()):
            return False
        return (payload.get('schema_version') == 1
                and payload.get('task_id') == self.task_id
                and payload.get('request_sha256') == self.request_sha256
                and payload.get('scope') == self.proof()['scope'])


@dataclass(frozen=True)
class WorkflowAssessment:
    """One authoritative classification, not an LLM self-evaluation."""
    state: str
    code: str
    expected: int
    accepted: int
    outstanding: tuple[str, ...]

    def __post_init__(self):
        if self.state not in _STATES:
            raise ValueError('Unknown workflow state')

    def record(self):
        return {'schema_version': 1, 'state': self.state, 'code': self.code,
                'expected': self.expected, 'accepted': self.accepted,
                'outstanding': list(self.outstanding)}


def assess_production(required_ids, accepted_ids, *, plan_reviewed, editor_available,
                      pending_findings=(), production_error=None):
    """Separate unavailable infrastructure from fixable content problems.

    A finished plan and complete output set are necessary but not sufficient:
    the independent final review and persisted-file gate must still pass.
    """
    required = tuple(required_ids)
    if not required or len(set(required)) != len(required) or any(not isinstance(x, str) or not x for x in required):
        raise ValueError('Required outputs must be unique nonempty identifiers')
    accepted = set(accepted_ids)
    if not accepted <= set(required):
        raise ValueError('An unrequested output cannot count towards completion')
    missing = tuple(ident for ident in required if ident not in accepted)
    if not plan_reviewed:
        return WorkflowAssessment('blocked_review', 'plan_review_unavailable', len(required), len(accepted), missing)
    if not editor_available:
        return WorkflowAssessment('blocked_review', 'independent_review_unavailable', len(required), len(accepted), missing)
    if production_error:
        return WorkflowAssessment('blocked_generation', 'generation_failed', len(required), len(accepted), missing)
    if pending_findings:
        return WorkflowAssessment('blocked_content', 'editorial_findings', len(required), len(accepted), missing)
    if missing:
        return WorkflowAssessment('blocked_content', 'missing_outputs', len(required), len(accepted), missing)
    return WorkflowAssessment('ready_for_persistence', 'awaiting_storage_and_final_review',
                              len(required), len(accepted), ())


def verify_ready_record(record, required_count: int) -> bool:
    return (isinstance(record, dict) and record.get('schema_version') == 1
            and record.get('state') == 'ready_for_persistence'
            and record.get('code') == 'awaiting_storage_and_final_review'
            and type(record.get('expected')) is int
            and type(record.get('accepted')) is int
            and record.get('expected') == required_count
            and record.get('accepted') == required_count
            and record.get('outstanding') == [])


_RECOVERY_ACTIONS = {
    'plan_review_unavailable': 'requalify_reviewer_then_resume_plan',
    'independent_review_unavailable': 'requalify_reviewer_then_resume_review',
    'generation_failed': 'resume_unfinished_generation',
    'editorial_findings': 'repair_cited_artifacts_only',
    'missing_outputs': 'resume_missing_outputs_only',
    'awaiting_storage_and_final_review': 'final_review_then_persist',
}


def next_recovery_action(assessment: WorkflowAssessment) -> str:
    """Typed next step; never regenerate accepted artifacts to fix reviewer outage."""
    if not isinstance(assessment, WorkflowAssessment):
        raise TypeError('Workflow assessment required')
    try:
        return _RECOVERY_ACTIONS[assessment.code]
    except KeyError as exc:
        raise ValueError('Unknown recovery code') from exc
