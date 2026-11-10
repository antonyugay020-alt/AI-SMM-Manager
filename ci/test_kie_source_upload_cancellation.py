"""Offline cancellation chain for unpaid Kie source preparation."""
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'AI_SMM_Work_Architecture_A75'
sys.path.insert(0,str(WORK))
from media_upload_transfer import upload_with_check,copy_snapshot_with_check


def test_modern_adapter_checks_during_upload():
    calls=[]
    def check():calls.append('check')
    def modern(path,*,check):
        check();return 'ok'
    assert upload_with_check(modern,'file',check=check)=='ok'
    assert calls==['check']*3


def test_legacy_adapter_checked_before_after():
    calls=[]
    assert upload_with_check(lambda p:calls.append(p) or 'ok','file',check=lambda:calls.append('check'))=='ok'
    assert calls==['check','file','check']


def test_cancel_before_upload_never_calls_adapter():
    def cancel():raise InterruptedError('stopped')
    with pytest.raises(InterruptedError):
        upload_with_check(lambda p:pytest.fail('uploaded'),'file',check=cancel)


def test_adapter_failure_is_not_retried():
    count=[]
    def fail(p,**kw):count.append(p);raise RuntimeError('transport failed')
    with pytest.raises(RuntimeError,match='transport failed'):
        upload_with_check(fail,'file',check=lambda:None)
    assert count==['file']


def test_snapshot_copy_is_exact_and_chunk_cancelled(tmp_path):
    src=tmp_path/'source';src.write_bytes(b'A'*2048)
    dst=tmp_path/'copy';count=[]
    def check():
        count.append(1)
        if len(count)>=5:raise InterruptedError('stopped')
    with pytest.raises(InterruptedError):
        copy_snapshot_with_check(src,dst,check=check,chunk_size=512)
    assert dst.stat().st_size<2048
    copy_snapshot_with_check(src,tmp_path/'complete',check=lambda:None,chunk_size=512)
    assert (tmp_path/'complete').read_bytes()==src.read_bytes()


def test_invalid_chunk_size_and_check_fail_closed(tmp_path):
    with pytest.raises(ValueError):copy_snapshot_with_check(tmp_path/'a',tmp_path/'b',chunk_size=0)
    with pytest.raises(TypeError):upload_with_check(lambda p:None,'x',check='bad')


def test_composition_propagates_cancellation_to_native_upload_stream():
    source=(WORK/'application_paid_media.py').read_text(encoding='utf-8')
    assert source.count('explicit_upload_consent=True,check=check')==1
    assert 'path, explicit_upload_consent=True, check=check' in source
    client=(WORK/'kie_media_client.py').read_text(encoding='utf-8')
    assert 'def upload_file(self, filename, *, explicit_upload_consent=False, check=None)' in client
    assert 'checkpoint(check)' in client


def test_qt_worker_and_intent_forward_cancellation():
    worker=(WORK/'kie_media_worker.py').read_text(encoding='utf-8')
    runtime=(WORK/'media_generation_runtime.py').read_text(encoding='utf-8')
    gateway=(WORK/'kie_media_runtime.py').read_text(encoding='utf-8')
    assert 'cancel_check=check_interrupted' in worker
    assert 'copy_snapshot_with_check(path,snapshot,check=upload_checkpoint)' in runtime
    assert 'cancel_check=ensure_active' in runtime
    assert 'upload_with_check(self.upload_file,local,check=cancel_check)' in gateway


def test_image_gateway_cancelled_before_external_upload(monkeypatch):
    import kie_media_runtime as module
    monkeypatch.setattr(module,'verified_image',lambda path,**kw:Path(path))
    monkeypatch.setattr(module.KieMediaGateway,'_authorized',lambda self,owner,path:path)
    called=[]
    gateway=module.KieMediaGateway(None,None,upload_file=lambda path:called.append(path),
                                   create_task=None,query_task=None)
    def stop():raise InterruptedError('stopped before upload')
    with pytest.raises(InterruptedError,match='stopped before upload'):
        gateway.prepare_image('owner',prompt='watercolor forest',
                              sources=['photo.png'],cancel_check=stop)
    assert called==[]
