"""Model-facing source roles and host source-kind boundary agree (offline).

No Ollama/Qt/GPU or paid services. The schema must not advertise an image
as a video or a second video as an image reference. Host validation remains
authoritative if a decoder does not implement JSON Schema prefixItems.
"""
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
from generative_contract import ENGINES,source_kind
from local_media_contract import (available_local_tasks,local_media_schema,
                                  generative_source_ids_schema)


def _catalog(*,images=2,videos=2):
    return ({f'i{index}':{'kind':'image'} for index in range(images)}
            | {f'v{index}':{'kind':'video'} for index in range(videos)})


def _schema_for(task,catalog):
    caps={'local_media_tasks':[],
          'generative_profiles':{name:{'engine':engine} for name,engine in ENGINES.items()}}
    variants=local_media_schema(catalog,caps)['anyOf']
    matches=[variant['properties']['source_ids'] for variant in variants
             if isinstance(variant,dict) and variant.get('type')=='object'
             and variant['properties']['task']['enum']==[task]]
    return matches


@pytest.mark.parametrize('task,roles,minimum',[
    ('image_instruction',['image'],1),
    ('image_reference',['image','image'],2),
    ('image_inpaint',['image','image'],2),
    ('video_generative',['video','image'],1),
])
def test_model_schema_uses_the_same_ordered_roles_as_host(task,roles,minimum):
    catalog=_catalog()
    schema=generative_source_ids_schema(task,catalog)
    assert _schema_for(task,catalog)==[schema]
    assert schema['minItems']==minimum
    assert schema['maxItems']==len(roles)
    assert schema['uniqueItems'] is True
    assert len(schema['prefixItems'])==len(roles)
    for index,role in enumerate(roles):
        assert source_kind({'task':task},index)==role
        assert schema['prefixItems'][index]['enum']
        assert all(catalog[ident]['kind']==role
                   for ident in schema['prefixItems'][index]['enum'])
    assert set(schema['items']['enum'])==set().union(
        *(set(part['enum']) for part in schema['prefixItems']))


def test_video_with_no_reference_image_advertises_only_one_video():
    catalog=_catalog(images=0,videos=1)
    assert available_local_tasks(catalog,{'local_media_tasks':[],
        'generative_profiles':{'video':{'engine':ENGINES['video_generative']}}})==('video_generative',)
    schema=_schema_for('video_generative',catalog)[0]
    assert schema['minItems']==schema['maxItems']==1
    assert schema['prefixItems']==[{'type':'string','enum':['v0']}]
    assert schema['items']['enum']==['v0']


def test_image_only_catalog_cannot_offer_video_engine():
    catalog=_catalog(images=2,videos=0)
    assert _schema_for('video_generative',catalog)==[]
    assert _schema_for('image_reference',catalog)


def test_video_reference_must_not_be_another_video_or_a_reversed_pair():
    schema=_schema_for('video_generative',_catalog())[0]
    assert 'v0' in schema['prefixItems'][0]['enum']
    assert 'v1' not in schema['prefixItems'][1]['enum']
    assert 'i0' in schema['prefixItems'][1]['enum']
    assert 'i0' not in schema['prefixItems'][0]['enum']


def test_unadvertisable_source_roles_rejected():
    with pytest.raises(ValueError,match='Unadvertisable'):
        generative_source_ids_schema('image_reference',_catalog(images=0,videos=2))
    with pytest.raises(ValueError,match='Unknown'):
        generative_source_ids_schema('unsupported',_catalog())
