"""SQLite-only regression for task facts and transaction ownership.

No Qt, Ollama, network, GPU, paid provider or user's database is involved.
"""
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'AI_SMM_Work_Architecture_A75'))
from task_context import load_facts, update_facts


def connection():
    db = sqlite3.connect(':memory:')
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
    db.execute('CREATE TABLE ai_task_facts(project_id TEXT PRIMARY KEY REFERENCES projects(id),facts_json TEXT NOT NULL)')
    db.execute('CREATE TABLE unrelated_event(note TEXT NOT NULL)')
    db.executemany('INSERT INTO projects VALUES(?)', [('p1',), ('p2',)])
    db.commit()
    return db


def test_standalone_task_fact_update_is_durable_and_idempotent():
    with connection() as db:
        result = update_facts(db, 'p1', 'Ниша: Кофейня\nАудитория: студенты\ninstagram')
        assert result == load_facts(db, 'p1')
        assert result['business'] == 'Кофейня'
        assert not db.in_transaction
        update_facts(db, 'p1', 'telegram')
        assert load_facts(db, 'p1')['business'] == 'Кофейня'
        assert load_facts(db, 'p1')['channels'] == 'telegram'
        assert not db.in_transaction


def test_foreign_transaction_is_not_committed_and_can_rollback():
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("INSERT INTO unrelated_event VALUES('must rollback')")
        update_facts(db, 'p1', 'Ниша: Студия')
        assert db.in_transaction
        assert load_facts(db, 'p1')['business'] == 'Студия'
        db.rollback()
        assert load_facts(db, 'p1') == {}
        assert db.execute('SELECT * FROM unrelated_event').fetchall() == []


def test_foreign_transaction_commits_both_changes_together():
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("INSERT INTO unrelated_event VALUES('same transaction')")
        update_facts(db, 'p1', 'Ниша: Магазин')
        db.commit()
        assert load_facts(db, 'p1')['business'] == 'Магазин'
        assert db.execute('SELECT note FROM unrelated_event').fetchone() == ('same transaction',)


def test_invalid_project_rolls_back_only_task_facts_savepoint():
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("INSERT INTO unrelated_event VALUES('outer work remains')")
        with pytest.raises(sqlite3.IntegrityError):
            update_facts(db, 'unknown', 'Ниша: Салон')
        assert db.in_transaction
        assert db.execute('SELECT note FROM unrelated_event').fetchone() == ('outer work remains',)
        db.commit()
        assert load_facts(db, 'p1') == {}


def test_failed_replacement_keeps_old_facts_and_unrelated_committed_rows():
    with connection() as db:
        update_facts(db, 'p1', 'Ниша: Магазин')
        db.execute('''CREATE TRIGGER reject_task_facts BEFORE INSERT ON ai_task_facts
                      WHEN NEW.facts_json LIKE '%Салон%' BEGIN SELECT RAISE(ABORT, 'blocked'); END''')
        db.commit()
        with pytest.raises(sqlite3.IntegrityError, match='blocked'):
            update_facts(db, 'p1', 'Ниша: Салон', reset=True)
        assert not db.in_transaction
        assert load_facts(db, 'p1')['business'] == 'Магазин'


def test_two_projects_isolated_under_one_outer_transaction():
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        update_facts(db, 'p1', 'Ниша: Магазин')
        update_facts(db, 'p2', 'Ниша: Кафе')
        db.rollback()
        assert load_facts(db, 'p1') == load_facts(db, 'p2') == {}


def test_repeated_rollback_and_commit_preserve_facts_and_connection_state():
    with connection() as db:
        for i in range(100):
            db.execute('BEGIN IMMEDIATE')
            update_facts(db, 'p1', f'Ниша: Тест{i}', reset=True)
            if i % 2:
                db.commit()
            else:
                db.rollback()
            assert not db.in_transaction
            expected = f'Тест{i}' if i % 2 else f'Тест{i-1}' if i else None
            assert load_facts(db, 'p1').get('business') == expected
