"""QA CUDA subprocess failure telemetry must survive automatic workspace cleanup."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'AI_SMM_Work_Architecture_A75'))
from local_media_jobs import _retain_generation_failure


def test_failed_cuda_stage_logs_survive_in_exception_diagnostic():
    with tempfile.TemporaryDirectory() as directory:
        folder=Path(directory)
        (folder/'generation_process.json').write_text(
            json.dumps({'pid':123,'parent_pid':456,'started_at':12.5}),encoding='utf-8')
        (folder/'generation_process.log').write_text(
            '{"stage":"runner_started"}\n{"stage":"loading_pipeline"}\n',encoding='utf-8')
        error=RuntimeError('child interrupted')
        evidence=_retain_generation_failure(folder,error)
        assert error.diagnostic==evidence
        assert evidence['child']['pid']==123
        assert 'loading_pipeline' in evidence['stage_log_tail']
        assert evidence['stage_log_bytes']>0
    # The temp directory is now gone but durable job-error evidence is still present.
    assert 'runner_started' in error.diagnostic['stage_log_tail']


def test_no_cuda_log_does_not_convert_other_exception_into_diagnostic():
    with tempfile.TemporaryDirectory() as directory:
        error=RuntimeError('ordinary job failure')
        assert _retain_generation_failure(Path(directory),error)=={}
        assert not hasattr(error,'diagnostic')


def test_cuda_log_tail_is_bounded_and_existing_diagnostic_is_preserved():
    with tempfile.TemporaryDirectory() as directory:
        folder=Path(directory)
        (folder/'generation_process.log').write_bytes(
            b'a'*30000+b'{"stage":"inference_started"}\n')
        class GenerationError(RuntimeError):
            diagnostic={'error':'CUDA_OR_INTERRUPT'}
        error=GenerationError()
        record=_retain_generation_failure(folder,error)
        assert record['error']=='CUDA_OR_INTERRUPT'
        assert record['stage_log_bytes']>30000
        assert len(record['stage_log_tail'])<=16384
        assert 'inference_started' in record['stage_log_tail']
