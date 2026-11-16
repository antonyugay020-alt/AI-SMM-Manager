"""SQLite-only regression for atomic legacy outbox upgrade and caller UoW.

No Qt, model, network, Windows bot, exporter or filesystem mutation needed.
"""
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
from file_effect_outbox import install


LEGACY='''CREATE TABLE ai_file_effect_outbox (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, artifact_id TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL, request_sha256 TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('verify_receipt','clear_pending')),
    receipt_name TEXT NOT NULL, pending_name TEXT NOT NULL, receipt_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','done')),
    attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL)'''


def old_db(factory=sqlite3.Connection):
    db=sqlite3.connect(':memory:',factory=factory)
    db.execute(LEGACY)
    db.execute("INSERT INTO ai_file_effect_outbox VALUES('old','p','a','sha','req','verify_receipt','r','n','{}','pending',2,'old failure','2026')")
    db.commit()
    return db


def rows(db):
    return db.execute('SELECT id,kind,state,attempts,last_error FROM ai_file_effect_outbox').fetchall()


def schema(db):
    return db.execute("SELECT sql FROM sqlite_master WHERE name='ai_file_effect_outbox'").fetchone()[0]


def test_install_new_schema_idempotent_without_open_transaction():
    with sqlite3.connect(':memory:') as db:
        install(db)
        assert not db.in_transaction
        install(db)
        assert not db.in_transaction
        assert "'export_document'" in schema(db)


def test_legacy_upgrade_preserves_pending_receipt_and_attempts():
    with old_db() as db:
        before=rows(db)
        install(db)
        assert rows(db)==before
        assert "'export_document'" in schema(db)
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='ai_file_effect_outbox_v1'").fetchone() is None


def test_upgrade_participates_in_foreign_transaction_without_commit():
    with old_db() as db:
        db.execute('BEGIN IMMEDIATE')
        install(db)
        assert db.in_transaction
        assert "'export_document'" in schema(db)
        db.rollback()
        assert "'export_document'" not in schema(db)
        assert rows(db)==[('old','verify_receipt','pending',2,'old failure')]


def test_upgrade_rolls_back_schema_after_mid_migration_error():
    class FailingConnection(sqlite3.Connection):
        fail=False
        def execute(self,sql,*args,**kwargs):
            if self.fail and sql.startswith('CREATE TABLE ai_file_effect_outbox ('):
                raise RuntimeError('simulated crash after legacy table rename')
            return super().execute(sql,*args,**kwargs)
    with old_db(FailingConnection) as db:
        db.fail=True
        with pytest.raises(RuntimeError,match='simulated crash'):
            install(db)
        db.fail=False
        assert rows(db)==[('old','verify_receipt','pending',2,'old failure')]
        assert "'export_document'" not in schema(db)
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='ai_file_effect_outbox_v1'").fetchone() is None
        install(db)
        assert "'export_document'" in schema(db)
        assert rows(db)==[('old','verify_receipt','pending',2,'old failure')]


def test_install_rollback_drops_uncommitted_new_schema():
    with sqlite3.connect(':memory:') as db:
        db.execute('BEGIN IMMEDIATE')
        install(db)
        db.rollback()
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='ai_file_effect_outbox'").fetchone() is None
