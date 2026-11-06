"""Regression contracts based on the first genuine full Windows acceptance failure.

These are only code-level guards; they do not certify Windows E2E success.
"""
import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def _source(module):
    return (ROOT/(module+'.py')).read_text(encoding='utf-8')

def test_staging_flush_uses_writable_windows_handle():
    src=_source('artifact_staging')
    assert "with staged.open('r+b') as handle:" in src
    assert "os.fsync(handle.fileno())" in src
    assert "with staged.open('rb') as handle:" not in src

def test_backup_flush_uses_writable_windows_handle():
    src=_source('project_control_runtime')
    assert "with path.open('r+b') as handle:" in src
    assert "os.fsync(handle.fileno())" in src

def test_gui_timeout_reports_diagnostics_before_and_during_processing():
    src=_source('live_gui_workflow_acceptance')
    assert "workspace/'gui_workflow_diagnostics.json'" in src
    assert 'last_checkpoint=begin' in src
    assert 'save_gui_diagnostics(workspace,app,pid,worker_events,task_seed,profile,brief)' in src

def test_parent_collects_diagnostics_even_after_child_timeout():
    source=_source('windows_acceptance')
    tree=ast.parse(source)
    child=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='child_run')
    handlers=[node for node in ast.walk(child) if isinstance(node,ast.ExceptHandler)]
    timeout=next(h for h in handlers if isinstance(h.type,ast.Attribute) and h.type.attr=='TimeoutExpired')
    text=ast.get_source_segment(source,timeout)
    assert "name=='live_gui_full_workflow'" in text
    assert 'shutil.copy2(evidence,OUT/evidence.name)' in text
