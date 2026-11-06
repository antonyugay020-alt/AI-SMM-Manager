"""Application-level crash recovery coordinator, independent from Qt.

Owns the ordered replay of document and text projections and the durable
completion ledger.  The UI receives immutable completion events after commit.
Recovery is project-scoped and fail-closed: malformed or ambiguous task
identities cannot be inferred from request text.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass

from completion_ledger import finalize_document
from file_effect_outbox import drain as drain_documents
from project_text_projection import drain as drain_text

_TASK_NAME = re.compile(r'pending_text_task_([0-9a-f]{32})\.json\Z')

@dataclass(frozen=True)
class CompletionEvent:
    project_id: str
    artifact_id: str
    kind: str
    title: str
    request: str
    task_id: str | None
    notice: str

@dataclass(frozen=True)
class RecoveryReport:
    documents: dict
    texts: dict
    completed: tuple[CompletionEvent, ...]
    rejected: tuple[str, ...]


def recover(db, workspace_for_project, export_document, notice_for_document, *, limit=1000):
    """Replay effects and atomically close only independently verified tasks.

    Errors are reported without modifying unrelated projects. Outbox failures
    stay pending and are retried at the next startup. Never use UI state to
    identify the original project or task.
    """
    if db.in_transaction:
        raise RuntimeError('Recovery requires an unowned SQLite connection')
    docs = drain_documents(db, workspace_for_project, limit=limit,
                           export_document=export_document)
    texts = drain_text(db, workspace_for_project, limit=limit)
    candidates = db.execute("""SELECT o.project_id,o.artifact_id,o.receipt_json,
        o.pending_name,d.kind,d.title FROM ai_file_effect_outbox o
        JOIN manager_documents d ON d.id=o.artifact_id AND d.project_id=o.project_id
        WHERE o.kind='clear_pending' AND o.state='done'
          AND NOT EXISTS (SELECT 1 FROM ai_file_effect_outbox e
              WHERE e.project_id=o.project_id AND e.artifact_id=o.artifact_id
              AND e.state<>'done')
          AND NOT EXISTS (SELECT 1 FROM ai_completed_artifacts c
              WHERE c.project_id=o.project_id AND c.artifact_id=o.artifact_id)
        ORDER BY o.created_at,o.id""").fetchall() if _table_exists(db,'ai_completed_artifacts') else _legacy_candidates(db)
    events=[]
    rejected=[]
    for pid,aid,raw,pending_name,kind,title in candidates:
        try:
            metadata=json.loads(raw)
            if not isinstance(metadata,dict) or not isinstance(metadata.get('request'),str) or not metadata['request']:
                raise ValueError('Missing durable task origin')
            request=metadata['request']
            pending=db.execute('SELECT request FROM ai_pending_tasks WHERE project_id=?',(pid,)).fetchone()
            if pending is None or pending[0]!=request:
                # An already superseded task is not an error and must never
                # modify the current project execution.
                continue
            match=_TASK_NAME.fullmatch(pending_name)
            task_id=match.group(1) if match else None
            notice=notice_for_document(kind,title)
            if finalize_document(db,project_id=pid,artifact_id=aid,
                                 request=request,task_id=task_id,notice=notice):
                events.append(CompletionEvent(pid,aid,kind,title,request,task_id,notice))
        except (ValueError,RuntimeError,TypeError,KeyError) as exc:
            rejected.append(f'{pid}:{aid}: {type(exc).__name__}: {exc}')
    return RecoveryReport(docs,texts,tuple(events),tuple(rejected))


def _table_exists(db,name):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone())


def _legacy_candidates(db):
    return db.execute("""SELECT o.project_id,o.artifact_id,o.receipt_json,
        o.pending_name,d.kind,d.title FROM ai_file_effect_outbox o
        JOIN manager_documents d ON d.id=o.artifact_id AND d.project_id=o.project_id
        WHERE o.kind='clear_pending' AND o.state='done'
          AND NOT EXISTS (SELECT 1 FROM ai_file_effect_outbox e
              WHERE e.project_id=o.project_id AND e.artifact_id=o.artifact_id
              AND e.state<>'done')
        ORDER BY o.created_at,o.id""").fetchall()
