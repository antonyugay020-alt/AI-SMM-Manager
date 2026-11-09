"""Non-GPU regression for bounded, owned local semantic QA.

Mocks replace only the child process; strict rejection/PASS evidence and
ownership deadlines are checked without Ollama, media models or paid APIs.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
from qa_quality_worker import BoundedQualityReview


class Child:
    pid=345678
    returncode=None
    def __init__(self,after=3,receipt=None):
        self.polls=0;self.after=after;self.receipt=receipt
    def poll(self):
        self.polls+=1
        if self.receipt is not None and self.polls==self.after:
            path,record=self.receipt
            path.write_text(json.dumps(record),encoding='utf-8')
        if self.after and self.polls>=self.after:
            self.returncode=0
        return self.returncode


def run_with_mock(tmp_path,child,timeout=3):
    closed=[]
    @contextmanager
    def owned(*args,**kwargs):
        try:yield child
        finally:closed.append(True)
    folder=tmp_path/'quality'
    output=tmp_path/'result.png';output.write_bytes(b'fixture-do-not-image-open')
    reviewer=BoundedQualityReview('gemma3:4b','a'*64,timeout=timeout)
    with patch('native_process_runtime.owned_native_process',owned):
        result=reviewer.run_with_pump(
            {'prompt':'watercolor'},output,folder,pump=lambda:None)
    return result,closed,folder


def test_owned_quality_pass_proves_matching_model_and_digest(tmp_path):
    folder=tmp_path/'quality';report=folder/'quality.json'
    receipt={'status':'PASS','model':'gemma3:4b','digest':'a'*64}
    result,closed,_=run_with_mock(tmp_path,Child(receipt=(report,receipt)))
    assert result==receipt
    assert closed==[True]
    assert (folder/'quality_request.json').is_file()


def test_rejected_quality_remains_blocked_and_child_closes(tmp_path):
    receipt={'status':'BLOCKED','model':'gemma3:4b','digest':'a'*64}
    result,closed,_=run_with_mock(tmp_path,Child(receipt=(tmp_path/'quality'/'quality.json',receipt)))
    assert result['status']=='BLOCKED'
    assert closed==[True]


def test_invalid_model_receipt_fails_closed_and_closes(tmp_path):
    receipt={'status':'PASS','model':'different:1b','digest':'a'*64}
    with pytest.raises(RuntimeError,match='RECEIPT_INVALID'):
        run_with_mock(tmp_path,Child(receipt=(tmp_path/'quality'/'quality.json',receipt)))
    diagnostic=json.loads((tmp_path/'quality'/'quality_process_error.json').read_text())
    assert diagnostic['error_type']=='RuntimeError'


def test_hung_quality_review_times_out_and_records_owner_pid(tmp_path):
    with pytest.raises(TimeoutError,match='LOCAL_QUALITY_REVIEW_TIMEOUT'):
        run_with_mock(tmp_path,Child(after=0),timeout=.08)
    diagnostic=json.loads((tmp_path/'quality'/'quality_process_error.json').read_text())
    assert diagnostic['error_type']=='TimeoutError'
    assert diagnostic['pid']==345678


def test_windows_acceptance_uses_the_owned_process_not_an_inline_http_call():
    source=(ROOT/'AI_SMM_Work_Architecture_A75/windows_acceptance.py').read_text(encoding='utf-8')
    assert 'callback=BoundedQualityReview(vision.model,vision.digest,timeout=900)' in source
    assert 'def callback(case,path,folder):' not in source
    acceptance=(ROOT/'AI_SMM_Work_Architecture_A75/generative_acceptance.py').read_text(encoding='utf-8')
    assert "quality_callback=_bounded_quality_callback(quality_callback)" in acceptance
    assert "raise TypeError('BOUNDED_QUALITY_EXECUTOR_REQUIRED')" in acceptance
    assert 'pump=app.processEvents' in acceptance
    assert 'ThreadPoolExecutor' not in acceptance


def test_unowned_quality_callback_rejected_before_any_gpu_work():
    from generative_acceptance import _bounded_quality_callback
    with pytest.raises(TypeError,match='BOUNDED_QUALITY_EXECUTOR_REQUIRED'):
        _bounded_quality_callback(lambda case,output,folder: {'status':'PASS'})
    assert _bounded_quality_callback(None) is None
    class Owned:
        def run_with_pump(self,case,output,folder,*,pump):
            return {'status':'BLOCKED'}
    owned=Owned()
    assert _bounded_quality_callback(owned) is owned
