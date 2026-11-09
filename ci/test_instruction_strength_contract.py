"""Unified media-edit strength contract; pure CPU checks, no models.

No fixed screenshot verdict or relaxed quality gate.
"""
from pathlib import Path
import math
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
from generative_contract import instruction_image_guidance,validate_generation,ENGINES


def _request(task,strength,steps=20):
    n=2 if task in ('image_reference','image_inpaint') else 1
    return {
        'task':task,
        'sources':[f'source{i}' for i in range(n)],
        'prompt':'Make the requested style change',
        'generation':{'engine':ENGINES[task],'bundle':'/offline',
                      'manifest_sha256':'0'*64,'device':'cpu'},
        'width':256,'height':256,'steps':steps,'strength':strength
    }


@pytest.mark.parametrize('strength,expected',[
    (.05,1.95),(.2,1.8),(.65,1.35),(.85,1.15),(1.0,1.0)
])
def test_pix2pix_edit_strength_monotone_and_bounded(strength,expected):
    assert instruction_image_guidance(strength)==pytest.approx(expected)


def test_pix2pix_strength_not_misvalidated_as_denoising_steps():
    validate_generation(_request('image_instruction',.05,steps=2))
    with pytest.raises(ValueError,match='denoising step'):
        validate_generation(_request('image_inpaint',.05,steps=2))


def test_invalid_strength_never_reaches_model():
    for strength in [float('nan'),float('inf'),-.1,0,1.01,True,'0.65']:
        with pytest.raises(ValueError,match='strength'):
            instruction_image_guidance(strength)


def test_ui_and_runner_use_the_one_contract():
    from pathlib import Path
    ui=(ROOT/'AI_SMM_Work_Architecture_A75/kie_media_queue_ui.py').read_text('utf-8')
    runner=(ROOT/'AI_SMM_Work_Architecture_A75/generative_runner.py').read_text('utf-8')
    assert "'strength':self.local_strength.value()" in ui
    assert 'instruction_image_guidance(request.get(' in runner
    assert "instruction_image_guidance_scale':kwargs.get('image_guidance_scale')" in runner
