"""Model capabilities and bounded response-shape negotiation, independent of topic.

Thinking controls use model metadata, not one universal boolean. The built-in
GPT-OSS compatibility entry follows Ollama's documented levels; callers can
register /api/show metadata for any other model without changing workflow code.
"""
from __future__ import annotations
import copy
import hashlib
import json
from threading import RLock

_LOCK=RLock()
_METADATA={}
_PROMPT_ROUTES=set()
_COMPATIBILITY={'gpt-oss':{'thinking':{'values':['low','medium','high'],'default':'medium'}}}


def register_model_metadata(model, metadata):
    if not isinstance(model,str) or not model.strip() or not isinstance(metadata,dict):
        raise ValueError('Invalid model metadata')
    with _LOCK:
        _METADATA[model.casefold()]=copy.deepcopy(metadata)


def model_metadata(model):
    with _LOCK:
        return copy.deepcopy(_METADATA.get(str(model).casefold(),
            _COMPATIBILITY.get(str(model).casefold().split(':',1)[0],{})))


def schema_output_tokens(schema, depth=0):
    """Conservative bounded allowance for a typed answer, not a text template."""
    if not isinstance(schema,dict) or depth>12:
        return 32
    if isinstance(schema.get('enum'),list):
        return max((len(json.dumps(x,ensure_ascii=False))//3+4 for x in schema['enum']),default=4)
    kind=schema.get('type')
    if kind=='object':
        return 4+sum(len(str(key))//3+4+schema_output_tokens(value,depth+1)
                     for key,value in schema.get('properties',{}).items())
    if kind=='array':
        count=schema.get('maxItems',4)
        count=count if type(count) is int else 4
        return 4+min(max(count,1),16)*(2+schema_output_tokens(schema.get('items',{}),depth+1))
    if kind=='string':
        length=schema.get('maxLength',120)
        return min(max(length if type(length) is int else 120,1),600)//3+4
    return 8


def prepare_model_payload(payload):
    """Preserve input and deadlines; account for mandatory reasoning separately."""
    result=dict(payload)
    metadata=model_metadata(payload.get('model',''))
    thinking=metadata.get('thinking',{})
    values=thinking.get('values',[]) if isinstance(thinking,dict) else []
    values=[x for x in values if type(x) is bool or isinstance(x,str)] if isinstance(values,list) else []
    requested=payload.get('think')
    can_disable=any(type(x) is bool and x is False for x in values)
    mandatory=bool(values) and not can_disable
    if requested is False and mandatory:
        if 'low' in values:
            result['think']='low'
        else:
            # Do not invent unsupported levels; let the model's documented
            # default apply when the requested off control is unavailable.
            result.pop('think',None)
    if mandatory:
        options=dict(payload.get('options') or {})
        limit=options.get('num_predict')
        if type(limit) is int and limit>0:
            reserve=metadata.get('reasoning_reserve_tokens',768)
            reserve=reserve if type(reserve) is int and 0<=reserve<=4096 else 768
            allowance=min(4096,schema_output_tokens(payload.get('format'))+reserve)
            options['num_predict']=max(limit,allowance)
            result['options']=options
    return result


def contract_key(model, schema):
    if not isinstance(schema,dict):
        shape='json_object'
    else:
        # Root protocol fields identify a contract, never a topic or a prompt.
        shape=json.dumps({'type':schema.get('type'),
            'fields':sorted(schema.get('properties',{}))},sort_keys=True)
    return str(model).casefold(),hashlib.sha256(shape.encode()).hexdigest()[:16]


def uses_prompt_route(model, schema):
    with _LOCK:
        return contract_key(model,schema) in _PROMPT_ROUTES


def note_incompatible_tool_result(model, schema, response):
    """Retry a *transport* mismatch using prompt JSON, never accept its payload.

    Only a finished response from our own synthetic return function is eligible.
    Schema validation is still mandatory on every later response; malformed
    arguments here are never interpreted, repaired, or returned to the caller.
    """
    message = response.get('message') if isinstance(response, dict) else None
    calls = message.get('tool_calls') if isinstance(message, dict) else None
    if response.get('done_reason') != 'stop' or not isinstance(calls, list) or len(calls) != 1:
        return False
    function = calls[0].get('function') if isinstance(calls[0], dict) else None
    if not isinstance(function, dict) or function.get('name') != 'return_structured_result':
        return False
    with _LOCK:
        _PROMPT_ROUTES.add(contract_key(model, schema))
    return True


def note_empty_tool_result(model, schema, response):
    """Only a completed empty synthetic-tool response negotiates a new route.

    Truncation, timeouts, malformed values and semantic rejection cannot change
    route or become approval. The caller's existing retry budget is unchanged.
    """
    message=response.get('message') if isinstance(response,dict) else None
    empty=(isinstance(message,dict) and not message.get('content')
           and not message.get('tool_calls') and response.get('done_reason')=='stop')
    if empty:
        with _LOCK:
            _PROMPT_ROUTES.add(contract_key(model,schema))
    return bool(empty)
