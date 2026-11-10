"""Offline regression of text transport finality and schema-route isolation."""
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
from model_completion_contract import require_completed


@pytest.mark.parametrize('response',[
    {'done':False,'done_reason':'stop','message':{'content':'partial'}},
    {'done':True,'done_reason':'length','message':{'content':'partial'}},
    {'done':True,'done_reason':'unload','message':{'content':'partial'}},
])
def test_ollama_incomplete_never_accepted(response):
    with pytest.raises(ValueError,match='incomplete'):
        require_completed('ollama',response)


@pytest.mark.parametrize('reason',['length','content_filter','tool_calls',None])
def test_chat_completion_requires_stop(reason):
    with pytest.raises(ValueError,match='incomplete'):
        require_completed('chat_completions',{'choices':[{'finish_reason':reason,
            'message':{'content':'plausible but incomplete'}}]})


@pytest.mark.parametrize('status',['in_progress','requires_action','failed',None])
def test_gemini_requires_completed(status):
    with pytest.raises(ValueError,match='complete'):
        require_completed('gemini_interactions',{'status':status,'output_text':'partial'})


class Response(io.BytesIO):
    def __init__(self,data):super().__init__(json.dumps(data).encode('utf-8'))


def test_real_cloud_adapter_rejects_incomplete_response_without_network():
    from text_providers import _chat_request
    cases=[
        ('groq','openai/gpt-oss-20b',{'choices':[{'finish_reason':'length','message':{'content':'partial'}}]}),
        ('openrouter','vendor/model:free',{'choices':[{'finish_reason':'content_filter','message':{'content':'partial'}}]}),
        ('openai','fixture',{'choices':[{'finish_reason':'tool_calls','message':{'content':'partial'}}]}),
        ('gemini','gemini-3.1-flash-lite',{'status':'in_progress','output_text':'partial'}),
    ]
    for provider,model,body in cases:
        with (patch('text_providers.urllib.request.urlopen',return_value=Response(body)),
              patch('diagnostic_report.record_model_event')):
            with pytest.raises(ValueError):
                _chat_request(provider,model,[{'role':'user','content':'fixture'}],5,'fixture-key',.2)


def test_real_cloud_adapter_accepts_only_explicit_completion():
    from text_providers import _chat_request
    cases=[
        ('groq','openai/gpt-oss-20b',{'choices':[{'finish_reason':'stop','message':{'content':'complete'}}]}),
        ('gemini','gemini-3.1-flash-lite',{'status':'completed','output_text':'complete'}),
    ]
    for provider,model,body in cases:
        with (patch('text_providers.urllib.request.urlopen',return_value=Response(body)),
              patch('diagnostic_report.record_model_event')):
            text,usage=_chat_request(provider,model,[{'role':'user','content':'fixture'}],5,'fixture-key',.2)
        assert text=='complete' and isinstance(usage,dict)


def test_nested_schema_routes_are_isolated_and_bounded():
    from model_protocol import (contract_key,uses_prompt_route,note_empty_tool_result,
        note_incompatible_tool_result,_PROMPT_ROUTES,_MAX_PROMPT_ROUTES)
    _PROMPT_ROUTES.clear()
    model='fixture-model'
    base={'type':'object','properties':{'value':{'type':'integer','minimum':0}}}
    sibling={'type':'object','properties':{'value':{'type':'integer','minimum':1}}}
    reordered={'properties':{'value':{'minimum':0,'type':'integer'}},'type':'object'}
    assert contract_key(model,base)==contract_key(model.upper(),reordered)
    assert contract_key(model,base)!=contract_key(model,sibling)
    assert note_empty_tool_result(model,base,{'done_reason':'stop','message':{'content':''}})
    assert uses_prompt_route(model,base) and not uses_prompt_route(model,sibling)
    assert not note_empty_tool_result(model,sibling,None)
    assert not note_incompatible_tool_result(model,sibling,None)
    for i in range(_MAX_PROMPT_ROUTES+4):
        schema={'type':'object','properties':{'value':{'type':'integer','minimum':i+10}}}
        assert note_empty_tool_result(model,schema,{'done_reason':'stop','message':{'content':''}})
    assert len(_PROMPT_ROUTES)<=_MAX_PROMPT_ROUTES
    assert not uses_prompt_route(model,base)
    _PROMPT_ROUTES.clear()
