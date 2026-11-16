"""A short ownership diagnostic never certifies full Windows/Qt/CUDA QA."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import windows_qa_session as session


@pytest.mark.parametrize('exit_code',[0,2])
def test_diagnostic_excludes_models_and_cannot_advance_acceptance(tmp_path,monkeypatch,exit_code):
    monkeypatch.setattr(session,'os',SimpleNamespace(name='nt',environ=os.environ,replace=os.replace))
    calls=[]
    def child(command,env,folder,timeout):
        calls.append(command)
        assert timeout==180
        assert json.loads(env['AI_SMM_QA_ALLOWED_MODELS'])==[]
        assert env['HF_HUB_OFFLINE']=='1'
        return exit_code
    monkeypatch.setattr(session,'run_child',child)
    state={'target_clean_consecutive':9,'clean_consecutive':9,'release_eligible':True}
    assert session.execute_process_diagnostic(tmp_path,state)==exit_code
    assert state['status']==('DIAGNOSTIC_COMPLETE' if exit_code==0 else 'BLOCKED')
    assert state['acceptance_run'] is False
    assert state['target_clean_consecutive']==state['clean_consecutive']==0
    assert state['release_eligible'] is False
    assert 'windows_process_diagnostic.py' in calls[0][1]
    with ZipFile(session.publish_latest_diagnostics(tmp_path,tmp_path)) as z:
        assert 'VALIDATION_RESULTS/process_diagnostic.json' in z.namelist()


def test_short_mode_skips_model_configuration_but_archive_failure_is_fatal(tmp_path,monkeypatch):
    monkeypatch.setattr(sys,'argv',['qa','--role','preliminary_3080ti','--output',str(tmp_path),'--process-diagnostic'])
    monkeypatch.setattr(session,'source_snapshot',lambda root:{'files':{},'fingerprint':'fixture'})
    monkeypatch.setattr(session,'bootstrap_inventory',lambda folder:None)
    monkeypatch.setattr(session,'require_qa_environment',lambda folder:None)
    def diagnostic(folder,state):
        state.update(status='DIAGNOSTIC_COMPLETE',acceptance_run=False)
        return 0
    monkeypatch.setattr(session,'execute_process_diagnostic',diagnostic)
    def forbidden(*args):raise AssertionError('Model configuration must not be read')
    monkeypatch.setattr(session,'read_config',forbidden)
    monkeypatch.setattr(session,'wizard',forbidden)
    def broken_archive(*args):raise OSError('fixture archive failure')
    monkeypatch.setattr(session,'publish_latest_diagnostics',broken_archive)
    assert session.main()==2
    state=json.loads((tmp_path/'STATUS.json').read_text())
    assert state['status']=='BLOCKED' and state['target_clean_consecutive']==0
