"""Real managed media reads: no symlink, inode-swap, mutation or partial copy.

Offline CPU/SQLite-boundary tests; no model, network, Qt or paid service.
"""
import hashlib
import os
from pathlib import Path
import sys
from unittest.mock import patch
import pytest

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'AI_SMM_Work_Architecture_A75'
sys.path.insert(0,str(WORK))
from stable_file_source import read_stable_bytes
import media_ingest_core as ingest


def test_stable_digest_and_publication_stream(tmp_path):
    data=b'abc'*400000
    source=tmp_path/'source.png';source.write_bytes(data)
    target=tmp_path/'copy.png'
    with target.open('xb') as writer:
        assert read_stable_bytes(source,write=writer.write)==(hashlib.sha256(data).hexdigest(),len(data))
    assert target.read_bytes()==data
    assert ingest._digest(target)==(hashlib.sha256(data).hexdigest(),len(data))


@pytest.mark.skipif(os.name=='nt',reason='Windows symlink creation needs host privileges')
def test_symlink_and_linked_parent_rejected(tmp_path):
    external=tmp_path/'private';external.write_bytes(b'secret')
    link=tmp_path/'linked.png';link.symlink_to(external)
    with pytest.raises((ValueError,OSError)):
        read_stable_bytes(link)
    folder=tmp_path/'linked_folder';folder.symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises((ValueError,OSError)):
        read_stable_bytes(folder/'private')


@pytest.mark.skipif(os.name=='nt',reason='Windows stable handles deny concurrent mutation')
def test_inplace_mutation_and_inode_swap_fail(tmp_path):
    source=tmp_path/'source.png';source.write_bytes(b'a'*(3*1024*1024))
    def mutate(block):
        if not mutate.done:
            mutate.done=True
            with source.open('r+b') as handle:
                handle.write(b'z');handle.flush();os.fsync(handle.fileno())
    mutate.done=False
    with pytest.raises(ValueError,match='changed during reading'):
        read_stable_bytes(source,write=mutate)
    replacement=tmp_path/'replacement.png';replacement.write_bytes(source.read_bytes())
    def replace(block):
        if replacement.exists():
            source.replace(tmp_path/'old.png');replacement.replace(source)
    with pytest.raises(ValueError,match='changed during reading'):
        read_stable_bytes(source,write=replace)


def test_cancel_and_expected_digest_fail_closed(tmp_path):
    source=tmp_path/'source.png';source.write_bytes(b'a'*(3*1024*1024))
    checks=[0]
    def cancel():
        checks[0]+=1
        if checks[0]==5:raise InterruptedError('stop')
    with pytest.raises(InterruptedError,match='stop'):
        read_stable_bytes(source,check=cancel)
    with pytest.raises(ValueError,match='durable receipt'):
        read_stable_bytes(source,expected=('f'*64,3*1024*1024))


@pytest.mark.skipif(os.name=='nt',reason='Windows symlink creation needs host privileges')
def test_spool_reuse_cannot_follow_link_planted_after_path_check(tmp_path):
    root=tmp_path/'project';root.mkdir()
    source=tmp_path/'input.png';source.write_bytes(b'legitimate')
    external=tmp_path/'external';external.write_bytes(b'private')
    original=ingest._spool
    def inject(root_path,sha):
        folder,target=original(root_path,sha)
        target.symlink_to(external)
        return folder,target
    with patch.object(ingest,'_spool',side_effect=inject):
        with pytest.raises((ValueError,OSError)):
            ingest.stage_source(root,source)
    assert external.read_bytes()==b'private'
    assert not list((root/'.ingest_spool').glob('.import-*'))


@pytest.mark.skipif(os.name=='nt',reason='Windows symlink creation needs host privileges')
def test_publication_refuses_linked_spool_source(tmp_path):
    root=tmp_path/'project';root.mkdir()
    spool=root/'.ingest_spool';spool.mkdir()
    processed=root/'processed';processed.mkdir()
    external=tmp_path/'external';external.write_bytes(b'private')
    linked=spool/('a'*64);linked.symlink_to(external)
    destination=processed/('b'*32+'.png')
    with pytest.raises((ValueError,OSError)):
        ingest._publish_independent_copy(root,linked,destination,'a'*64,7,'result')
    assert not destination.exists()
    assert not list(processed.glob('.publish-*'))
