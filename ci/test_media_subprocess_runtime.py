"""Real local subprocess ownership and bounded FFmpeg/ffprobe regression.

No cloud, GPU, model download or paid service. The Windows Job Object path
still requires physical Windows Qt acceptance; these are CPU/OS contract tests.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
from media_subprocess_runtime import run_owned_media_process
from local_media_jobs import _tool,_probe,LocalToolError


def run(code, *, check=None, timeout=5, capture=False):
    return run_owned_media_process([sys.executable,'-c',code], check,
                                   timeout=timeout, capture_stdout=capture)


def test_success_capture_and_stderr_tail():
    assert run('import sys;sys.stdout.write("ok");sys.stderr.write("warning")',
               capture=True)==(0,b'ok',b'warning')


def test_nonzero_exit_returns_bounded_stderr_tail():
    rc,out,err=run('import sys;sys.stderr.write("E"*20000);sys.exit(7)')
    assert rc==7 and out==b'' and len(err)==8192 and err==b'E'*8192


def test_stdout_is_bounded_without_pipe_deadlock():
    with pytest.raises(ValueError,match='safe bound'):
        run('import sys;sys.stdout.write("X"*2000000)',capture=True)


def test_very_large_stderr_does_not_accumulate_in_memory():
    rc,out,err=run('import sys;sys.stderr.write("A"*12000000)',timeout=10)
    assert rc==0 and len(err)==8192


def test_cancelled_before_spawn():
    with patch('media_subprocess_runtime.subprocess.Popen') as spawn:
        with pytest.raises(RuntimeError,match='cancelled'):
            run('pass',check=lambda: (_ for _ in ()).throw(RuntimeError('cancelled')))
        spawn.assert_not_called()


def test_cancelled_child_is_reaped(tmp_path):
    pid_file=tmp_path/'pid'
    code='import os,time,pathlib;pathlib.Path('+repr(str(pid_file))+').write_text(str(os.getpid()));time.sleep(30)'
    def check():
        if pid_file.exists():raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):
        run(code,check=check,timeout=5)
    if os.name!='nt':
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()),0)


def test_deadline_kills_only_owned_child(tmp_path):
    pid_file=tmp_path/'pid'
    code='import os,time,pathlib;pathlib.Path('+repr(str(pid_file))+').write_text(str(os.getpid()));time.sleep(30)'
    with pytest.raises(TimeoutError,match='deadline'):
        run(code,timeout=.5)
    if pid_file.exists() and os.name!='nt':
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()),0)


@pytest.mark.parametrize('timeout',[0,-1,float('inf'),float('nan'),True])
def test_invalid_deadline_never_spawns(timeout):
    with patch('media_subprocess_runtime.subprocess.Popen') as spawn:
        with pytest.raises(ValueError):run('pass',timeout=timeout)
        spawn.assert_not_called()


def test_invalid_cancel_type_never_spawns():
    with patch('media_subprocess_runtime.subprocess.Popen') as spawn:
        with pytest.raises(TypeError):run('pass',check='no')
        spawn.assert_not_called()


def test_finished_leader_cannot_leave_owned_grandchild(tmp_path):
    if os.name=='nt':pytest.skip('POSIX group check; Windows uses Job Object')
    marker=tmp_path/'grandchild.txt'
    code=('import subprocess,sys;'
          'subprocess.Popen([sys.executable,"-c",'
          +repr('import time,pathlib;time.sleep(1);pathlib.Path('+repr(str(marker))+').write_text("orphan")')
          +'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)')
    rc,_,_=run(code)
    assert rc==0
    time.sleep(1.3)
    assert not marker.exists()


def test_real_ffmpeg_ffprobe_owned_process_roundtrip(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('ffmpeg/ffprobe unavailable')
    path=tmp_path/'out.mp4'
    _tool(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i',
           'color=c=red:s=160x120:r=10','-t','1','-pix_fmt','yuv420p',
           str(path)],lambda:None,timeout=20)
    duration,audio,dimensions=_probe(path,full=True)
    assert .8<duration<1.2 and audio is False and dimensions==(160,120)


def test_tool_failure_retains_only_bounded_diagnostic():
    with pytest.raises(LocalToolError) as exc:
        _tool([sys.executable,'-c',
               'import sys;sys.stderr.write("X"*50000);sys.exit(3)'],
              lambda:None,timeout=5)
    assert exc.value.diagnostic['exit_code']==3
    assert len(exc.value.diagnostic['stderr'])==8192


def test_original_tool_cancellation_contract_still_reaps_child():
    processes=[];native=subprocess.Popen
    def spawn(*args,**kwargs):
        process=native(*args,**kwargs);processes.append(process);return process
    started=time.monotonic()
    def stop():
        if time.monotonic()-started>.1:raise RuntimeError('fixture cancellation')
    with patch.object(subprocess,'Popen',side_effect=spawn):
        with pytest.raises(RuntimeError,match='fixture cancellation'):
            _tool([sys.executable,'-c','import time;time.sleep(30)'],stop,timeout=5)
    assert len(processes)==1 and processes[0].poll() is not None
    assert time.monotonic()-started<3


def test_probe_cancellation_before_spawn():
    with patch.object(subprocess,'Popen') as spawn:
        with pytest.raises(RuntimeError,match='cancelled'):
            _probe('missing.mp4',check=lambda: (_ for _ in ()).throw(RuntimeError('cancelled')))
        spawn.assert_not_called()
