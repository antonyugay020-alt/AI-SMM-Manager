"""Real storage regressions; no model judgment or Windows E2E is simulated."""
from contextlib import closing
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest
from application_bootstrap import open_application_database
from application_core import ApplicationRuntime
from diagnostic_monitor import discover_projects
from sqlite_transaction_core import own


def test_deferred_commit_failure_rolls_back_and_allows_next_operation():
    with closing(sqlite3.connect(':memory:')) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('CREATE TABLE parent(id INTEGER PRIMARY KEY)')
        db.execute('CREATE TABLE child(pid INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)')
        with pytest.raises(sqlite3.IntegrityError):
            with own(db):
                db.execute('INSERT INTO child VALUES(42)')
        assert not db.in_transaction
        assert db.execute('SELECT * FROM child').fetchall() == []
        with own(db):
            db.execute('INSERT INTO parent VALUES(42)')
            db.execute('INSERT INTO child VALUES(42)')
        assert db.execute('SELECT * FROM child').fetchall() == [(42,)]


def test_runtime_close_preserves_foreign_transaction_then_releases_database(tmp_path):
    path=tmp_path/'проект.db'
    db=open_application_database(path,tmp_path)
    runtime=ApplicationRuntime(db,lambda pid:tmp_path/pid)
    try:
        db.execute("INSERT INTO projects(id,name,created_at) VALUES('p','Проект','now')")
        with pytest.raises(RuntimeError):runtime.close()
        assert db.in_transaction
        db.commit()
        runtime.close()
        runtime.close()
        with pytest.raises(sqlite3.ProgrammingError):db.execute('SELECT 1')
        # Windows rejects this replacement if a connection still owns the file.
        moved=path.with_suffix('.old')
        path.rename(moved)
        with closing(open_application_database(moved,tmp_path)) as reopened:
            assert reopened.execute('SELECT name FROM projects').fetchone() == ('Проект',)
    finally:
        db.close()


def test_diagnostic_reader_closes_on_success_and_query_failure(tmp_path):
    path=tmp_path/'studio.sqlite3'
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE project_locations(folder TEXT)')
        db.commit()
    real_connect=sqlite3.connect
    opened=[]
    def connect(*args,**kwargs):
        db=real_connect(*args,**kwargs)
        opened.append(db)
        return db
    with patch('diagnostic_monitor.sqlite3.connect',side_effect=connect):
        discover_projects(tmp_path)
        with closing(real_connect(path)) as db:
            db.execute('DROP TABLE project_locations')
            db.execute('CREATE TABLE project_locations(wrong_column TEXT)')
        _,warnings,_=discover_projects(tmp_path)
    assert any('Cannot read project locations' in w for w in warnings)
    assert len(opened)==2
    for db in opened:
        with pytest.raises(sqlite3.ProgrammingError):db.execute('SELECT 1')
    path.unlink()


def test_unicode_checkpoints_roundtrip_with_non_utf8_default(tmp_path):
    import io
    import json
    from durable_json import atomic_write_json
    from task_orchestration import TaskLedger
    original_open=io.open
    def locale_open(file,mode='r',buffering=-1,encoding=None,*args,**kwargs):
        if 'b' not in mode and encoding is None:encoding='cp1252'
        return original_open(file,mode,buffering,encoding,*args,**kwargs)
    with patch('io.open',side_effect=locale_open):
        path=tmp_path/'черновик.json'
        payload={'posts':{'1/Вт':{'post':'Проверенный текст'}}}
        atomic_write_json(path,payload)
        assert json.loads(path.read_text(encoding='utf-8')) == payload
        ledger=TaskLedger.open(tmp_path,'unicode-boundary')
        ledger.register('текст',['публикация'])
        recovered=TaskLedger.open(tmp_path,'unicode-boundary')
        assert recovered.outputs == ledger.outputs
        assert not recovered.completed
