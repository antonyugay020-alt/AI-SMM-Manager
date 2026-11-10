"""CPU-only contract parity: model numeric limits must match the host.

Uses deterministic randomized boundaries, no Qt, Ollama, GPU, downloads or
paid services. Cross-field denoising constraints remain host-authoritative.
"""
import math
from pathlib import Path
import random
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
from generative_contract import ENGINES,generation_scalar_schema,validate_generation
from local_media_contract import local_media_schema


def _request(task):
    sources=['v0'] if task=='video_generative' else ['i0','i1'] if task in ('image_reference','image_inpaint') else ['i0']
    return {'task':task,'sources':sources,'prompt':'Edit the visual style',
            'generation':{'engine':ENGINES[task],'bundle':'/offline',
                          'manifest_sha256':'0'*64,'device':'cpu'},
            'width':256,'height':256,'steps':20,'strength':.65}


def _matches_numeric_schema(spec,value):
    if spec['type']=='integer':
        valid=type(value) is int
    else:
        valid=type(value) in (int,float) and math.isfinite(value)
    return (valid and spec['minimum']<=value<=spec['maximum']
            and (not spec.get('multipleOf') or value%spec['multipleOf']==0))


@pytest.mark.parametrize('task',list(ENGINES))
def test_host_numeric_bounds_equal_generated_schema_with_random_boundaries(task):
    request=_request(task)
    schema=generation_scalar_schema(task)
    catalog={'i0':{'kind':'image'},'i1':{'kind':'image'},'v0':{'kind':'video'}}
    caps={'local_media_tasks':[],
          'generative_profiles':{name:{'engine':engine} for name,engine in ENGINES.items()}}
    variants=local_media_schema(catalog,caps)['anyOf']
    variant=next(item for item in variants if isinstance(item,dict) and item.get('type')=='object'
                 and item['properties']['task']['enum']==[task])
    for key,spec in schema.items():
        assert variant['properties'][key]==spec
    assert set(variant['properties'])=={'task','source_ids','profile','prompt',*schema}
    rng=random.Random(87273)
    for key,spec in schema.items():
        low,high=spec['minimum'],spec['maximum']
        values=[None,True,False,float('nan'),float('inf'),-1,low-1,low,high,high+1,low+.25,high-.25]
        values += [rng.uniform(-200,5000) if spec['type']=='number' else rng.randint(-200,5000)
                   for _ in range(200)]
        for value in values:
            try:
                validate_generation({**request,key:value})
                accepted=True
            except ValueError:
                accepted=False
            assert accepted==_matches_numeric_schema(spec,value), (task,key,value)


def test_video_start_and_8gb_bounds_are_advertised_and_enforced():
    video=generation_scalar_schema('video_generative')
    assert video['start']=={'type':'number','minimum':0,'maximum':3600}
    assert video['width']['maximum']==video['height']['maximum']==384
    for value in (3600,3600.1,-.01,True,float('nan')):
        accepted=0<=value<=3600 if type(value) in (int,float) and math.isfinite(value) else False
        try:
            validate_generation({**_request('video_generative'),'start':value})
            actual=True
        except ValueError:
            actual=False
        assert actual==accepted
    assert 'start' not in generation_scalar_schema('image_instruction')


def test_schema_is_fresh_and_host_keeps_cross_field_denoising_guard():
    schema=generation_scalar_schema('image_inpaint')
    schema['width']['maximum']=99999
    assert generation_scalar_schema('image_inpaint')['width']['maximum']==512
    with pytest.raises(ValueError,match='Known generative task'):
        generation_scalar_schema('unknown')
    with pytest.raises(ValueError,match='denoising step'):
        validate_generation({**_request('image_inpaint'),'steps':2,'strength':.05})
    validate_generation({**_request('image_instruction'),'steps':2,'strength':.05})
