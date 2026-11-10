"""Source read/identity must never follow an ancestor swapped after precheck."""
import hashlib
import os
from pathlib import Path
from unittest.mock import patch
import time

import pytest
import stable_file_source as stable


def test_regular_nested_file_and_receipt(tmp_path):
    nested = tmp_path/'inside'/'nested'
    nested.mkdir(parents=True)
    file = nested/'image.bin'
    payload = b'original' * 30000
    file.write_bytes(payload)
    seen=[]
    sha, size = stable.read_stable_bytes(file, write=seen.append)
    assert (sha, size) == (hashlib.sha256(payload).hexdigest(),len(payload))
    assert b''.join(seen)==payload
    with os.fdopen(stable.open_source(file), 'rb') as f:
        assert stable.descriptor_identity(f.fileno()) == stable.source_identity(file)


@pytest.mark.skipif(os.name=='nt', reason='POSIX openat race regression')
def test_parent_symlink_swap_after_precheck_fails_closed(tmp_path):
    inside=tmp_path/'inside';inside.mkdir()
    parent=inside/'nested';parent.mkdir()
    (parent/'image.bin').write_bytes(b'authorized')
    outside=tmp_path/'outside';outside.mkdir()
    (outside/'image.bin').write_bytes(b'UNAUTHORIZED')
    original=stable.reject_path_link
    swapped=[False]
    def precheck_then_swap(path):
        original(path)
        if not swapped[0]:
            swapped[0]=True
            parent.rename(inside/'saved')
            parent.symlink_to(outside,target_is_directory=True)
    with patch.object(stable,'reject_path_link',side_effect=precheck_then_swap):
        with pytest.raises((OSError,ValueError)):
            stable.open_source(parent/'image.bin')
    assert swapped[0]
    assert (outside/'image.bin').read_bytes()==b'UNAUTHORIZED'


@pytest.mark.skipif(os.name=='nt', reason='POSIX openat race regression')
def test_source_identity_uses_no_follow_walk(tmp_path):
    inside=tmp_path/'inside';inside.mkdir()
    parent=inside/'nested';parent.mkdir()
    (parent/'image.bin').write_bytes(b'authorized')
    outside=tmp_path/'outside';outside.mkdir()
    (outside/'image.bin').write_bytes(b'UNAUTHORIZED')
    original=stable.reject_path_link
    swapped=[False]
    def precheck_then_swap(path):
        original(path)
        if not swapped[0]:
            swapped[0]=True
            parent.rename(inside/'saved')
            parent.symlink_to(outside,target_is_directory=True)
    with patch.object(stable,'reject_path_link',side_effect=precheck_then_swap):
        with pytest.raises((OSError,ValueError)):
            stable.source_identity(parent/'image.bin')
    assert swapped[0]


@pytest.mark.skipif(os.name=='nt' or not hasattr(os,'mkfifo'),reason='POSIX FIFO')
def test_fifo_cannot_block_source_open(tmp_path):
    fifo=tmp_path/'fake.png'
    os.mkfifo(fifo)
    started=time.monotonic()
    with pytest.raises(ValueError,match='regular file'):
        stable.open_source(fifo)
    assert time.monotonic()-started < 1.0


@pytest.mark.skipif(os.name=='nt',reason='POSIX openat race regression')
def test_leaf_symlink_and_directory_are_rejected(tmp_path):
    source=tmp_path/'source.bin';source.write_bytes(b'hello')
    link=tmp_path/'link.bin';link.symlink_to(source)
    with pytest.raises((OSError,ValueError)):
        stable.open_source(link)
    with pytest.raises(ValueError,match='regular file'):
        stable.open_source(tmp_path)


def test_receipt_mismatch_and_cancellation(tmp_path):
    file=tmp_path/'x.bin';file.write_bytes(b'hello')
    with pytest.raises(ValueError,match='durable receipt'):
        stable.read_stable_bytes(file,expected=('0'*64,5))
    with pytest.raises(InterruptedError):
        stable.read_stable_bytes(file,check=lambda: (_ for _ in ()).throw(InterruptedError()))

@pytest.mark.skipif(os.name=='nt', reason='POSIX spool lease race regression')
def test_lock_file_symlink_swap_after_precheck_does_not_write_outside(tmp_path):
    from media_spool_lease import spool_lease
    root=tmp_path/'project';root.mkdir()
    external=tmp_path/'outside.bin';external.write_bytes(b'')
    original=stable.reject_path_link
    swapped=[False]
    def precheck_then_swap(path):
        original(path)
        if not swapped[0] and Path(path).name=='.lease':
            swapped[0]=True
            (root/'.ingest_spool'/'.lease').symlink_to(external)
    with patch.object(stable,'reject_path_link',side_effect=precheck_then_swap):
        with pytest.raises((OSError,ValueError)):
            with spool_lease(root):
                pytest.fail('Unsafe lock acquired')
    assert swapped[0]
    assert external.read_bytes()==b''


@pytest.mark.skipif(os.name=='nt', reason='POSIX spool lease race regression')
def test_lock_file_hardlink_rejected(tmp_path):
    from media_spool_lease import spool_lease
    root=tmp_path/'project';root.mkdir()
    folder=root/'.ingest_spool';folder.mkdir()
    external=tmp_path/'outside.bin';external.write_bytes(b'')
    os.link(external,folder/'.lease')
    with pytest.raises(ValueError,match='hard links'):
        with spool_lease(root):pass
    assert external.read_bytes()==b''


def test_spool_lease_regular_acquisition_and_cleanup(tmp_path):
    from media_spool_lease import spool_lease
    with spool_lease(tmp_path):
        lock=tmp_path/'.ingest_spool'/'.lease'
        inode=lock.stat().st_ino
    assert lock.read_bytes()==b'1'
    with spool_lease(tmp_path,timeout=1):
        assert lock.stat().st_ino==inode

@pytest.mark.skipif(os.name=='nt', reason='POSIX spool lease cross-process regression')
def test_spool_lease_serializes_distinct_processes(tmp_path):
    import subprocess
    import sys
    from media_spool_lease import spool_lease
    script = ('import sys,time\n'
              'sys.path.insert(0,sys.argv[1])\n'
              'from media_spool_lease import spool_lease\n'
              'with spool_lease(sys.argv[2]):\n'
              ' print("locked",flush=True)\n'
              ' time.sleep(10)\n')
    child=subprocess.Popen([sys.executable,'-c',script,str(Path(stable.__file__).parent),str(tmp_path)],
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='locked'
        with pytest.raises(TimeoutError,match='MEDIA_SPOOL_LEASE_TIMEOUT'):
            with spool_lease(tmp_path,timeout=.1):
                pytest.fail('Two owners admitted')
    finally:
        child.terminate()
        child.wait(timeout=5)
        child.stdout.close();child.stderr.close()
    with spool_lease(tmp_path,timeout=1):pass


@pytest.mark.skipif(os.name=='nt', reason='POSIX spool lease cross-process regression')
def test_spool_lock_inode_changed_after_open_fails_closed(tmp_path):
    from media_spool_lease import spool_lease
    import media_spool_lease as lease
    root=tmp_path/'project';root.mkdir()
    original=lease.open_managed_lock
    def open_then_replace(path):
        handle=original(path)
        lock=Path(path)
        lock.rename(lock.with_name('retired'))
        lock.write_bytes(b'1')
        return handle
    with patch.object(lease,'open_managed_lock',side_effect=open_then_replace):
        with pytest.raises(ValueError,match='inode changed'):
            with spool_lease(root):
                pytest.fail('Inode mismatch admitted')
