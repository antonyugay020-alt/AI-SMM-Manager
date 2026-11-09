"""Legacy task identity migration is one atomic SQLite unit of work.

Tests do not call Qt, models, providers, or project filesystem.
"""
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
from task_registry import ensure_registry


def db_with_legacy():
    db=sqlite3.connect(':memory:')
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('CREATE TABLE projects (id TEXT PRIMARY KEY)')
    db.executemany('INSERT INTO projects VALUES(?)',[('p1',),('p2',)])
    db.execute('CREATE TABLE ai_pending_tasks(project_id TEXT,request TEXT,question TEXT,revision_id TEXT)')
    db.executemany('INSERT INTO ai_pending_tasks VALUES(?,?,?,?)',
                   [('p1','request1','question1',None),('p2','request2','question2',None)])
    db.commit()
    return db


def active(db):
    return db.execute("SELECT project_id,request,legacy_origin FROM ai_task_registry WHERE status='active' ORDER BY project_id").fetchall()


def test_legacy_task_migration_is_idempotent_and_commits_cleanly():
    with db_with_legacy() as db:
        ensure_registry(db)
        assert not db.in_transaction
        assert active(db)==[('p1','request1',1),('p2','request2',1)]
        ensure_registry(db)
        assert active(db)==[('p1','request1',1),('p2','request2',1)]


def test_conflicting_second_legacy_pointer_rolls_back_first_migration():
    with db_with_legacy() as db:
        db.execute('DELETE FROM ai_pending_tasks')
        db.commit()
        ensure_registry(db)
        db.execute("INSERT INTO ai_task_registry VALUES('existing','p2','different','q',NULL,'active','2026','2026',0)")
        db.executemany('INSERT INTO ai_pending_tasks VALUES(?,?,?,?)',
                       [('p1','request1','question1',None),('p2','request2','question2',None)])
        db.commit()
        with pytest.raises(ValueError,match='legacy'):
            ensure_registry(db)
        assert active(db)==[('p2','different',0)]
        assert not db.in_transaction


def test_foreign_transaction_keeps_schema_and_migration_under_caller_control():
    with db_with_legacy() as db:
        db.execute('BEGIN IMMEDIATE')
        ensure_registry(db)
        assert db.in_transaction
        assert len(active(db))==2
        db.rollback()
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='ai_task_registry'").fetchone() is None
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='ai_task_contexts'").fetchone() is None


def test_legacy_context_binding_commits_with_task_identity():
    with db_with_legacy() as db:
        db.execute('CREATE TABLE ai_semantic_tasks(project_id TEXT,request TEXT,context_json TEXT)')
        db.execute("INSERT INTO ai_semantic_tasks VALUES('p1','request1','{\"source\":\"v1\"}')")
        db.commit()
        ensure_registry(db)
        assert db.execute('SELECT context_json FROM ai_task_contexts').fetchall()==[('{"source":"v1"}',)]


def test_missing_legacy_pointer_table_still_creates_registry_atomically():
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        db.commit()
        ensure_registry(db)
        assert not db.in_transaction
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='ai_task_registry'").fetchone()
