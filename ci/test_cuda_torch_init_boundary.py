"""Offline regression for the native Torch initialization / GPU lease boundary.

No GPU, Ollama, model download, privileged Windows operation or paid API needed.
"""
import json
import os
from contextlib import contextmanager
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
import generative_runner as runner


def test_windows_worker_imports_native_torch_before_acquiring_gpu_lease(tmp_path,monkeypatch):
    request=tmp_path/'request.json'
    request.write_text(json.dumps({'request':{'generation':{'device':'cuda'}},
        'inputs':[],'target':str(tmp_path/'image.png')}),encoding='utf-8')
    order=[]
    monkeypatch.setattr(sys,'argv',['generative_runner.py','--request',str(request)])
    for key in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE',
                'HF_HUB_DISABLE_TELEMETRY','DO_NOT_TRACK'):
        monkeypatch.setenv(key,os.environ.get(key,''))
    monkeypatch.setattr(runner.threading.Thread,'start',lambda self:order.append('watch_armed'))
    monkeypatch.setattr(runner,'validate_generation',lambda item:order.append('validate'))
    monkeypatch.setattr(runner,'verify_bundle',lambda p:order.append('verify'))
    module=object()
    def initialize():
        order.append('import_torch')
        return module
    monkeypatch.setattr(runner,'initialize_python_torch',initialize)
    @contextmanager
    def lease():
        order.append('lease_acquired')
        try:yield
        finally:order.append('lease_released')
    monkeypatch.setattr(runner,'device_lock',lease)
    def fake_run(req,inputs,target,*,preloaded_torch=None):
        assert preloaded_torch is module
        order.append('run')
    monkeypatch.setattr(runner,'run',fake_run)
    assert runner.main()==0
    assert order==['validate','verify','import_torch',
                   'watch_armed','lease_acquired','run','lease_released']


def test_failed_native_import_never_opens_gpu_lease(tmp_path,monkeypatch):
    request=tmp_path/'request.json'
    request.write_text(json.dumps({'request':{'generation':{}},
        'inputs':[],'target':str(tmp_path/'image.png')}),encoding='utf-8')
    monkeypatch.setattr(sys,'argv',['generative_runner.py','--request',str(request)])
    for key in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE',
                'HF_HUB_DISABLE_TELEMETRY','DO_NOT_TRACK'):
        monkeypatch.setenv(key,os.environ.get(key,''))
    monkeypatch.setattr(runner.threading.Thread,'start',lambda self:None)
    monkeypatch.setattr(runner,'validate_generation',lambda item:None)
    monkeypatch.setattr(runner,'verify_bundle',lambda profile:None)
    def broken():
        raise RuntimeError('TORCH_IMPORT_FAILED')
    monkeypatch.setattr(runner,'initialize_python_torch',broken)
    def not_allowed():
        raise AssertionError('GPU lock opened after import failure')
    monkeypatch.setattr(runner,'device_lock',not_allowed)
    try:runner.main()
    except RuntimeError as exc:assert str(exc)=='TORCH_IMPORT_FAILED'
    else:raise AssertionError('Missing import failure')
