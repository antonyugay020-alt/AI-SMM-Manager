"""Reap only an application-owned child and its POSIX process group.

Caller MUST start POSIX children with start_new_session=True. Windows children
must be placed into a kill-on-close Job Object before they run. This helper
never scans processes or signals unrelated process names.
"""
import os
import signal
import subprocess


def close_owned_process(process, job=None, *, grace=3):
    """Stop the whole owned process tree, including a finished leader's children.

    Close stdin on every path. A Job Object owns the Windows tree; a dedicated
    POSIX session owns the process group. A mock without a PID falls back to
    direct leader termination and does not signal an arbitrary group.
    """
    if type(grace) not in (int, float) or not 0 < grace <= 10:
        raise ValueError('Invalid owned process cleanup grace')
    try:
        if job is not None:
            job.close()
        if process is None:
            return
        if os.name != 'nt' and type(getattr(process, 'pid', None)) is int and process.pid > 0:
            group = process.pid  # Popen(start_new_session=True) guarantees this.
            if process.poll() is None:
                try:
                    os.killpg(group, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                pass
            finally:
                # The process group can outlive its reaped leader. Always
                # kill remaining descendants before returning to the caller.
                try:
                    os.killpg(group, signal.SIGKILL)
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
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            process.wait(timeout=5)
    finally:
        if process is not None and getattr(process, 'stdin', None) is not None:
            process.stdin.close()
