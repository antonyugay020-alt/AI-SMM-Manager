"""Offline evidence contracts: excluded Ollama tags and native PyTorch-import stacks.

No real GPU, Ollama service or paid API is invoked by these checks.
"""
import ast
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from windows_qa_session import model_roster_evidence


def test_explicit_local_roster_explains_missing_qwen14b():
    items=[{'name':'qwen2.5:14b'},{'name':'deepseek-r1:8b'},{'name':'gemma4:cloud'}]
    outcome=model_roster_evidence(items,['deepseek-r1:8b'],['deepseek-r1:8b'])
    assert outcome['explicit_local_models_selection'] is True
    assert outcome['admitted_local_completion_tags']==['deepseek-r1:8b']
    assert {'name':'qwen2.5:14b','reason':'not_selected_in_settings'} in outcome['excluded_tags']
    assert all('credential' not in item for item in outcome)


def test_implicit_roster_is_audit_only_and_does_not_admit_unqualified_tags():
    items=[{'name':'qwen2.5:14b'},{'name':'embed:latest'},{'name':'gemma4:cloud'},{}]
    outcome=model_roster_evidence(items,['qwen2.5:14b'],None)
    assert outcome['explicit_local_models_selection'] is False
    assert outcome['discovered_tags']==['qwen2.5:14b','embed:latest','gemma4:cloud']
    assert outcome['admitted_local_completion_tags']==['qwen2.5:14b']
    assert all(row['reason']=='not_eligible_for_local_completion' for row in outcome['excluded_tags'])


def test_windows_qa_packages_roster_in_actual_diagnostic_report():
    source=(ROOT/'windows_qa_session.py').read_text(encoding='utf-8')
    assert "write_json(session/'qa_model_roster.json',qa_roster)" in source
    assert "write_json(qa_roster_folder/'qa_model_roster.json',qa_roster)" in source
    assert "model_roster_evidence(inventory,allowed,requested)" in source


def test_torch_import_traces_are_armed_and_cancelled_with_progress_marker():
    source=(ROOT/'AI_SMM_Work_Architecture_A75'/'generative_runner.py').read_text(encoding='utf-8')
    tree=ast.parse(source)
    run=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run')
    calls=[n for n in ast.walk(run) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)]
    funcs=[n.func.attr for n in calls]
    assert 'dump_traceback_later' in funcs
    assert 'cancel_dump_traceback_later' in funcs
    assert '_stage(\'torch_imported\')' in source
