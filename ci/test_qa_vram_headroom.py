"""Actual VRAM readiness is checked before the real CUDA UI workload."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
from generative_acceptance import _parse_cuda_vram,_require_cuda_headroom


def test_vram_parser_preserves_actual_free_and_used_memory():
    assert _parse_cuda_vram('458, 12288\n')=={
        'free_mib':458,'total_mib':12288,'used_mib':11830}


def test_vram_parser_rejects_invalid_or_ambiguous_driver_output():
    for sample in ('', 'N/A, 12288', '458, 12288\n2000, 8192',
                   '999999, 12288', '458, 12288, 0'):
        try:_parse_cuda_vram(sample)
        except RuntimeError:pass
        else:raise AssertionError('Accepted invalid VRAM probe: '+repr(sample))


def test_low_vram_stops_before_dispatch_without_touching_other_gpu_processes():
    with tempfile.TemporaryDirectory() as directory:
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='458, 12288\n')):
            try:_require_cuda_headroom(Path(directory),0,max_wait_seconds=0)
            except RuntimeError as error:
                assert 'QA_CUDA_INSUFFICIENT_FREE_VRAM' in str(error)
            else:raise AssertionError('Insufficient VRAM passed')
        data=json.loads((Path(directory)/'vram_case_0.json').read_text())
        assert data['status']=='BLOCKED'
        assert data['samples'][0]['free_mib']==458
        assert data['minimum_free_mib']==3072


def test_enough_vram_allows_inference_only_after_real_probe():
    with tempfile.TemporaryDirectory() as directory:
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='4100, 8192\n')):
            result=_require_cuda_headroom(Path(directory),2,max_wait_seconds=0)
        assert result['free_mib']==4100
        evidence=json.loads((Path(directory)/'vram_case_2.json').read_text())
        assert evidence['status']=='PASS'


def test_failed_nvidia_smi_never_becomes_a_pass():
    with tempfile.TemporaryDirectory() as directory:
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=1,stdout='')):
            try:_require_cuda_headroom(Path(directory),0,max_wait_seconds=0)
            except RuntimeError as error:assert 'QA_CUDA_MEMORY_PROBE_FAILED' in str(error)
            else:raise AssertionError('Failed GPU probe counted as ready')


def test_invalid_driver_output_preserves_failed_probe_evidence():
    with tempfile.TemporaryDirectory() as directory:
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='N/A, 8192',stderr='driver unavailable')):
            try:_require_cuda_headroom(Path(directory),1,max_wait_seconds=0)
            except RuntimeError:pass
            else:raise AssertionError('Invalid driver output passed')
        evidence=json.loads((Path(directory)/'vram_case_1.json').read_text())
        assert evidence['status']=='BLOCKED'
        assert evidence['last_probe']['stdout']=='N/A, 8192'
        assert evidence['samples']==[]


def test_preflight_services_qt_events_while_waiting_for_real_headroom():
    ticks=iter([0,0,0,0,0,1,6,6,6,6])
    samples=[SimpleNamespace(returncode=0,stdout='100, 8192'),SimpleNamespace(returncode=0,stdout='4096, 8192')]
    pumped=[]
    with tempfile.TemporaryDirectory() as directory:
        with patch('subprocess.run',side_effect=samples),patch('generative_acceptance.time.monotonic',side_effect=lambda:next(ticks,6)),patch('generative_acceptance.time.sleep'):
            result=_require_cuda_headroom(Path(directory),3,max_wait_seconds=90,pump=lambda:pumped.append(True))
        assert pumped and result['free_mib']==4096
