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


def open_source(path):
    path=Path(os.path.abspath(path));reject_path_link(path)
    if not _windows():
        return os.open(path,os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0))
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


def source_identity(path):
    reject_path_link(Path(os.path.abspath(path)))
    if not _windows():
        info=os.stat(path,follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):raise ValueError('Source is not a regular file')
        return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
    fd=open_source(path)
    try:return descriptor_identity(fd,path)
    finally:os.close(fd)
