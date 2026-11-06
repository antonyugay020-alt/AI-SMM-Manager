"""Stream media before SQLite transactions; fence path/byte identity inside."""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import os
import stat

def _identity(path):
    s=os.stat(path,follow_symlinks=False)
    if not stat.S_ISREG(s.st_mode):raise ValueError('Not a regular media file')
    return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_mode,s.st_nlink)

@dataclass(frozen=True)
class FileEvidence:
    path:Path
    digest:str
    size:int
    identity:tuple

def capture(path,*,allow_empty=False):
    target=Path(path);before=_identity(target)
    if not before[2] and not allow_empty:raise ValueError('Empty media cannot be published')
    fd=os.open(target,os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0))
    try:
        st=os.fstat(fd)
        opened=(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns,st.st_mode,st.st_nlink)
        if opened!=before:raise ValueError('Media changed during preflight')
        h=sha256();size=0
        with os.fdopen(fd,'rb') as stream:
            fd=-1
            for chunk in iter(lambda:stream.read(1024*1024),b''):
                h.update(chunk);size+=len(chunk)
            st=os.fstat(stream.fileno())
            after=(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns,st.st_mode,st.st_nlink)
        if after!=before or size!=before[2] or _identity(target)!=before:
            raise ValueError('Media changed during preflight')
        return FileEvidence(target,h.hexdigest(),size,before)
    finally:
        if fd>=0:os.close(fd)

def unchanged(evidence):
    try:return _identity(evidence.path)==evidence.identity
    except (OSError,ValueError):return False
