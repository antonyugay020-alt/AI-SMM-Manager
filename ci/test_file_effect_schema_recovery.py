import sqlite3
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
from file_effect_schema_recovery import install_schema

LEGACY = '''CREATE TABLE ai_file_effect_outbox (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, artifact_id TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL, request_sha256 TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('verify_receipt','clear_pending')),
    receipt_name TEXT NOT NULL, pending_name TEXT NOT NULL, receipt_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','done')),
    attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL)'''


def make_old():
    db = sqlite3.connect(':memory:')
    db.execute(LEGACY)
    db.execute("INSERT INTO ai_file_effect_outbox VALUES('old','p','a','sha','req','verify_receipt','r','n','{}','pending',2,'old failure','2026')")
    db.execute("INSERT INTO ai_file_effect_outbox VALUES('old2','p','b','sha','req','clear_pending','r','n','{}','done',3,'','2026')")
    db.commit()
    return db


def values(db, table='ai_file_effect_outbox'):
    return db.execute('SELECT * FROM '+table+' ORDER BY id').fetchall()


def orphan(db, mode):
    original = values(db)
    db.execute('ALTER TABLE ai_file_effect_outbox RENAME TO ai_file_effect_outbox_v1')
    if mode != 'rename_only':
        from file_effect_schema_recovery import _CREATE
        db.execute(_CREATE)
        if mode == 'partial':
            db.execute('INSERT INTO ai_file_effect_outbox SELECT * FROM ai_file_effect_outbox_v1 WHERE id=?', ('old',))
        if mode == 'full':
            db.execute('INSERT INTO ai_file_effect_outbox SELECT * FROM ai_file_effect_outbox_v1')
    db.commit()
    return original


def test_new_and_idempotent():
    with sqlite3.connect(':memory:') as db:
        install_schema(db)
        assert not db.in_transaction
        install_schema(db)
        assert not db.in_transaction
        assert "'export_document'" in db.execute("SELECT sql FROM sqlite_master WHERE name='ai_file_effect_outbox'").fetchone()[0]


def test_regular_legacy_upgrade_preserves_all_fields():
    with make_old() as db:
        expected=values(db)
        install_schema(db)
        assert values(db)==expected
        assert not db.in_transaction
        assert db.execute("SELECT name FROM sqlite_master WHERE name='ai_file_effect_outbox_v1'").fetchone() is None


@pytest.mark.parametrize('stage',['rename_only','new_empty','partial','full'])
def test_crashed_legacy_migration_recovers_without_loss(stage):
    with make_old() as db:
        expected=orphan(db,stage)
        install_schema(db)
        assert values(db)==expected
        assert not db.in_transaction
        assert db.execute("SELECT name FROM sqlite_master WHERE name='ai_file_effect_outbox_v1'").fetchone() is None
        index=db.execute("SELECT tbl_name FROM sqlite_master WHERE type='index' AND name='ai_file_effect_pending'").fetchone()
        assert index==('ai_file_effect_outbox',)


def test_conflicting_orphan_does_not_discard_either_table():
    with make_old() as db:
        orphan(db,'partial')
        db.execute("UPDATE ai_file_effect_outbox SET attempts=99 WHERE id='old'")
        db.commit()
        before=(values(db),values(db,'ai_file_effect_outbox_v1'))
        with pytest.raises(RuntimeError,match='Conflicting'):
            install_schema(db)
        assert (values(db),values(db,'ai_file_effect_outbox_v1'))==before
        assert not db.in_transaction


def test_unknown_orphan_columns_fail_without_mutation():
    with make_old() as db:
        orphan(db,'new_empty')
        db.execute('ALTER TABLE ai_file_effect_outbox_v1 ADD COLUMN unknown TEXT')
        db.commit()
        with pytest.raises(RuntimeError,match='Unknown'):
            install_schema(db)
        assert len(values(db,'ai_file_effect_outbox_v1'))==2
        assert not db.in_transaction


def test_ambiguous_old_and_orphan_fail_closed():
    with make_old() as db:
        db.execute('CREATE TABLE ai_file_effect_outbox_v1 (id TEXT PRIMARY KEY)')
        db.commit()
        with pytest.raises(RuntimeError,match='Ambiguous'):
            install_schema(db)
        assert len(values(db))==2
        assert not db.in_transaction


def test_outer_transaction_rolls_back_recovery():
    with make_old() as db:
        expected=orphan(db,'partial')
        db.execute('BEGIN IMMEDIATE')
        install_schema(db)
        assert db.in_transaction
        assert values(db)==expected
        db.rollback()
        assert values(db,'ai_file_effect_outbox_v1')==expected
        assert len(values(db))==1


def test_crash_during_recovery_rolls_back_partial_merge():
    class Failing(sqlite3.Connection):
        fail=False
        def execute(self,sql,*args,**kwargs):
            if self.fail and sql.startswith('DROP TABLE ai_file_effect_outbox_v1'):
                raise RuntimeError('simulated interruption')
            return super().execute(sql,*args,**kwargs)
    with make_old() as source:
        destination=sqlite3.connect(':memory:',factory=Failing)
        source.backup(destination)
    with destination as db:
        expected=orphan(db,'new_empty')
        db.fail=True
        with pytest.raises(RuntimeError,match='interruption'):
            install_schema(db)
        assert values(db)==[]
        assert values(db,'ai_file_effect_outbox_v1')==expected
        db.fail=False
        install_schema(db)
        assert values(db)==expected
