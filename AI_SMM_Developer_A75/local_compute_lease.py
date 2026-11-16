"""One cancellable OS lease for app-owned local text, vision and diffusion.

No model loads, unload requests or process termination. External GPU workloads
remain outside our authority and are checked by the actual hardware preflight.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import errno
import os
from pathlib import Path
import tempfile
import threading
import time
from project_file_boundary import reject_path_link

_HELD=ContextVar('local_compute_lease',default=None)

@contextmanager
def device_lease(path=None,*,wait=True,max_wait_seconds=180,check=lambda:None,on_wait=None):
    if max_wait_seconds<=0:raise ValueError('Positive device-lock deadline required')
    path=reject_path_link(Path(path) if path is not None else Path(tempfile.gettempdir())/'ai-smm-generative-device0.lock')
    owner=(os.getpid(),threading.get_ident(),str(path.absolute()))
    check()
    if _HELD.get()==owner:
        yield 0.0
        return
    started=time.monotonic();contended=False
    with path.open('a+b') as handle:
        if path.stat().st_size==0:handle.write(b'0');handle.flush()
        while True:
            check();handle.seek(0)
            try:
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError as exc:
                busy=(exc.errno in (errno.EACCES,errno.EAGAIN,errno.EDEADLK)
                    or (os.name=='nt' and getattr(exc,'winerror',None) in (32,33)))
                if not wait or not busy:raise
                if not contended:
                    contended=True
                    if on_wait:on_wait()
                if time.monotonic()-started>=max_wait_seconds:
                    raise TimeoutError('LOCAL_DEVICE_LOCK_TIMEOUT') from exc
                time.sleep(max(0,min(.1,max_wait_seconds-(time.monotonic()-started))))
        token=_HELD.set(owner)
        try:
            check()
            yield time.monotonic()-started if contended else 0.0
        finally:
            _HELD.reset(token);handle.seek(0)
            if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
