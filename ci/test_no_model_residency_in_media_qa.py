"""The QA media gateway must not cold-load every local Ollama model.

The selected vision model performs its own qualified zero-residency requests;
other models are already independently qualified in the text workflow.
"""
import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'AI_SMM_Work_Architecture_A75'/'windows_acceptance.py'
QUALITY=ROOT/'AI_SMM_Work_Architecture_A75'/'local_media_quality.py'


def test_media_qa_does_not_warm_all_local_llms_before_diffusers():
    tree=ast.parse(SOURCE.read_text(encoding='utf-8'))
    calls=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
            if node.func.attr=='call' and node.args and isinstance(node.args[0],ast.Constant):
                calls.append(node.args[0].value)
    assert '/api/generate' not in calls
    assert "vision.qualify(OUT/'vision_qualification')" in SOURCE.read_text(encoding='utf-8')


def test_actual_vision_checks_release_the_model_and_do_not_use_cloud():
    code=QUALITY.read_text(encoding='utf-8')
    assert "'keep_alive':0" in code
    assert "LocalVision" in code
    assert "'/api/chat'" in code
