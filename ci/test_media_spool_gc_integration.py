"""Real SQLite ingest/GC race: an old content-addressed blob cannot be GC'd
between staging and its committed durable intent. No Qt, models or network.
"""
from contextlib import contextmanager
import os
import sys
from pathlib import Path
import sqlite3
import threading
import time
from unittest.mock import patch

import pytest

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'AI_SMM_Work_Architecture_A75'
if not WORK.is_dir():WORK=Path(__file__).resolve().parent
sys.path.insert(0,str(WORK))
import media_ingest_core as media

OWNER = 'a'*32


@contextmanager
def _database(path):
    db = sqlite3.connect(path, timeout=10, check_same_thread=False)
    db.execute('PRAGMA busy_timeout=10000')
    try:
        yield db
    finally:
        if db.in_transaction: db.rollback()
        db.close()


def test_import_intent_and_gc_share_cross_process_spool_lifetime(tmp_path):
    root=tmp_path/OWNER
    root.mkdir()
    source=tmp_path/'photo.png'
    source.write_bytes(b'known immutable imported bytes')
    database=tmp_path/'media.sqlite'
    with _database(database) as db:
        db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        db.execute('INSERT INTO projects VALUES(?)',(OWNER,))
        db.execute('''CREATE TABLE assets(id TEXT PRIMARY KEY,project_id TEXT,
            original_name TEXT,stored_path TEXT,sha256 TEXT,imported_at TEXT)''')
        db.execute('''CREATE TABLE project_files(id TEXT PRIMARY KEY,project_id TEXT,
            kind TEXT,display_name TEXT,path TEXT,created_at TEXT)''')
        db.commit()
    staged=threading.Event();release=threading.Event();gc_entered=threading.Event()
    results={};failures=[]
    stage_original=media.stage_source

    def workspace(owner):
        assert owner==OWNER
        return root

    def stage_then_pause(root_path,source_path,*,check=None):
        result=stage_original(root_path,source_path,check=check)
        blob=root_path/'.ingest_spool'/result[0]
        old=time.time()-90000
        os.utime(blob,(old,old))
        staged.set()
        if not release.wait(timeout=10):raise TimeoutError('test staging pause expired')
        return result

    def importing():
        try:
            with _database(database) as db:
                results['import']=media.ingest(db,workspace,source,project_id=OWNER,kind='asset')
        except BaseException as exc:failures.append(exc)

    def collecting():
        try:
            with _database(database) as db:
                gc_entered.set()
                results['gc']=media.prune_unreferenced_spool(
                    db,workspace,project_id=OWNER,min_age_seconds=86400)
        except BaseException as exc:failures.append(exc)

    with patch.object(media,'stage_source',side_effect=stage_then_pause):
        importer=threading.Thread(target=importing,daemon=True)
        collector=threading.Thread(target=collecting,daemon=True)
        importer.start()
        try:
            assert staged.wait(10)
            collector.start()
            assert gc_entered.wait(10)
            assert not release.is_set()
            collector.join(timeout=.2)
            assert collector.is_alive(), "GC ran while the ingest intent was uncommitted"
        finally:
            release.set()
            importer.join(timeout=15)
            if collector.ident is not None:collector.join(timeout=15)
    assert not importer.is_alive() and not collector.is_alive()
    assert not failures, failures
    assert results['import'].published
    assert results['gc']['deleted']==0
    with _database(database) as db:
        (sha,)=db.execute('SELECT source_sha256 FROM media_ingest_jobs').fetchone()
    assert (root/'.ingest_spool'/sha).is_file()


def test_failed_import_releases_spool_lease_for_recovery(tmp_path):
    root=tmp_path/OWNER;root.mkdir()
    source=tmp_path/'bad.png';source.write_bytes(b'fixture')
    database=tmp_path/'media.sqlite'
    with _database(database) as db:
        db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        db.execute('INSERT INTO projects VALUES(?)',(OWNER,))
        db.execute('''CREATE TABLE assets(id TEXT PRIMARY KEY,project_id TEXT,
            original_name TEXT,stored_path TEXT,sha256 TEXT,imported_at TEXT)''')
        db.execute('''CREATE TABLE project_files(id TEXT PRIMARY KEY,project_id TEXT,
            kind TEXT,display_name TEXT,path TEXT,created_at TEXT)''')
        db.commit()
        with pytest.raises(ValueError,match='display name'):
            media.ingest(db,lambda owner:root,source,project_id=OWNER,
                         kind='asset',original_name='\x00')
        # The lock was released even though staging already wrote a spool blob.
        assert isinstance(media.prune_unreferenced_spool(
            db,lambda owner:root,project_id=OWNER,min_age_seconds=86400),dict)
