"""Own only the application-created subprocess tree (including QA) with a Windows kill-on-close Job Object.

Child starts suspended, is assigned before executing any application code, then
resumes. No process-name/global taskkill. Actual Win32 acceptance is unrun here.
"""
import ctypes
from ctypes import wintypes as w
import os
import math
import time


class WindowsJob:
    def __init__(self):
        if os.name!='nt':raise RuntimeError('Win32 Job Object requires Windows')
        class Basic(ctypes.Structure):
            _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),('flags',w.DWORD),
                ('min_working_set',ctypes.c_size_t),('max_working_set',ctypes.c_size_t),('active_limit',w.DWORD),
                ('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
        class IO(ctypes.Structure):_fields_=[(name,ctypes.c_ulonglong) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        class Extended(ctypes.Structure):
            _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        self.api=ctypes.WinDLL('kernel32',use_last_error=True)
        self.api.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];self.api.CreateJobObjectW.restype=w.HANDLE
        self.api.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD];self.api.SetInformationJobObject.restype=w.BOOL
        self.api.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE];self.api.AssignProcessToJobObject.restype=w.BOOL
        self.api.CloseHandle.argtypes=[w.HANDLE];self.api.CloseHandle.restype=w.BOOL
        self.api.TerminateJobObject.argtypes=[w.HANDLE,w.UINT];self.api.TerminateJobObject.restype=w.BOOL
        self.api.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];self.api.OpenProcess.restype=w.HANDLE
        self.api.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];self.api.WaitForSingleObject.restype=w.DWORD
        self.api.QueryInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD,ctypes.c_void_p];self.api.QueryInformationJobObject.restype=w.BOOL
        self.handle=self.api.CreateJobObjectW(None,None)
        if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
        limits=Extended();limits.basic.flags=0x2000
        if not self.api.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            error=ctypes.get_last_error();self.close();raise ctypes.WinError(error)

    def assign_resume(self,child):
        handle=w.HANDLE(int(child._handle))
        if not self.api.AssignProcessToJobObject(self.handle,handle):raise ctypes.WinError(ctypes.get_last_error())
        resume=ctypes.WinDLL('ntdll').NtResumeProcess;resume.argtypes=[w.HANDLE];resume.restype=w.LONG
        status=resume(handle)
        if status!=0:raise RuntimeError('Cannot resume owned suspended QA process: '+str(status))

    def active(self):
        class Accounting(ctypes.Structure):
            _fields_=[('user',ctypes.c_longlong),('kernel',ctypes.c_longlong),('period_user',ctypes.c_longlong),('period_kernel',ctypes.c_longlong),
                ('page_faults',w.DWORD),('total',w.DWORD),('active',w.DWORD),('terminated',w.DWORD)]
        data=Accounting()
        if not self.api.QueryInformationJobObject(self.handle,1,ctypes.byref(data),ctypes.sizeof(data),None):raise ctypes.WinError(ctypes.get_last_error())
        return data.active

    def member_pids(self):
        """Enumerate this job only; never enumerate/terminate system processes."""
        capacity=16
        while capacity<=4096:
            class ProcessIds(ctypes.Structure):
                _fields_=[('assigned',w.DWORD),('listed',w.DWORD),
                          ('ids',ctypes.c_size_t*capacity)]
            data=ProcessIds()
            if self.api.QueryInformationJobObject(self.handle,3,ctypes.byref(data),ctypes.sizeof(data),None):
                if data.listed>capacity:raise RuntimeError('Invalid owned job PID count')
                return sorted(set(int(data.ids[i]) for i in range(data.listed)))
            error=ctypes.get_last_error()
            if error!=234:raise ctypes.WinError(error)  # ERROR_MORE_DATA
            capacity*=2
        raise RuntimeError('Owned job PID list exceeds safety limit')

    def terminate_and_wait(self,*,timeout=5):
        """Drain the owned tree before releasing its handles and output files.

        KILL_ON_JOB_CLOSE requests termination asynchronously. Waiting only on
        Popen may reap a venv redirector while its Python descendant still has
        the log open. ActiveProcesses is an accounting counter, not a wait on
        each process object's completion. Pin member handles before termination
        and wait for their signals as well as the empty job accounting state.
        """
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=10:
            raise ValueError('Invalid Windows job drain timeout')
        if not getattr(self,'handle',None):return
        deadline=time.monotonic()+timeout
        handles=[]
        self.drain_evidence={'status':'BLOCKED','member_pids':[],'waited_pids':[]}
        try:
            for pid in self.member_pids():
                handle=self.api.OpenProcess(0x00100000,False,pid)  # SYNCHRONIZE only
                if not handle:
                    error=ctypes.get_last_error()
                    if error==87 and pid not in self.member_pids():continue  # exited before OpenProcess
                    raise ctypes.WinError(error)
                handles.append((pid,handle))
                self.drain_evidence['member_pids'].append(pid)
            if not self.api.TerminateJobObject(self.handle,1):raise ctypes.WinError(ctypes.get_last_error())
            for pid,handle in handles:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise TimeoutError('OWNED_WINDOWS_JOB_DRAIN_TIMEOUT')
                result=self.api.WaitForSingleObject(handle,max(1,math.ceil(remaining*1000)))
                if result==0x102:raise TimeoutError('OWNED_WINDOWS_JOB_DRAIN_TIMEOUT')
                if result!=0:raise ctypes.WinError(ctypes.get_last_error())
                self.drain_evidence['waited_pids'].append(pid)
            while self.active():
                remaining=deadline-time.monotonic()
                if remaining<=0:raise TimeoutError('OWNED_WINDOWS_JOB_DRAIN_TIMEOUT')
                time.sleep(min(.01,remaining))
            self.drain_evidence['status']='DRAINED'
        finally:
            for _,handle in handles:self.api.CloseHandle(handle)

    def close(self):
        if getattr(self,'handle',None):self.api.CloseHandle(self.handle);self.handle=None
