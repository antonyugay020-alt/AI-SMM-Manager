"""Atomic immutable provenance binding between a Kie intent and paid payload.

Created in the same transaction as kie_media_tasks. Missing legacy bindings
cannot certify a recovered prepared task; they require explicit reconciliation.
"""
from __future__ import annotations


def _digest(value):
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in '0123456789abcdef' for char in value)):
        raise ValueError('Invalid Kie intent/payload digest')
    return value


def install(db):
    db.execute('''CREATE TABLE IF NOT EXISTS kie_media_intent_bindings (
        job_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
        intent_sha256 TEXT NOT NULL, payload_sha256 TEXT NOT NULL)''')


def bind(db, project_id, job_id, intent_sha256, payload_sha256):
    if not db.in_transaction:
        raise RuntimeError('Kie intent binding must share task creation transaction')
    install(db)
    db.execute('''INSERT INTO kie_media_intent_bindings
        (job_id,project_id,intent_sha256,payload_sha256) VALUES (?,?,?,?)''',
        (job_id, project_id, _digest(intent_sha256), _digest(payload_sha256)))


def require_bound(db, project_id, job_id, intent_sha256, payload_sha256):
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='kie_media_intent_bindings'").fetchone() is None:
        raise ValueError('Unbound Kie prepared task requires manual reconciliation')
    receipt = db.execute('''SELECT intent_sha256,payload_sha256
        FROM kie_media_intent_bindings WHERE job_id=? AND project_id=?''',
        (job_id,project_id)).fetchone()
    if receipt != (_digest(intent_sha256), _digest(payload_sha256)):
        raise ValueError('Kie prepared task is not bound to the immutable intent')
    return True
