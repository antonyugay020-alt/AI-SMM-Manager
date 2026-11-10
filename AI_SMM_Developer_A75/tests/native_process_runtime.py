"""Isolated, parent-owned native Python; shared preflight/inference launch path.

Stdlib only: do not import Torch, Pillow or Qt in the parent. No process-name
kills or changes to global environment. Windows jobs contain only our child.
"""
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from owned_process_cleanup import close_owned_process

_spawn_native=subprocess.Popen
PROTOCOL='native_runtime_v1'
MAX_NATIVE_LOG_BYTES = 16 * 1024 * 1024
_BOOTSTRAP = """import os,sys,runpy
owner=int(sys.argv.pop(1))
if sys.platform.startswith('linux'):
 import ctypes,signal
 libc=ctypes.CDLL(None,use_errno=True)
 if libc.prctl(1,signal.SIGKILL,0,0,0)!=0:raise OSError(ctypes.get_errno(),'parent-death guard failed')
 if os.getppid()!=owner:os._exit(75)
script=sys.argv.pop(1)
sys.path.insert(0,os.path.dirname(script))
sys.argv[0]=script
runpy.run_path(script,run_name='__main__')
"""

class NativeRuntimeError(RuntimeError):
    def __init__(self,code,diagnostic):
        self.public_code=code;self.diagnostic=diagnostic
        super().__init__(code)


def native_command(python,script,*arguments):
    return [str(python),'-I','-u','-X','utf8','-c',_BOOTSTRAP,
        str(os.getpid()),str(Path(script).resolve()),*map(str,arguments)]


def native_environment(environment):
    result=dict(environment)
    for name in ('PYTHONPATH','PYTHONHOME','PYTHONSTARTUP','PYTHONINSPECT'):
        result.pop(name,None)
    result.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
        HF_HUB_DISABLE_TELEMETRY='1',DO_NOT_TRACK='1')
    return result


def _create_job():
    if os.name!='nt':return None
    from windows_process_group import WindowsJob
    return WindowsJob()


@contextmanager
def owned_native_process(command,*,env,stdout,stderr):
    job=None;process=None
    try:
        flags=0
        job=_create_job()
        if job is not None:flags=0x00000200|0x00000004|0x08000000
        process=_spawn_native(command,stdin=subprocess.PIPE,stdout=stdout,
            stderr=stderr,env=native_environment(env),creationflags=flags,
            start_new_session=os.name!='nt')
        if job is not None:job.assign_resume(process)
        yield process
    finally:
        # The same owned-tree cleanup is used by FFmpeg/ffprobe and native
        # inference. A reaped POSIX leader must not orphan its descendants.
        close_owned_process(process,job,grace=5)


def wait_owned_native(process, log, *, timeout, pump=None, max_log_bytes=None):
    """Wait for an owned native child with a finite deadline and disk budget.

    The caller MUST keep the process inside owned_native_process so a timeout,
    output flood or Qt interruption kills only its own child and descendants.
    Both stdout and stderr must target the same regular file.
    """
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 0 < timeout <= 3600):
        raise ValueError('Invalid native process deadline')
    if max_log_bytes is None:
        max_log_bytes = MAX_NATIVE_LOG_BYTES
    if (type(max_log_bytes) is not int
            or not 0 < max_log_bytes <= 16 * 1024 * 1024):
        raise ValueError('Invalid native log budget')
    if pump is not None and not callable(pump):
        raise TypeError('Invalid native event pump')
    deadline = time.monotonic() + timeout
    while True:
        if pump is not None:
            pump()
        if os.fstat(log.fileno()).st_size > max_log_bytes:
            raise RuntimeError('NATIVE_LOG_BUDGET_EXCEEDED')
        code = process.poll()
        if time.monotonic() >= deadline:
            raise TimeoutError('NATIVE_PROCESS_TIMEOUT')
        if code is not None:
            return code
        time.sleep(min(.05, max(0, deadline - time.monotonic())))


def validate_runtime_receipt(runtime):
    if not isinstance(runtime,dict):raise ValueError('Missing native runtime object')
    required={'cuda','name','capability','total','device_count','torch','diffusers',
        'transformers','accelerate','safetensors'}
    if set(runtime)!=required:raise ValueError('Incomplete native runtime receipt')
    if (type(runtime['cuda']) is not bool or not isinstance(runtime['name'],str)
            or type(runtime['total']) is not int or runtime['total']<0
            or type(runtime['device_count']) is not int or runtime['device_count']<0
            or not isinstance(runtime['capability'],list)
            or any(type(part) is not int for part in runtime['capability'])):
        raise ValueError('Invalid native hardware evidence')
    if runtime['cuda']:
        if not runtime['name'] or runtime['total']<=0 or runtime['device_count']<1 or len(runtime['capability'])!=2:
            raise ValueError('Contradictory CUDA evidence')
    elif runtime['name'] or runtime['total'] or runtime['capability']:
        raise ValueError('CPU runtime claimed GPU properties')
    if any(not isinstance(runtime[key],str) or not runtime[key].strip()
           for key in ('torch','diffusers','transformers','accelerate','safetensors')):
        raise ValueError('Invalid native dependency evidence')
    return runtime


def probe_native_runtime(python,environment,*,timeout=90,diagnostic_path=None):
    """Run the SAME Torch/Pillow/CUDA startup as inference, without model loading."""
    evidence={'protocol':PROTOCOL,'status':'BLOCKED','python':str(python)}
    code='NATIVE_PREFLIGHT_FAILED';process=None
    with tempfile.TemporaryFile() as log:
        try:
            command=native_command(python,Path(__file__).with_name('generative_runner.py'),'--preflight')
            with owned_native_process(command,env=environment,stdout=log,stderr=log) as process:
                evidence['child_pid']=process.pid
                try:
                    exit_code=wait_owned_native(process,log,timeout=timeout)
                except TimeoutError:
                    code='NATIVE_PREFLIGHT_TIMEOUT'
                    raise
                except RuntimeError as exc:
                    if str(exc)=='NATIVE_LOG_BUDGET_EXCEEDED':
                        code='NATIVE_PREFLIGHT_LOG_BUDGET'
                    raise
                if exit_code:raise RuntimeError('Native child failed')
            log.seek(0);raw=log.read()
            receipts=[]
            for line in raw.splitlines():
                try:record=json.loads(line)
                except (ValueError,UnicodeDecodeError):continue
                if isinstance(record,dict) and record.get('stage')=='runtime_preflight_complete':
                    receipts.append(record.get('runtime'))
            if len(receipts)!=1 or not isinstance(receipts[0],dict):
                raise ValueError('Missing or ambiguous native runtime receipt')
            validate_runtime_receipt(receipts[0])
            evidence.update(status='PASS',runtime=receipts[0])
            return receipts[0]
        except Exception as exc:
            evidence.update(error_code=code,error_type=type(exc).__name__)
            raise NativeRuntimeError(code,evidence) from exc
        finally:
            if process is not None:evidence['exit_code']=process.poll()
            size=log.seek(0,2);log.seek(max(0,size-16384))
            evidence.update(log_bytes=size,log_tail=log.read().decode('utf-8','replace'))
            if diagnostic_path is not None:
                from durable_json import atomic_write_json
                atomic_write_json(diagnostic_path,evidence)
