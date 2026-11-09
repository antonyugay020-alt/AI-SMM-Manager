"""Actual owned Python subprocess contract with synthetic local-vision fixtures.

Runs real native_command/owned_native_process from frozen project source. The
fake vision model is scoped to a temp child directory and never touches Ollama,
CUDA, the network, the user's existing models or Windows trading workloads.
It verifies process lifecycle, NOT actual visual output quality.
"""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import time

import pytest

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'AI_SMM_Work_Architecture_A75'


@pytest.fixture
def isolated_quality_module(tmp_path,monkeypatch):
    for name in ('qa_quality_worker.py','qa_receipt_provenance.py',
                 'native_process_runtime.py','windows_process_group.py'):
        shutil.copyfile(WORK/name,tmp_path/name)
    (tmp_path/'durable_json.py').write_text(
        'import json\nfrom pathlib import Path\n'
        'def atomic_write_json(path,data):\n'
        ' p=Path(path);temp=p.with_suffix(p.suffix+".tmp")\n'
        ' temp.write_text(json.dumps(data),encoding="utf-8")\n'
        ' temp.replace(p)\n',encoding='utf-8')
    (tmp_path/'local_media_quality.py').write_text(
        'import time\n'
        'class ObjectiveQualityRejected(ValueError):pass\n'
        'class LocalVision:\n'
        ' def __init__(self,model):self.model=model;self.digest=None\n'
        ' def installed(self):self.digest="a"*64;return self.digest\n'
        ' def review(self,case,output,folder,metrics):\n'
        '  if case.get("behavior")=="hang":time.sleep(30)\n'
        '  state="BLOCKED" if case.get("behavior")=="reject" else "PASS"\n'
        '  return {"status":state,"model":self.model,"digest":self.digest,'
        '"verdict":{"verdict":"FAIL" if state=="BLOCKED" else "PASS",'
        '"instruction":"UNCERTAIN" if state=="BLOCKED" else "PASS",'
        '"preservation":"PASS","reference_or_mask":"NOT_APPLICABLE",'
        '"temporal":"NOT_APPLICABLE","observations":"The subject remains legible and the composition is preserved."},'
        '"scope":"synthetic CPU fixture"}\n'
        'def objective_quality(case,output,folder):\n'
        ' if case.get("behavior")=="blank":raise ObjectiveQualityRejected("Blank generated image")\n'
        ' if case.get("behavior")=="infra":raise RuntimeError("Local codec failure")\n'
        ' return {"width":256,"height":256,"contrast":.2,"mean_absolute_change":.25}\n',
        encoding='utf-8')
    monkeypatch.syspath_prepend(str(tmp_path))
    # Import the byte-identical QA worker from an isolated file location.
    spec=importlib.util.spec_from_file_location('isolated_qa_quality_worker',tmp_path/'qa_quality_worker.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def _call(module,tmp_path,behavior,timeout):
    output=tmp_path/'result.png';output.write_bytes(b'fixture')
    worker=module.BoundedQualityReview('gemma3:4b','a'*64,timeout=timeout)
    return worker.run_with_pump({'behavior':behavior},output,tmp_path/'proof',pump=lambda:None)


def test_real_owned_child_returns_checked_local_receipt(isolated_quality_module,tmp_path):
    result=_call(isolated_quality_module,tmp_path,'pass',5)
    assert result['status']=='PASS'
    assert result['model']=='gemma3:4b'
    assert result['digest']=='a'*64
    from hashlib import sha256
    assert result['output_sha256']==sha256(b'fixture').hexdigest()
    assert len(result['request_id'])==32
    assert (tmp_path/'proof'/'quality_process.log').is_file()


def test_real_owned_child_rejection_does_not_pass(isolated_quality_module,tmp_path):
    result=_call(isolated_quality_module,tmp_path,'reject',5)
    assert result['status']=='BLOCKED'


def test_real_owned_child_hang_is_bounded_and_error_captured(isolated_quality_module,tmp_path):
    started=time.monotonic()
    with pytest.raises(TimeoutError,match='LOCAL_QUALITY_REVIEW_TIMEOUT'):
        _call(isolated_quality_module,tmp_path,'hang',.5)
    assert time.monotonic()-started<12
    diagnostic=json.loads((tmp_path/'proof'/'quality_process_error.json').read_text(encoding='utf-8'))
    assert diagnostic['error_type']=='TimeoutError'
    assert isinstance(diagnostic.get('pid'),int)


def test_real_objective_quality_rejection_is_collected(isolated_quality_module,tmp_path):
    result=_call(isolated_quality_module,tmp_path,'blank',5)
    assert result['status']=='BLOCKED'
    assert result['objective_rejection']=='Blank generated image'
    assert result['model']=='gemma3:4b'
    assert not (tmp_path/'proof'/'quality_process_error.json').exists()


def test_real_codec_failure_still_aborts_and_keeps_diagnostics(isolated_quality_module,tmp_path):
    with pytest.raises(RuntimeError,match='LOCAL_QUALITY_REVIEW_PROCESS_FAILED'):
        _call(isolated_quality_module,tmp_path,'infra',5)
    info=json.loads((tmp_path/'proof'/'quality_process_error.json').read_text(encoding='utf-8'))
    assert info['error_type']=='RuntimeError'
    assert (tmp_path/'proof'/'quality.json').is_file()


def test_real_owned_child_repeated_folder_never_reuses_receipt(isolated_quality_module,tmp_path):
    first=_call(isolated_quality_module,tmp_path,'pass',5)
    second=_call(isolated_quality_module,tmp_path,'pass',5)
    assert first['status']==second['status']=='PASS'
    assert first['request_id']!=second['request_id']
    assert first['output_sha256']==second['output_sha256']
