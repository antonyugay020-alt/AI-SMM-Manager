"""Owned tree termination contracts; Win32 semantics still need physical QA."""
from pathlib import Path
import os
import subprocess
import sys
import time
from unittest.mock import Mock

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
import windows_process_group as windows
from owned_process_cleanup import close_owned_process
from native_process_runtime import native_command,owned_native_process


def job_fixture(active):
    job=object.__new__(windows.WindowsJob)
    job.handle=123
    job.api=Mock()
    job.api.TerminateJobObject.return_value=True
    job.active=Mock(side_effect=active)
    return job


def test_windows_drain_waits_for_descendants_before_closing(monkeypatch):
    job=job_fixture([2,1,0])
    monkeypatch.setattr(windows.time,'sleep',lambda delay: None)
    job.terminate_and_wait(timeout=1)
    job.api.TerminateJobObject.assert_called_once_with(123,1)
    assert job.active.call_count==3
    assert job.handle==123
    job.close()
    assert job.handle is None


def test_windows_stuck_descendant_cannot_report_cleanup_success(monkeypatch):
    job=job_fixture([1,1])
    clock=iter([0,2])
    monkeypatch.setattr(windows.time,'monotonic',lambda:next(clock))
    with pytest.raises(TimeoutError,match='OWNED_WINDOWS_JOB_DRAIN_TIMEOUT'):
        job.terminate_and_wait(timeout=1)
    assert job.handle==123
    job.close()


def test_drain_failure_still_closes_job_reaps_leader_and_pipe():
    events=[]
    job=Mock()
    def fail(**kwargs):
        events.append('drain')
        raise TimeoutError('tree remains')
    job.terminate_and_wait.side_effect=fail
    job.close.side_effect=lambda:events.append('close_job')
    process=Mock()
    process.poll.return_value=0
    process.wait.side_effect=lambda **kwargs:events.append('reap')
    process.stdin.close.side_effect=lambda:events.append('close_stdin')
    with pytest.raises(TimeoutError,match='tree remains'):
        close_owned_process(process,job)
    assert events==['drain','close_job','reap','close_stdin']


@pytest.mark.parametrize('value',[0,-1,11,True,float('nan'),float('inf')])
def test_invalid_drain_timeout_never_signals(value):
    job=job_fixture([0])
    with pytest.raises(ValueError):job.terminate_and_wait(timeout=value)
    job.api.TerminateJobObject.assert_not_called()


def test_actual_descendant_output_handle_released_on_owner_exit(tmp_path):
    # On Windows this exercises venv redirectors plus a nested Python child.
    # On POSIX it exercises process-group ownership, not Win32 emulation.
    ready=tmp_path/'ready'
    script=tmp_path/'owner.py'
    nested='import pathlib,time;pathlib.Path('+repr(str(ready))+').write_text("ready");time.sleep(60)'
    script.write_text('import subprocess,sys,time\nsubprocess.Popen([sys.executable,"-c",'
                      +repr(nested)+'])\ntime.sleep(60)\n',encoding='utf-8')
    output=tmp_path/'owned.log'
    with output.open('wb') as log:
        with owned_native_process(native_command(sys.executable,script),env=os.environ,
                                  stdout=log,stderr=log):
            deadline=time.monotonic()+10
            while not ready.exists() and time.monotonic()<deadline:time.sleep(.02)
            assert ready.exists()
    # A live Windows descendant's inherited output handle denies this unlink.
    output.unlink()
    assert not output.exists()
