"""Stable source handles. Windows path stat and descriptor fstat are NOT comparable.

CPython 3.12 path stat preserves birthtime as ctime; fstat exposes ChangeTime.
Use the same Win32 handle metadata for both observations, deny concurrent
writers/deletion, open the reparse point itself and verify the handle's final
path. POSIX uses no-follow descriptors and device/inode/nanosecond metadata.
"""
import os
import stat
from pathlib import Path
from project_file_boundary import reject_path_link


def _windows():
    return os.name == 'nt'


def _api():
    import ctypes
    from ctypes import wintypes as w
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateFileW.argtypes = [w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID,w.DWORD,w.DWORD,w.HANDLE]
    k.CreateFileW.restype = w.HANDLE
    k.GetFileType.argtypes = [w.HANDLE]
    k.GetFileType.restype = w.DWORD
    k.CloseHandle.argtypes = [w.HANDLE]
    k.CloseHandle.restype = w.BOOL
    k.GetFileInformationByHandle.argtypes = [w.HANDLE,w.LPVOID]
    k.GetFileInformationByHandle.restype = w.BOOL
    k.GetFileInformationByHandleEx.argtypes = [w.HANDLE,ctypes.c_int,w.LPVOID,w.DWORD]
    k.GetFileInformationByHandleEx.restype = w.BOOL
    k.GetFinalPathNameByHandleW.argtypes = [w.HANDLE,w.LPWSTR,w.DWORD,w.DWORD]
    k.GetFinalPathNameByHandleW.restype = w.DWORD
    return ctypes,w,k


def _native_identity(handle, path=None):
    c,w,k = _api()
    class Info(c.Structure):
        _fields_ = [('attributes',w.DWORD),('creation',w.FILETIME),
            ('access',w.FILETIME),('write',w.FILETIME),('volume',w.DWORD),
            ('size_high',w.DWORD),('size_low',w.DWORD),('links',w.DWORD),
            ('index_high',w.DWORD),('index_low',w.DWORD)]
    class Basic(c.Structure):
        _fields_ = [('creation',c.c_longlong),('access',c.c_longlong),
            ('write',c.c_longlong),('change',c.c_longlong),('attributes',w.DWORD)]
    class FileId(c.Structure):
        _fields_ = [('volume',c.c_uint64),('ident',c.c_ubyte*16)]
    info=Info();basic=Basic();file_id=FileId()
    if k.GetFileType(handle)!=1:raise ValueError('Source is not a disk file')
    if not k.GetFileInformationByHandle(handle,c.byref(info)):
        raise c.WinError(c.get_last_error())
    if info.attributes & (0x400 | 0x10):
        raise ValueError('Source is a reparse point or directory')
    if not k.GetFileInformationByHandleEx(handle,0,c.byref(basic),c.sizeof(basic)):
        raise c.WinError(c.get_last_error())
    if not k.GetFileInformationByHandleEx(handle,18,c.byref(file_id),c.sizeof(file_id)):
        raise c.WinError(c.get_last_error())
    if path is not None:
        buffer=c.create_unicode_buffer(32768)
        count=k.GetFinalPathNameByHandleW(handle,buffer,len(buffer),0)
        if not count or count>=len(buffer):
            raise OSError('Cannot verify source handle path')
        actual=buffer.value
        # Win32 extended DOS/UNC prefixes describe the same lexical path.
        if actual.startswith('\\\\?\\UNC\\'):actual='\\\\'+actual[8:]
        elif actual.startswith('\\\\?\\'):actual=actual[4:]
        if os.path.normcase(os.path.abspath(path)) != os.path.normcase(actual):
            raise ValueError('Source handle path changed or traversed a reparse point')
    return (file_id.volume,bytes(file_id.ident),
        (info.size_high<<32)|info.size_low,basic.write,basic.change)


def descriptor_identity(fd, path=None):
    if _windows():
        import msvcrt
        return _native_identity(msvcrt.get_osfhandle(fd),path)
    info=os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):raise ValueError('Source is not a regular file')
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def _open_posix_no_follow(path, *, flags=os.O_RDONLY):
    """Open every path component by directory descriptor, never via a link.

    O_NOFOLLOW on the final filename alone does not protect its parents: a
    concurrent rename can replace an already-checked directory with a symlink.
    O_NONBLOCK also prevents a substituted FIFO from hanging the media worker.
    """
    if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        raise OSError('Safe no-follow media opening is unsupported on this POSIX host')
    parts = path.parts
    if len(parts) < 2:
        raise ValueError('Source is not a file path')
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open(parts[0], directory_flags)
    try:
        for component in parts[1:-1]:
            child = os.open(component, directory_flags, dir_fd=parent)
            os.close(parent)
            parent = child
        fd = os.open(parts[-1], flags | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=parent)
        try:
            descriptor_identity(fd)
            os.set_blocking(fd, True)
            return fd
        except BaseException:
            os.close(fd)
            raise
    finally:
        os.close(parent)


def open_source(path):
    path=Path(os.path.abspath(path));reject_path_link(path)
    if not _windows():
        return _open_posix_no_follow(path)
    import msvcrt
    c,w,k=_api()
    # FILE_SHARE_READ only: writers and deletion cannot race this snapshot.
    handle=k.CreateFileW(str(path),0x80000000,1,None,3,0x00200000,None)
    if handle == c.c_void_p(-1).value:raise c.WinError(c.get_last_error())
    try:
        _native_identity(handle,path)
        fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
    except BaseException:
        k.CloseHandle(handle);raise
    return fd


def open_managed_lock(path):
    """Open a permanent writable lock inode without POSIX symlink traversal.

    The Windows locking path is unchanged until physical Win32 validation.
    On POSIX the same no-follow walk protects the writable lock as the media
    source. A hard-linked lock inode is rejected to avoid external writes.
    """
    path=Path(os.path.abspath(path));reject_path_link(path)
    if _windows():
        return path.open('a+b')
    fd=_open_posix_no_follow(path,flags=os.O_RDWR|os.O_CREAT)
    try:
        if os.fstat(fd).st_nlink != 1:
            raise ValueError('Media lock file has unexpected hard links')
        return os.fdopen(fd,'r+b')
    except BaseException:
        os.close(fd)
        raise


def source_identity(path):
    # Use the same no-follow handle walk for pathname observations as for
    # streaming. A separate os.stat(path) could traverse a swapped ancestor.
    fd=open_source(path)
    try:return descriptor_identity(fd,path)
    finally:os.close(fd)

def read_stable_bytes(path, *, write=None, expected=None, check=None):
    """Hash managed media through a stable, no-follow file handle.

    Optionally stream to a caller-owned exclusive temporary writer. The source
    pathname and descriptor must identify the same unchanged file throughout.
    The caller owns temporary-file cleanup and durable fsync/commit.
    """
    import hashlib
    if write is not None and not callable(write):
        raise TypeError('Stable media sink must be callable')
    if check is not None and not callable(check):
        raise TypeError('Stable media cancellation check must be callable')
    if expected is not None and (not isinstance(expected,tuple) or len(expected)!=2
            or not isinstance(expected[0],str) or len(expected[0])!=64
            or any(c not in '0123456789abcdef' for c in expected[0])
            or type(expected[1]) is not int or expected[1]<0):
        raise ValueError('Invalid expected stable media digest')
    if check is not None:check()
    before=source_identity(path)
    with os.fdopen(open_source(path),'rb') as stream:
        if descriptor_identity(stream.fileno(),path)!=before:
            raise ValueError('Stable media source changed before reading')
        digest=hashlib.sha256();length=0
        while True:
            if check is not None:check()
            block=stream.read(1024*1024)
            if check is not None:check()
            if not block:break
            digest.update(block);length+=len(block)
            if write is not None:write(block)
        if (descriptor_identity(stream.fileno(),path)!=before
                or source_identity(path)!=before):
            raise ValueError('Stable media source changed during reading')
    if check is not None:check()
    result=(digest.hexdigest(),length)
    if expected is not None and result!=expected:
        raise ValueError('Stable media source bytes differ from durable receipt')
    return result
