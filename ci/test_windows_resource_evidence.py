"""Preserve failed memory/admission evidence without relaxing any gate."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
import generative_runner as runner
from ollama_resilience import StructuredResponseError
from review_qualification import probe_qualification


@pytest.mark.parametrize('task,gib',[('image_instruction',6),('video_generative',10)])
def test_memory_rejection_keeps_exact_measurements_and_floor(task,gib,monkeypatch,capsys):
    import psutil
    required=gib*2**30
    monkeypatch.setattr(psutil,'virtual_memory',lambda:SimpleNamespace(available=required-1,total=32*2**30))
    with pytest.raises(RuntimeError,match='^SYSTEM_RAM_INSUFFICIENT$'):
        runner.check_system_memory(task)
    evidence=json.loads(capsys.readouterr().out)
    assert evidence['minimum_bytes']==required
    assert evidence['available_bytes']==required-1
    assert evidence['total_bytes']==32*2**30
    monkeypatch.setattr(psutil,'virtual_memory',lambda:SimpleNamespace(available=required,total=32*2**30))
    runner.check_system_memory(task)
    assert json.loads(capsys.readouterr().out)['available_bytes']==required


def test_incomplete_reviewer_response_retains_reason_without_retry():
    failure=StructuredResponseError('qualification','incomplete response')
    failure.response_diagnostics={'done':True,'done_reason':'length','content_chars':100,'thinking_chars':20}
    send=Mock(side_effect=failure)
    report=probe_qualification('fixture-local:8b',seed=123,transport=send,force=True)
    assert report['pass'] is False and report['contract_pass'] is False
    assert report['model_calls']==1 and send.call_count==1
    assert report['response_diagnostics']==failure.response_diagnostics
    assert report['raw_reply']==''
    assert len(report['controls'])==6 and all(not row['pass'] for row in report['controls'])
