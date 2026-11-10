"""Opt-in loopback llama.cpp transport for real local GGUF model execution.

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
    return (env.get('AI_SMM_LOCAL_LLM_BACKEND')=='llama_cpp'
            and isinstance(model,str) and bool(model.strip())
            and model==env.get('AI_SMM_LOCAL_LLM_MODEL'))


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


def request_local(payload,*,timeout=180,environ=None,opener=None):
    env=os.environ if environ is None else environ
    model=payload.get('model') if isinstance(payload,dict) else None
    if not configured_for(model,environ=env):raise RuntimeError('Local llama.cpp backend not selected for this model')
    check_cancelled()
    endpoint=endpoint_for(env.get('AI_SMM_LOCAL_LLM_URL','http://127.0.0.1:8080'))
    messages=payload.get('messages')
    if not isinstance(messages,list) or not messages:raise ValueError('Local chat messages required')
    options=payload.get('options') or {}
    if not isinstance(options,dict):raise ValueError('Invalid local model options')
    temperature=options.get('temperature',.2)
    if type(temperature) not in (int,float) or not math.isfinite(temperature) or not 0<=temperature<=2:
        raise ValueError('Invalid local model temperature')
    maximum=options.get('num_predict',2048)
    if type(maximum) is not int or not 1<=maximum<=32768:raise ValueError('Local output token budget invalid')
    body={'model':model,'messages':messages,'temperature':temperature,'max_tokens':maximum,
          'stream':False,'chat_template_kwargs':{'enable_thinking':False}}
    contract=payload.get('format')
    if isinstance(contract,dict):body['response_format']={'type':'json_object','schema':contract}
    elif contract=='json':body['response_format']={'type':'json_object'}
    elif contract is not None:raise ValueError('Unsupported local response format')
    if payload.get('tools'):
        body['tools']=payload['tools']
    encoded=json.dumps(body,ensure_ascii=False,allow_nan=False).encode('utf-8')
    if len(encoded)>8*1024*1024:raise ValueError('Local model request exceeds byte budget')
    req=request.Request(endpoint,data=encoded,headers={'Content-Type':'application/json'},method='POST')
    # Never send project text through an inherited outbound HTTP proxy.
    http=opener or request.build_opener(request.ProxyHandler({}),_NoRedirect()).open
    try:
        http_started=time.monotonic()
        with http(req,timeout=timeout) as response:
            if getattr(response,'status',200)!=200:raise RuntimeError('Local model HTTP status unavailable')
            raw=read_bounded_body(response,max_bytes=MAX_RESPONSE,
                deadline=http_started+timeout,check=check_cancelled)
        if len(raw)>MAX_RESPONSE:raise ValueError('Local model response exceeds byte budget')
        data=json.loads(raw)
    except error.HTTPError as exc:
        code=exc.code;exc.close();raise ProviderHTTPError('local llama.cpp',code) from None
    except error.URLError:
        raise RuntimeError('Local llama.cpp transport unavailable') from None
    check_cancelled()
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
