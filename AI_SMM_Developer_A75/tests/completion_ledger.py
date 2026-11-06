"""Atomic finalization of a verified AI document and its original task.

A durable export outbox ACK does not by itself mean that the task was closed.
The completion ledger, execution journal, task registry transition and user
message commit together under one SQLite transaction. Replaying the transition
is idempotent; a different active task is never closed by a stale result.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from dialogue_execution import ProjectConnection, result_transaction


_KINDS = frozenset(('export_document', 'verify_receipt', 'clear_pending'))


def finalize_document(db: ProjectConnection, *, project_id: str, artifact_id: str,
                      request: str, task_id: str | None, notice: str) -> bool:
    """Return True for new completion, False if already committed.

    Raises for incomplete effects or a conflicting active task. The caller
    must own a clean GUI SQLite connection; all durable writes are atomic.
    """
    if not isinstance(db, ProjectConnection) or db.in_transaction:
        raise RuntimeError('Completion requires a clean transactional project connection')
    if not isinstance(request, str) or not request or not isinstance(notice, str) or not notice:
        raise ValueError('Missing completion origin or notification')
    if not isinstance(project_id, str) or not isinstance(artifact_id, str):
        raise ValueError('Invalid completion identity')
    reqsha = hashlib.sha256(request.encode('utf-8')).hexdigest()
    with result_transaction(db):
        db.execute('''CREATE TABLE IF NOT EXISTS ai_completed_artifacts (
            project_id TEXT NOT NULL, artifact_id TEXT NOT NULL,
            request_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(project_id, artifact_id))''')
        prior = db.execute('''SELECT request_sha256 FROM ai_completed_artifacts
            WHERE project_id=? AND artifact_id=?''',(project_id,artifact_id)).fetchone()
        if prior:
            if prior[0] != reqsha:
                raise ValueError('Previously completed artifact belongs to another request')
            return False
        doc = db.execute('SELECT content FROM manager_documents WHERE id=? AND project_id=?',
                         (artifact_id,project_id)).fetchone()
        if not doc or not isinstance(doc[0], str):
            raise ValueError('Document missing from originating project')
        sha = hashlib.sha256(doc[0].encode('utf-8')).hexdigest()
        effects = db.execute('''SELECT kind,state,artifact_sha256,request_sha256,receipt_json
            FROM ai_file_effect_outbox WHERE project_id=? AND artifact_id=?''',
            (project_id,artifact_id)).fetchall()
        if len(effects) != len(_KINDS) or {e[0] for e in effects} != _KINDS:
            raise ValueError('Document completion effect graph is incomplete')
        for kind, state, artifact_sha, request_sha, record_json in effects:
            if state != 'done' or artifact_sha != sha or request_sha != reqsha:
                raise ValueError('Document export or receipt not yet verified')
            record = json.loads(record_json)
            if (not isinstance(record,dict) or record.get('request')!=request or
                    record.get('artifact_id')!=artifact_id or
                    record.get('artifact_sha256')!=sha or
                    record.get('request_sha256')!=reqsha):
                raise ValueError('Completion evidence belongs to another task or revision')
        pending = db.execute('SELECT request FROM ai_pending_tasks WHERE project_id=?',
                             (project_id,)).fetchone()
        if pending:
            if pending[0] != request:
                raise ValueError('Another project task is active; stale result cannot complete it')
            from task_lifecycle import TaskLifecycle
            TaskLifecycle(db).finish(project_id,request,task_id=task_id)
        from stage57_core import receipt
        receipt(db,project_id,request,'document',doc[0],'saved',
                json.dumps({'artifact_id':artifact_id,'storage_check':'verified_export_and_receipt'}))
        db.execute('INSERT INTO messages(project_id,role,content,created_at) VALUES(?,?,?,?)',
                   (project_id,'assistant',notice,datetime.now(timezone.utc).isoformat()))
        db.execute('''INSERT INTO ai_completed_artifacts
            (project_id,artifact_id,request_sha256,created_at) VALUES(?,?,?,?)''',
            (project_id,artifact_id,reqsha,datetime.now(timezone.utc).isoformat()))
    return True
