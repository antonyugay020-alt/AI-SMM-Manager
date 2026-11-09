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
    def __init__(self,after=3,receipt=None,mutate_output=False,mutate_source=False):
        self.polls=0;self.after=after;self.receipt=receipt
        self.mutate_output=mutate_output
        self.mutate_source=mutate_source
    def poll(self):
        self.polls+=1
        if self.receipt is not None and self.polls==self.after:
            path,record=self.receipt
            request=json.loads((path.parent/'quality_request.json').read_text(encoding='utf-8'))
            record=dict(record)
            for key in ('request_id','output_sha256','source_sha256'):
                record.setdefault(key,request[key])
            if self.mutate_output:
                (path.parent.parent/'result.png').write_bytes(b'changed-output')
            if self.mutate_source:
                Path(request['case']['sources'][0]).write_bytes(b'changed-source')
            path.write_text(json.dumps(record),encoding='utf-8')
        if self.after and self.polls>=self.after:
            self.returncode=0
        return self.returncode


def run_with_mock(tmp_path,child,timeout=3,with_source=False):
    closed=[]
    @contextmanager
    def owned(*args,**kwargs):
        try:yield child
        finally:closed.append(True)
    folder=tmp_path/'quality'
    output=tmp_path/'result.png';output.write_bytes(b'fixture-do-not-image-open')
    reviewer=BoundedQualityReview('gemma3:4b','a'*64,timeout=timeout)
    case={'prompt':'watercolor'}
    if with_source:
        source=tmp_path/'source.png';source.write_bytes(b'original-source')
        case['sources']=[str(source)]
    with patch('native_process_runtime.owned_native_process',owned):
        result=reviewer.run_with_pump(
            case,output,folder,pump=lambda:None)
    return result,closed,folder


def complete_receipt(status='PASS',model='gemma3:4b'):
    return {'status':status,'model':model,'digest':'a'*64,
        'metrics':{'width':256,'height':256,'contrast':.2,'mean_absolute_change':.25},
        'verdict':{'verdict':'PASS' if status=='PASS' else 'FAIL',
            'instruction':'PASS' if status=='PASS' else 'UNCERTAIN',
            'preservation':'PASS','reference_or_mask':'NOT_APPLICABLE',
            'temporal':'NOT_APPLICABLE','observations':'The edited object retains its subject and composition.'}}


def test_owned_quality_pass_proves_matching_model_and_digest(tmp_path):
    folder=tmp_path/'quality';report=folder/'quality.json'
    receipt=complete_receipt()
    result,closed,_=run_with_mock(tmp_path,Child(receipt=(report,receipt)))
    assert all(result.get(key)==value for key,value in receipt.items())
    assert len(result['request_id'])==32
    assert len(result['output_sha256'])==64
    assert closed==[True]
    assert (folder/'quality_request.json').is_file()


def test_rejected_quality_remains_blocked_and_child_closes(tmp_path):
    receipt=complete_receipt('BLOCKED')
    result,closed,_=run_with_mock(tmp_path,Child(receipt=(tmp_path/'quality'/'quality.json',receipt)))
    assert result['status']=='BLOCKED'
    assert closed==[True]


def test_invalid_model_receipt_fails_closed_and_closes(tmp_path):
    receipt=complete_receipt(model='different:1b')
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


def test_incomplete_pass_rejected_even_with_correct_model_digest(tmp_path):
    receipt={'status':'PASS','model':'gemma3:4b','digest':'a'*64}
    with pytest.raises(RuntimeError,match='LOCAL_QUALITY_REVIEW_RECEIPT_INVALID'):
        run_with_mock(tmp_path,Child(receipt=(tmp_path/'quality'/'quality.json',receipt)))
    error=json.loads((tmp_path/'quality'/'quality_process_error.json').read_text(encoding='utf-8'))
    assert error['error_type']=='RuntimeError'


def test_unproven_reference_or_video_temporal_quality_never_passes():
    from qa_quality_worker import validate_quality_receipt
    report=complete_receipt()
    report['verdict']['reference_or_mask']='NOT_APPLICABLE'
    with pytest.raises(ValueError,match='RECEIPT_INVALID'):
        validate_quality_receipt(report,{'task':'image_reference','sources':['a','b']},'gemma3:4b','a'*64)
    report=complete_receipt()
    report['metrics']={'duration':2,'sampled_frames':[{'contrast':.3}]}
    report['verdict']['temporal']='NOT_APPLICABLE'
    with pytest.raises(ValueError,match='RECEIPT_INVALID'):
        validate_quality_receipt(report,{'task':'video_generative','sources':['a']},'gemma3:4b','a'*64)


def test_stale_pass_receipt_is_never_reused_after_successful_child_exit(tmp_path):
    folder=tmp_path/'quality';folder.mkdir()
    (folder/'quality.json').write_text(json.dumps(complete_receipt()),encoding='utf-8')
    with pytest.raises(FileNotFoundError):
        run_with_mock(tmp_path,Child(after=2))
    assert not (folder/'quality.json').exists()
    assert (folder/'quality_process_error.json').is_file()


def test_receipt_from_another_request_is_rejected(tmp_path):
    receipt={**complete_receipt(),'request_id':'f'*32}
    with pytest.raises(RuntimeError,match='RECEIPT_INVALID'):
        run_with_mock(tmp_path,Child(receipt=(tmp_path/'quality'/'quality.json',receipt)))


def test_output_changed_while_reviewing_is_rejected(tmp_path):
    receipt=complete_receipt()
    with pytest.raises(RuntimeError,match='RECEIPT_INVALID'):
        run_with_mock(tmp_path,Child(receipt=(tmp_path/'quality'/'quality.json',receipt),
                                     mutate_output=True))


def test_owned_quality_binds_original_source_and_rejects_mid_review_mutation(tmp_path):
    receipt=complete_receipt()
    result,closed,folder=run_with_mock(tmp_path,Child(
        receipt=(tmp_path/'quality'/'quality.json',receipt)),with_source=True)
    assert len(result['source_sha256'])==1
    assert closed==[True]
    receipt=complete_receipt()
    with pytest.raises(RuntimeError,match='RECEIPT_INVALID'):
        run_with_mock(tmp_path,Child(
            receipt=(tmp_path/'quality'/'quality.json',receipt),
            mutate_source=True),with_source=True)
    error=json.loads((folder/'quality_process_error.json').read_text(encoding='utf-8'))
    assert error['error_type']=='RuntimeError'


def test_windows_generation_rechecks_source_and_output_after_visual_review():
    source=(ROOT/'AI_SMM_Work_Architecture_A75/generative_acceptance.py').read_text(encoding='utf-8')
    assert "raise ValueError('Protected source changed during review')" in source
    assert "raise ValueError('Generated output changed during review')" in source
    assert source.index("raise ValueError('Protected source changed during review')") < source.index("window.grab().save")
