"""Exact stage evidence is observable, and timeout budgets are non-fabricated."""
import json
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
from generative_acceptance import _observed_generation_stage
from generative_media_runtime import _stage_deadline,_last_child_stage


def test_stage_timeout_distinguishes_cuda_phases_and_hardware_workload():
    assert _stage_deadline(None,False)==180
    assert _stage_deadline('loading_pipeline',False)==1200
    assert _stage_deadline('inference_started',False)==1500
    assert _stage_deadline('inference_started',True)==3600
    assert _stage_deadline('waiting_for_local_device',True)==240
    assert _stage_deadline('totally_unknown',False)==180


def test_log_tail_reads_last_real_child_stage_not_arbitrary_text():
    with tempfile.TemporaryDirectory() as directory:
        log=Path(directory)/'generation_process.log'
        assert _last_child_stage(log) is None
        log.write_bytes(b'invalid JSON\n'+
            json.dumps({'stage':'loading_pipeline'}).encode()+b'\n'+
            json.dumps({'stage':'inference_started'}).encode()+b'\n')
        assert _last_child_stage(log)=='inference_started'
        with log.open('ab') as stream:
            stream.write(b'{"error_code":"CUDA_OUT_OF_MEMORY"}\n')
        assert _last_child_stage(log)=='inference_started'


def test_active_case_uses_real_log_in_qa_project_and_no_fake_progress():
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        assert _observed_generation_stage(root) is None
        work=root/'project_id'/'workspace'/'.local_media_work'/'job';work.mkdir(parents=True)
        log=work/'generation_process.log';log.write_text('',encoding='utf-8')
        assert _observed_generation_stage(root) is None
        log.write_text('{"stage":"runner_started"}\n',encoding='utf-8')
        assert _observed_generation_stage(root)=='runner_started'
        with log.open('a',encoding='utf-8') as stream:
            stream.write('{"stage":"inference_started"}\n')
        assert _observed_generation_stage(root)=='inference_started'
