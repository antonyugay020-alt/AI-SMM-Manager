"""Crash-safe JSON replacement for independent project-scoped checkpoints.

No application/model coupling. A unique temporary file avoids clobbering other
writers' temporary data; each successful replacement exposes complete JSON.
"""
import json
import os
from pathlib import Path
import tempfile
import time


def atomic_write_json(path, payload, *, retries=5, delay=0.2):
    if type(retries) is not int or retries < 1 or delay < 0:
        raise ValueError('Invalid atomic-write retry budget')
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=target.parent,
                prefix='.'+target.name+'.', suffix='.tmp', delete=False) as out:
            temp_name = out.name
            json.dump(payload, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        for attempt in range(retries):
            try:
                os.replace(temp_name, target)
                break
            except PermissionError:
                if attempt + 1 == retries:
                    raise
                time.sleep(delay * (attempt + 1))
        temp_name = None
        # On POSIX, fsync the containing directory after rename. File fsync
        # alone does not ensure the newly renamed directory entry survives a
        # sudden power loss. Windows has no portable directory fsync facility.
        if os.name == 'posix':
            flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)
            directory_fd = os.open(target.parent, flags)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
