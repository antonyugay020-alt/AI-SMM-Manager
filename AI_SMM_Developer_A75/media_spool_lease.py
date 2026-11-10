"""Cross-process lifetime fence for immutable media spool and its SQLite intents.

The import lease covers staging through committing the media_ingest_jobs intent.
GC takes the same lease before checking references and deleting old blobs.
The lock inode is permanent; unlinking it would allow simultaneous owners.
"""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import time

from project_file_boundary import require_managed_folder, require_managed_file


@contextmanager
def spool_lease(root, *, timeout=30):
    if type(timeout) not in (int, float) or not 0 < timeout <= 120:
        raise ValueError('Invalid media spool lease timeout')
    root = Path(root)
    folder = require_managed_folder(root, '.ingest_spool')
    folder.mkdir(parents=True, exist_ok=True)
    path = require_managed_file(root, folder / '.lease', ('.ingest_spool',))
    deadline = time.monotonic() + timeout
    with path.open('a+b') as handle:
        # msvcrt.locking requires an existing byte; never remove this file.
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b'1')
            handle.flush()
            os.fsync(handle.fileno())
        acquired = False
        try:
            while True:
                handle.seek(0)
                try:
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    if time.monotonic() >= deadline:
                        raise TimeoutError('MEDIA_SPOOL_LEASE_TIMEOUT') from exc
                    time.sleep(min(.05, max(0, deadline - time.monotonic())))
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
