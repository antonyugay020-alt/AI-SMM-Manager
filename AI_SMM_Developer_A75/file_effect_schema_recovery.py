"""Crash-recoverable, transaction-composable schema for document effects.

Recover an old interrupted ALTER RENAME / CREATE / COPY / DROP migration,
including a partially populated replacement table. Never discard an orphan
legacy table or silently overwrite a divergent receipt.
"""
from __future__ import annotations

from sqlite_transaction_core import compose

_TABLE = 'ai_file_effect_outbox'
_LEGACY = 'ai_file_effect_outbox_v1'
_COLUMNS = ('id', 'project_id', 'artifact_id', 'artifact_sha256',
            'request_sha256', 'kind', 'receipt_name', 'pending_name',
            'receipt_json', 'state', 'attempts', 'last_error', 'created_at')
_COLUMN_SQL = ','.join(_COLUMNS)
_CREATE = '''CREATE TABLE IF NOT EXISTS ai_file_effect_outbox (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, artifact_id TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL, request_sha256 TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('export_document','verify_receipt','clear_pending')),
    receipt_name TEXT NOT NULL, pending_name TEXT NOT NULL, receipt_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','done')),
    attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL)'''


def _exists(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _schema(db):
    row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,)).fetchone()
    if not row:
        raise RuntimeError('Document-effect table is missing after schema creation')
    return row[0]


def _reconcile_orphan(db):
    if not _exists(db, _LEGACY):
        return
    columns = tuple(row[1] for row in db.execute('PRAGMA table_info(' + _LEGACY + ')'))
    if columns != _COLUMNS:
        raise RuntimeError('Unknown interrupted document-effect schema; legacy data preserved')
    if "'export_document'" not in _schema(db):
        raise RuntimeError('Ambiguous document-effect migration; both legacy tables preserved')
    placeholders = ','.join('?' for _ in _COLUMNS)
    for row in db.execute('SELECT ' + _COLUMN_SQL + ' FROM ' + _LEGACY):
        existing = db.execute('SELECT ' + _COLUMN_SQL + ' FROM ' + _TABLE + ' WHERE id=?', (row[0],)).fetchone()
        if existing is None:
            db.execute('INSERT INTO ' + _TABLE + '(' + _COLUMN_SQL + ') VALUES(' + placeholders + ')', row)
        elif existing != row:
            raise RuntimeError('Conflicting document-effect receipt; both tables preserved')
    db.execute('DROP TABLE ' + _LEGACY)


def install_schema(db):
    """Install/upgrade atomically, even if an older non-atomic upgrade crashed.

    On an existing caller transaction, only this savepoint is owned; the caller
    controls durability. A conflicting duplicate ID fails closed and restores
    both tables exactly as they were before this call.
    """
    with compose(db, 'file_effect_schema'):
        db.execute(_CREATE)
        if "'export_document'" not in _schema(db):
            if _exists(db, _LEGACY):
                raise RuntimeError('Ambiguous document-effect migration; both legacy tables preserved')
            db.execute('ALTER TABLE ' + _TABLE + ' RENAME TO ' + _LEGACY)
            # Use the non-IF-NOT-EXISTS form after rename: an unexpected
            # replacement is an error, not permission to discard legacy data.
            db.execute(_CREATE.replace('CREATE TABLE IF NOT EXISTS', 'CREATE TABLE', 1))
        _reconcile_orphan(db)
        db.execute('CREATE INDEX IF NOT EXISTS ai_file_effect_pending ON ai_file_effect_outbox(state,created_at)')
