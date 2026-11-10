"""Owned FFmpeg/ffprobe subprocess boundary, independent of Qt and CUDA.

Capture to disk, not PIPE: long encodes must never accumulate stderr in RAM.
Windows children are assigned to an application-owned kill-on-close Job Object
before they run. POSIX children use their own process group for bounded cleanup.
"""
from __future__ import annotations

import math
import os
import signal
import subprocess
import tempfile
import time


MAX_PROBE_STDOUT = 1_048_576
MAX_ERROR_TAIL = 8192


def _stop(process, job):
    """Reap the child and its descendants, even if the leader already exited."""
    if job is not None:
        job.close()
    if process is None:
        return
    if os.name != 'nt':
        # The group can outlive its leader. A finished ffprobe/ffmpeg process
        # must not leave a helper running beyond the user's cancellation.
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        return
    if process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        process.wait(timeout=5)


def run_owned_media_process(command, check=None, *, timeout=1200, capture_stdout=False):
    """Return (exit_code, bounded_stdout, bounded_stderr_tail).

    Raises on cancellation, deadline, spawn/ownership failure, or oversized
    probe output. Never reads unbounded output into the parent process.
    """
    if (not isinstance(command, (tuple, list)) or not command
            or any(not isinstance(item, (str, os.PathLike)) for item in command)):
        raise ValueError('Invalid media process command')
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Invalid media process deadline')
    if check is not None and not callable(check):
        raise TypeError('Media cancellation check must be callable')
    if type(capture_stdout) is not bool:
        raise TypeError('Invalid media stdout capture mode')
    process = job = None
    with tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as output:
        try:
            if check is not None:
                check()
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            if os.name == 'nt':
                from windows_process_group import WindowsJob
                job = WindowsJob()
                flags |= 0x00000004 | 0x00000200  # suspended; new process group
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                stdout=output if capture_stdout else subprocess.DEVNULL,
                stderr=errors, creationflags=flags,
                start_new_session=os.name != 'nt')
            if job is not None:
                job.assign_resume(process)
            deadline = time.monotonic() + timeout
            while True:
                if check is not None:
                    check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Local media tool deadline exceeded')
                try:
                    code = process.wait(timeout=min(.1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
            errors.seek(max(0, errors.seek(0, 2) - MAX_ERROR_TAIL))
            tail = errors.read(MAX_ERROR_TAIL)
            if capture_stdout:
                size = output.seek(0, 2)
                if size > MAX_PROBE_STDOUT:
                    raise ValueError('Media probe output exceeds safe bound')
                output.seek(0)
                stdout = output.read(MAX_PROBE_STDOUT)
            else:
                stdout = b''
            return code, stdout, tail
        finally:
            _stop(process, job)
