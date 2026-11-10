"""Cooperative local media source digest, snapshot and durable staging tests.

Offline only: no cloud, CUDA, Qt, paid provider, or real Windows acceptance.
"""
import hashlib
from pathlib import Path
import sqlite3
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
from media_artifact_validation import digest_file
from media_upload_transfer import copy_snapshot_with_check
from media_ingest_core import stage_source,ingest


def source(tmp_path):
    path=tmp_path/'input.png'
    path.write_bytes(b'ABCD'*(800000))
    return path


def stop_after(count):
    seen=[0]
    def check():
        seen[0]+=1
        if seen[0]>=count:
            raise InterruptedError('user stopped')
    return check


def test_hash_cancels_between_bounded_reads(tmp_path):
    path=source(tmp_path)
    expected=hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest_file(path)==expected
    assert digest_file(path,check=lambda:None)==expected
    with pytest.raises(InterruptedError,match='user stopped'):
        digest_file(path,check=stop_after(5))
    with pytest.raises(TypeError,match='callable'):
        digest_file(path,check=42)


def test_local_snapshot_cancels_mid_copy_without_complete_file(tmp_path):
    path=source(tmp_path)
    target=tmp_path/'snapshot.png'
    with pytest.raises(InterruptedError,match='user stopped'):
        copy_snapshot_with_check(path,target,check=stop_after(6))
    assert 0<=target.stat().st_size<path.stat().st_size


def test_staging_cancellation_removes_temporary_bytes_before_sql(tmp_path):
    path=source(tmp_path)
    root=tmp_path/'project';root.mkdir()
    with pytest.raises(InterruptedError,match='user stopped'):
        stage_source(root,path,check=stop_after(5))
    spool=root/'.ingest_spool'
    assert spool.is_dir()
    assert list(spool.iterdir())==[]


def test_staging_legacy_and_checked_produce_identical_receipts(tmp_path):
    path=source(tmp_path)
    root=tmp_path/'project';root.mkdir()
    expected=hashlib.sha256(path.read_bytes()).hexdigest()
    assert stage_source(root,path)==(expected,path.stat().st_size)
    assert stage_source(root,path,check=lambda:None)==(expected,path.stat().st_size)
    assert (root/'.ingest_spool'/expected).is_file()


def test_ingest_passes_authority_to_staging_before_sql_commit(tmp_path,monkeypatch):
    import media_ingest_core as core
    root=tmp_path/'project';root.mkdir()
    path=source(tmp_path)
    owner='a'*32
    db=sqlite3.connect(':memory:')
    try:
        db.execute('CREATE TABLE projects (id TEXT PRIMARY KEY)')
        db.execute('INSERT INTO projects VALUES (?)',(owner,))
        db.commit()
        called=[False]
        def stop_in_stage(root,src,*,check=None):
            called[0]=True
            assert callable(check)
            check()
            raise InterruptedError('user stopped')
        monkeypatch.setattr(core,'stage_source',stop_in_stage)
        with pytest.raises(InterruptedError,match='user stopped'):
            ingest(db,lambda _:root,path,project_id=owner,kind='asset',
                   authorize=lambda:None)
        assert called==[True]
        assert not db.in_transaction
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assets'").fetchone() is None
    finally:
        db.close()
