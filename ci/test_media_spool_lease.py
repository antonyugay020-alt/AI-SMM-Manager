from pathlib import Path
import subprocess
import sys
import tempfile
import time

import pytest

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'AI_SMM_Work_Architecture_A75'
if not WORK.is_dir():WORK=Path(__file__).resolve().parent
sys.path.insert(0,str(WORK))
from media_spool_lease import spool_lease


def test_lease_rejects_invalid_timeouts(tmp_path):
    for timeout in (0, -1, 121, '30', True):
        with pytest.raises(ValueError):
            with spool_lease(tmp_path, timeout=timeout):
                pass


def test_lease_reuses_stable_lock_inode_and_releases(tmp_path):
    with spool_lease(tmp_path):
        lock=tmp_path/'.ingest_spool'/'.lease'
        first=lock.stat().st_ino
        assert lock.read_bytes() == b'1'
    with spool_lease(tmp_path):
        assert lock.stat().st_ino == first


def test_live_owner_blocks_gc_or_import_then_recovers(tmp_path):
    script = ('import sys,time\n'
              'sys.path.insert(0,sys.argv[1])\n'
              'from media_spool_lease import spool_lease\n'
              'with spool_lease(sys.argv[2]):\n'
              ' print("acquired",flush=True)\n'
              ' time.sleep(20)\n')
    child=subprocess.Popen([sys.executable,'-c',script,str(WORK),str(tmp_path)],
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip() == 'acquired'
        start=time.monotonic()
        with pytest.raises(TimeoutError, match='MEDIA_SPOOL_LEASE_TIMEOUT'):
            with spool_lease(tmp_path, timeout=.15):
                pytest.fail('Overlapping owner admitted')
        assert time.monotonic()-start < 1
        child.terminate(); child.wait(timeout=5)
        with spool_lease(tmp_path,timeout=1):
            assert (tmp_path/'.ingest_spool'/'.lease').exists()
    finally:
        if child.poll() is None:
            child.kill();child.wait(timeout=5)
        child.stdout.close();child.stderr.close()


def test_lease_releases_on_failure(tmp_path):
    with pytest.raises(RuntimeError):
        with spool_lease(tmp_path):
            raise RuntimeError('simulated SQL rollback')
    with spool_lease(tmp_path,timeout=.2):
        pass
