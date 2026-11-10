"""Single stable Windows QA archive: preserve history and always select last run.

These tests create synthetic reports in temporary directories. They never start
a GUI, use GPU/Ollama, touch the user's Desktop, or invoke paid APIs.
"""
from pathlib import Path
import sys
from zipfile import ZipFile,ZIP_DEFLATED

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from windows_qa_session import publish_latest_diagnostics


def test_latest_successful_report_has_one_stable_path(tmp_path):
    root=tmp_path/'AI_SMM_REPORTS';root.mkdir()
    session=root/'old_unique_session';report=session/'run_01'/'report'
    report.mkdir(parents=True)
    with ZipFile(report/'DIAGNOSTICS.zip','w',ZIP_DEFLATED) as archive:
        archive.writestr('VALIDATION_RESULTS/acceptance.json','{"status":"BLOCKED"}')
    expected=(report/'DIAGNOSTICS.zip').read_bytes()
    (session/'STATUS.json').write_text('{"status":"BLOCKED"}',encoding='utf-8')
    assert publish_latest_diagnostics(session,root)==root/'DIAGNOSTICS.zip'
    with ZipFile(root/'DIAGNOSTICS.zip') as final:
        assert final.read('VALIDATION_RESULTS/acceptance.json')==b'{"status":"BLOCKED"}'
        assert final.read('QA_SESSION/STATUS.json')==b'{"status":"BLOCKED"}'
    assert (report/'DIAGNOSTICS.zip').read_bytes()==expected


def test_bootstrap_failure_still_yields_one_archive(tmp_path):
    root=tmp_path/'AI_SMM_REPORTS';root.mkdir()
    session=root/'early_failure';session.mkdir()
    (session/'STATUS.json').write_text('{"status":"BLOCKED"}',encoding='utf-8')
    (session/'runner_error.json').write_text('{"error":"CUDA_NOT_AVAILABLE"}',encoding='utf-8')
    latest=publish_latest_diagnostics(session,root)
    with ZipFile(latest) as archive:
        assert archive.read('QA_SESSION/STATUS.json')==b'{"status":"BLOCKED"}'
        assert b'CUDA_NOT_AVAILABLE' in archive.read('QA_SESSION/runner_error.json')


def test_failed_latest_run_never_returns_earlier_success(tmp_path):
    root=tmp_path/'AI_SMM_REPORTS';root.mkdir()
    session=root/'qa_session';first=session/'run_01'/'report'
    first.mkdir(parents=True)
    with ZipFile(first/'DIAGNOSTICS.zip','w',ZIP_DEFLATED) as archive:
        archive.writestr('VALIDATION_RESULTS/acceptance.json','{"status":"PASS"}')
    final=session/'run_02';final.mkdir(parents=True)
    (final/'console.log').write_text('FAILED_CHILD',encoding='utf-8')
    (session/'STATUS.json').write_text('{"status":"BLOCKED"}',encoding='utf-8')
    latest=publish_latest_diagnostics(session,root)
    with ZipFile(latest) as archive:
        assert archive.read('QA_SESSION/latest_run/console.log')==b'FAILED_CHILD'
        assert archive.read('QA_SESSION/STATUS.json')==b'{"status":"BLOCKED"}'
        assert 'VALIDATION_RESULTS/acceptance.json' not in archive.namelist()


def test_symlink_latest_file_is_refused(tmp_path):
    root=tmp_path/'AI_SMM_REPORTS';root.mkdir()
    session=root/'qa_session';session.mkdir()
    external=tmp_path/'not_our_file.zip';external.write_bytes(b'USER_FILE')
    try:(root/'DIAGNOSTICS.zip').symlink_to(external)
    except (OSError,NotImplementedError):
        import pytest
        pytest.skip('Host forbids symlinks')
    import pytest
    with pytest.raises(RuntimeError,match='linked latest'):
        publish_latest_diagnostics(session,root)
    assert external.read_bytes()==b'USER_FILE'


def test_batch_launcher_fallback_writes_the_same_single_zip():
    command=(ROOT/'RUN_WINDOWS_QA.cmd').read_text(encoding='utf-8')
    assert 'Compress-Archive' in command
    assert "%QA_REPORTS%\\DIAGNOSTICS.zip" in command
    assert "BOOTSTRAP_CONSOLE.txt" in command


def test_supervisor_blocked_overrides_child_pass(tmp_path):
    root=tmp_path/'AI_SMM_REPORTS';root.mkdir()
    session=root/'latest';report=session/'run_01'/'report';report.mkdir(parents=True)
    with ZipFile(report/'DIAGNOSTICS.zip','w',ZIP_DEFLATED) as child:
        child.writestr('VALIDATION_RESULTS/acceptance.json','{"status":"PASS"}')
    (session/'STATUS.json').write_text('{"status":"BLOCKED","reason":"SOURCE_CHANGED"}')
    with ZipFile(publish_latest_diagnostics(session,root)) as final:
        assert final.read('VALIDATION_RESULTS/acceptance.json')==b'{"status":"PASS"}'
        assert b'"status":"BLOCKED"' in final.read('QA_SESSION/STATUS.json')


def test_archive_does_not_extract_or_copy_unsafe_members(tmp_path):
    root=tmp_path/'AI_SMM_REPORTS';root.mkdir()
    session=root/'latest';report=session/'run_01'/'report';report.mkdir(parents=True)
    with ZipFile(report/'DIAGNOSTICS.zip','w',ZIP_DEFLATED) as child:
        child.writestr('../bad.json','invalid')
    import pytest
    with pytest.raises(ValueError,match='Unsafe'):
        publish_latest_diagnostics(session,root)
    assert not (root/'DIAGNOSTICS.zip').exists()
