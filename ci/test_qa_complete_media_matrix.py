"""Fail-closed multi-engine acceptance: quality rejections collect, never pass.

No Qt/GPU/Ollama execution. Actual native behavior stays a Windows gate.
"""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
from generative_acceptance import _media_case_status


def test_a_single_rejected_style_never_certifies_a_batch():
    cases=[{'case':0,'status':'BLOCKED_QUALITY'},
           {'case':1,'status':'PASS'},
           {'case':2,'status':'PASS'},
           {'case':3,'status':'PASS'}]
    assert _media_case_status(cases,4,review=False,quality_callback=lambda *a:None)=='BLOCKED'


def test_unexecuted_engine_never_certifies_a_batch():
    cases=[{'case':0,'status':'PASS'},{'case':1,'status':'PASS'}]
    assert _media_case_status(cases,4,review=False,quality_callback=lambda *a:None)=='BLOCKED'


def test_successful_complete_batch_requires_actual_quality_or_visual_check():
    cases=[{'case':i,'status':'PASS'} for i in range(4)]
    assert _media_case_status(cases,4,review=False,quality_callback=lambda *a:None)=='PASS'
    assert _media_case_status(cases,4,review=True,quality_callback=None)=='PASS'
    assert _media_case_status(cases,4,review=False,quality_callback=None)=='TECHNICAL_PASS_PENDING_VISUAL_REVIEW'


def test_local_quality_rejection_continues_only_after_worker_completion():
    source=(ROOT/'AI_SMM_Work_Architecture_A75/generative_acceptance.py').read_text(encoding='utf-8')
    assert "and panel._result_worker is None)" in source
    assert "quality_failures.append(blocked)" in source
    assert "'status':'BLOCKED_QUALITY'" in source
    assert "if quality_rejected:" in source
    assert "return 0 if report.get('status') in" in source
