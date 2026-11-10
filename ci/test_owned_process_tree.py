"""Native/FFmpeg ownership share one bounded cleanup path, including descendants.

CPU-only synthetic subprocesses; not a Windows Qt/CUDA acceptance result.
"""
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'AI_SMM_Work_Architecture_A75'
if WORK.is_dir():sys.path.insert(0,str(WORK))
from owned_process_cleanup import close_owned_process
import native_process_runtime as native
import media_subprocess_runtime as media


def test_native_owner_reaps_finished_leader_and_descendant(tmp_path):
    marker=tmp_path/'orphan.txt'
    grandchild=('import time,pathlib;time.sleep(1.2);pathlib.Path('
                +repr(str(marker))+').write_text("orphan")')
    script=tmp_path/'launcher.py'
    script.write_text('import subprocess,sys;subprocess.Popen([sys.executable,"-c",'
                      +repr(grandchild)+'],stdin=subprocess.DEVNULL,'
                      'stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)',encoding='utf-8')
    with (tmp_path/'native.log').open('wb') as log:
        with native.owned_native_process(native.native_command(sys.executable,script),
                                         env=os.environ,stdout=log,stderr=log) as child:
            assert child.wait(timeout=5)==0
    time.sleep(1.4)
    assert not marker.exists()


def test_media_and_native_both_delegate_cleanup():
    with patch.object(native,'close_owned_process') as close:
        fake=object()
        with patch.object(native,'_create_job',return_value=None),patch.object(
                native,'_spawn_native',return_value=fake):
            with native.owned_native_process(['fixture'],env={},stdout=None,stderr=None):
                pass
        close.assert_called_once_with(fake,None,grace=5)
    with patch.object(media,'close_owned_process') as close:
        fake=object()
        media._stop(fake,None)
        close.assert_called_once_with(fake,None,grace=3)


def test_cleanup_reaps_owned_child_on_exception(tmp_path):
    pid_file=tmp_path/'pid'
    script=tmp_path/'hang.py'
    script.write_text('import os,time,pathlib;pathlib.Path('
                      +repr(str(pid_file))+').write_text(str(os.getpid()));time.sleep(30)',
                      encoding='utf-8')
    with (tmp_path/'native.log').open('wb') as log:
        with pytest.raises(RuntimeError,match='forced cancellation'):
            with native.owned_native_process(native.native_command(sys.executable,script),
                                             env=os.environ,stdout=log,stderr=log):
                deadline=time.monotonic()+3
                while not pid_file.exists() and time.monotonic()<deadline:
                    time.sleep(.01)
                assert pid_file.exists()
                raise RuntimeError('forced cancellation')
    if os.name!='nt':
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()),0)


def test_invalid_cleanup_grace_never_signals_a_process():
    for value in (0,-1,11,True,'3'):
        with pytest.raises(ValueError):
            close_owned_process(None,grace=value)
