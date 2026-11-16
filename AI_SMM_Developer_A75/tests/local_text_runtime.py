"""Opt-in loopback llama.cpp / LM Studio transport for local model execution.

No cloud credentials, redirects or remote base URLs. API contract:
https://github.com/ggml-org/llama.cpp/blob/b12202/tools/server/README.md
Ollama remains the default transport when this explicit backend is not selected.
"""
import ipaddress
import json
import math
import os
import time
from http_response_budget import read_bounded_body
from urllib import request,error,parse
from cooperative_cancellation import check_cancelled
from provider_errors import ProviderHTTPError

MAX_RESPONSE=16*1024*1024


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise ValueError('Local model redirects are not allowed')


def configured_for(model,*,environ=None):
    env=os.environ if environ is None else environ
    return (env.get('AI_SMM_LOCAL_LLM_BACKEND') in ('llama_cpp','lm_studio')
            and isinstance(model,str) and bool(model.strip())
            and model==env.get('AI_SMM_LOCAL_LLM_MODEL'))


def configured_url(env):
    return env.get('AI_SMM_LOCAL_LLM_URL',
        'http://127.0.0.1:1234' if env.get('AI_SMM_LOCAL_LLM_BACKEND')=='lm_studio' else 'http://127.0.0.1:8080')


def configuration_identity(model,*,environ=None):
    """Bind reviewer admission to the selected route, including QA weight hash."""
    env=os.environ if environ is None else environ
    if not configured_for(model,environ=env):return None
    return {'backend':env['AI_SMM_LOCAL_LLM_BACKEND'],'model':model,
            'endpoint':endpoint_for(configured_url(env)),
            'weights_sha256':env.get('AI_SMM_LOCAL_LLM_SHA256','')}


def lm_studio_model_id(model):
    if not isinstance(model,str) or not model.startswith('lmstudio/'):
        raise ValueError('LM Studio model must use lmstudio/<exact server id>')
    value=model[len('lmstudio/'):]
    if not value or value.strip()!=value or any(ord(c)<32 for c in value):
        raise ValueError('Invalid LM Studio model id')
    return value


def lm_studio_inventory(url,*,opener=None,timeout=12):
    """Read-only discovery; v1/models alone includes unloaded/JIT candidates."""
    base=endpoint_for(url)
    url=base.removesuffix('/v1/chat/completions')+'/api/v0/models'
    http=opener or request.build_opener(request.ProxyHandler({}),_NoRedirect()).open
    check_cancelled();started=time.monotonic()
    try:
        with http(request.Request(url,method='GET'),timeout=timeout) as response:
            if getattr(response,'status',200)!=200:raise ValueError('LM Studio inventory unavailable')
            raw=read_bounded_body(response,max_bytes=1024*1024,deadline=started+timeout,check=check_cancelled)
    except error.HTTPError as exc:
        code=exc.code;exc.close();raise ProviderHTTPError('local lm_studio inventory',code) from None
    data=json.loads(raw.decode('utf-8',errors='strict'))
    entries=data.get('data') if isinstance(data,dict) else None
    if not isinstance(entries,list):raise ValueError('Invalid LM Studio model inventory')
    return entries


def loaded_model(*,environ=None,opener=None,timeout=12):
    env=os.environ if environ is None else environ
    model=env.get('AI_SMM_LOCAL_LLM_MODEL')
    if env.get('AI_SMM_LOCAL_LLM_BACKEND')!='lm_studio' or not configured_for(model,environ=env):
        raise ValueError('Explicit LM Studio configuration required')
    identity=lm_studio_model_id(model)
    entries=lm_studio_inventory(configured_url(env),opener=opener,timeout=timeout)
    matches=[item for item in entries if isinstance(item,dict) and item.get('id')==identity]
    if len(matches)!=1 or matches[0].get('type')!='llm' or matches[0].get('state')!='loaded':
        raise ValueError('Selected LM Studio text model must already be loaded; no load/download attempted')
    item=matches[0]
    if any(item.get(k) for k in ('remote_host','remote_model')):
        raise ValueError('Remote model metadata is forbidden for local text')
    return {key:item.get(key) for key in ('id','type','publisher','arch','compatibility_type','quantization','max_context_length')}


def configured_models(*,environ=None):
    env=os.environ if environ is None else environ
    if env.get('AI_SMM_LOCAL_LLM_BACKEND')!='lm_studio':return []
    model=env.get('AI_SMM_LOCAL_LLM_MODEL')
    if env.get('AI_SMM_QA_LOCAL_ONLY')=='1' and model not in json.loads(env.get('AI_SMM_QA_ALLOWED_MODELS','[]')):
        raise ValueError('LM Studio model is outside the QA allowlist')
    loaded_model(environ=env)
    return [model]


def endpoint_for(value):
    if not isinstance(value,str):raise ValueError('Local model URL required')
    parts=parse.urlsplit(value)
    if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('Local model must use an uncredentialed loopback URL')
    host=parts.hostname
    try:local=ipaddress.ip_address(host).is_loopback
    except ValueError:local=host=='localhost'
    if not local or parts.path.rstrip('/') not in ('','/v1'):
        raise ValueError('Local model endpoint must be on loopback')
    if parts.port is None or not 1<=parts.port<=65535:raise ValueError('Explicit local model port required')
    origin=parse.urlunsplit((parts.scheme,parts.netloc,'','',''))
    return origin+'/v1/chat/completions'


def request_local(payload,*,timeout=180,environ=None,opener=None,evidence=None):
    env=os.environ if environ is None else environ
    model=payload.get('model') if isinstance(payload,dict) else None
    if not configured_for(model,environ=env):raise RuntimeError('Local backend not selected for this model')
    backend=env['AI_SMM_LOCAL_LLM_BACKEND']
    if type(timeout) not in (int,float) or not math.isfinite(timeout) or timeout<=0:
        raise ValueError('Finite positive local request timeout required')
    if backend=='lm_studio':
        started=time.monotonic()
        loaded_model(environ=env,opener=opener,timeout=min(timeout,12))
        timeout-=time.monotonic()-started
        if timeout<=0:raise TimeoutError('Local inventory exhausted call deadline')
    check_cancelled()
    endpoint=endpoint_for(configured_url(env))
    messages=payload.get('messages')
    if not isinstance(messages,list) or not messages:raise ValueError('Local chat messages required')
    options=payload.get('options') or {}
    if not isinstance(options,dict):raise ValueError('Invalid local model options')
    temperature=options.get('temperature',.2)
    if type(temperature) not in (int,float) or not math.isfinite(temperature) or not 0<=temperature<=2:
        raise ValueError('Invalid local model temperature')
    maximum=options.get('num_predict',2048)
    if type(maximum) is not int or not 1<=maximum<=32768:raise ValueError('Local output token budget invalid')
    body={'model':lm_studio_model_id(model) if backend=='lm_studio' else model,
          'messages':messages,'temperature':temperature,'max_tokens':maximum,'stream':False}
    if backend=='llama_cpp':body['chat_template_kwargs']={'enable_thinking':False}
    contract=payload.get('format')
    if isinstance(contract,dict):
        body['response_format']=({'type':'json_schema','json_schema':{'name':'ai_smm_response','strict':True,'schema':contract}}
            if backend=='lm_studio' else {'type':'json_object','schema':contract})
    elif contract=='json':body['response_format']={'type':'json_object'}
    elif contract is not None:raise ValueError('Unsupported local response format')
    if payload.get('tools'):
        body['tools']=payload['tools']
    encoded=json.dumps(body,ensure_ascii=False,allow_nan=False).encode('utf-8')
    if len(encoded)>8*1024*1024:raise ValueError('Local model request exceeds byte budget')
    req=request.Request(endpoint,data=encoded,headers={'Content-Type':'application/json'},method='POST')
    import hashlib
    if evidence is not None:
        evidence.update(request_sha256=hashlib.sha256(encoded).hexdigest(),request_bytes=len(encoded),
            request_utf8=encoded.decode('utf-8'))
    # Never send project text through an inherited outbound HTTP proxy.
    http=opener or request.build_opener(request.ProxyHandler({}),_NoRedirect()).open
    try:
        http_started=time.monotonic()
        with http(req,timeout=timeout) as response:
            if getattr(response,'status',200)!=200:raise RuntimeError('Local model HTTP status unavailable')
            raw=read_bounded_body(response,max_bytes=MAX_RESPONSE,
                deadline=http_started+timeout,check=check_cancelled)
        if len(raw)>MAX_RESPONSE:raise ValueError('Local model response exceeds byte budget')
        if evidence is not None:
            evidence.update(response_sha256=hashlib.sha256(raw).hexdigest(),
                response_bytes=len(raw),response_utf8_valid=False)
        decoded=raw.decode('utf-8',errors='strict')
        if evidence is not None:evidence['response_utf8_valid']=True
        data=json.loads(decoded)
    except error.HTTPError as exc:
        code=exc.code;exc.close();raise ProviderHTTPError('local '+backend,code) from None
    except error.URLError:
        raise RuntimeError('Local '+backend+' transport unavailable') from None
    check_cancelled()
    if backend=='lm_studio' and (not isinstance(data,dict) or data.get('model')!=lm_studio_model_id(model)):
        raise ValueError('LM Studio returned a different model identity')
    choices=data.get('choices') if isinstance(data,dict) else None
    if not isinstance(choices,list) or len(choices)!=1 or not isinstance(choices[0],dict):
        raise ValueError('Local model did not return one completion')
    choice=choices[0];message=choice.get('message')
    if choice.get('finish_reason') not in ('stop','tool_calls') or not isinstance(message,dict):
        raise ValueError('Local model response is incomplete')
    content=message.get('content')
    tools=message.get('tool_calls')
    if not isinstance(content,str) or not content.strip():
        if not isinstance(tools,list) or not tools:raise ValueError('Local model returned no text or tool result')
        message['content']=''
    if tools:
        for tool in tools:
            if not isinstance(tool,dict) or not isinstance(tool.get('function'),dict):raise ValueError('Invalid local tool result')
            arguments=tool['function'].get('arguments')
            if isinstance(arguments,str):tool['function']['arguments']=json.loads(arguments)
            if not isinstance(tool['function'].get('arguments'),dict):raise ValueError('Local tool arguments must be an object')
    usage=data.get('usage') or {}
    if not isinstance(usage,dict):usage={}
    return {'model':model,'message':message,'done':True,'done_reason':'stop',
            'prompt_eval_count':usage.get('prompt_tokens',0),'eval_count':usage.get('completion_tokens',0)}
