"""Bounded Ollama retries and per-call diagnostic events (no prompt logging)."""
import json
import hashlib
import os
import re
import socket
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from provider_errors import OllamaStageError, ProviderHTTPError, AuthorExecutionAborted

_CASE_DEADLINE = ContextVar('a75_ollama_case_deadline', default=None)
_UTF8_TRANSPORT = ContextVar('a75_utf8_transport',default=None)

@contextmanager
def capture_utf8_transport():
    """Opt-in exact wire evidence for synthetic qualification; never alter verdicts."""
    evidence={}
    token=_UTF8_TRANSPORT.set(evidence)
    try:yield evidence
    finally:_UTF8_TRANSPORT.reset(token)

_MODEL_CALL_ROLE = ContextVar('a75_ollama_model_call_role', default='author')

@contextmanager
def model_call_role(role):
    """Mark bounded calls as author, primary reviewer, or backup reviewer work."""
    if role not in ('author','reviewer','reviewer_backup'):
        raise ValueError('model call role must be author, reviewer, or reviewer_backup')
    token=_MODEL_CALL_ROLE.set(role)
    try:
        yield
    finally:
        _MODEL_CALL_ROLE.reset(token)


def _provider_response_body(error):
    """Keep bounded project evidence, redact credentials, never send it to telemetry."""
    try:
        body = error.read(8192).decode('utf-8', errors='replace')
    except Exception:
        return ''
    for key, secret in os.environ.items():
        if (key.endswith('_API_KEY') or key.endswith('_TOKEN')) and len(secret) >= 8:
            body = body.replace(secret, '[REDACTED]')
    return re.sub(r'(?i)(Bearer\s+)[^\s\"\'<>]+', r'\1[REDACTED]', body)


class StructuredResponseError(ValueError):
    """Protocol failure with a replayable response, never a content verdict."""
    def __init__(self, stage, detail, response_text=''):
        self.stage = stage
        self.response_text = str(response_text)
        self.response_diagnostics = {}
        super().__init__(f'{stage}: {detail}')


_RESPONSE_TOOL = 'return_structured_result'


def response_diagnostics(result):
    """Content-free evidence of truncation, reasoning-only and tool envelopes."""
    obj = result if isinstance(result, dict) else {}
    message = obj.get('message') if isinstance(obj.get('message'), dict) else {}
    calls = message.get('tool_calls')
    diagnostics = {'message_present': bool(message),
                   'content_chars': len(str(message.get('content') or '')),
                   'thinking_chars': len(str(message.get('thinking') or '')),
                   'tool_call_count': len(calls) if isinstance(calls, list) else 0,
                   'tool_calls_present': bool(calls)}
    for key in ('done', 'done_reason', 'eval_count', 'prompt_eval_count'):
        value = obj.get(key)
        if isinstance(value, (bool, int, float)) or (key == 'done_reason' and
                value in ('stop', 'length', 'load', 'unload')):
            diagnostics[key] = value
    return diagnostics


def _validate_response_root(value, contract, stage, raw=''):
    """Keep transport routes aligned; detailed semantic validation stays in callers."""
    schema={'type':'object'} if contract=='json' else contract if isinstance(contract,dict) else {}
    kinds=schema.get('type')
    kinds=kinds if isinstance(kinds,list) else [kinds] if kinds else []
    checks={'object':isinstance(value,dict),'array':isinstance(value,list),
        'string':isinstance(value,str),'boolean':type(value) is bool,'null':value is None,
        'number':type(value) in (int,float),'integer':type(value) is int}
    invalid=bool(kinds) and not any(checks.get(kind,False) for kind in kinds)
    if isinstance(value,dict):
        invalid=invalid or not set(schema.get('required',[])).issubset(value)
        invalid=invalid or (schema.get('additionalProperties') is False
            and bool(set(value)-set(schema.get('properties',{}))))
    if invalid:
        raise StructuredResponseError(stage,'Корневой результат не соответствует контракту',raw)


def _decode_response_contract(result, payload, stage, *, wrapped_result=False, require_json=False, contract=None):
    """Receive typed arguments; no model-selected function is ever executed."""
    expected = any(isinstance(t, dict) and isinstance(t.get('function'), dict)
                   and t['function'].get('name') == _RESPONSE_TOOL
                   for t in payload.get('tools', []))
    if not expected and not require_json:
        return result, False
    message = result.get('message') if isinstance(result, dict) else None
    if not isinstance(message, dict):
        raise StructuredResponseError(stage, 'Нет структурированного сообщения модели')
    calls = message.get('tool_calls')
    if calls and not expected:
        raise StructuredResponseError(stage, 'Неожиданный вызов функции вместо JSON')
    if calls:
        raw = json.dumps(calls, ensure_ascii=False)
        if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
            raise StructuredResponseError(stage, 'Ожидался один структурированный результат', raw)
        function = calls[0].get('function')
        if not isinstance(function, dict) or function.get('name') != _RESPONSE_TOOL:
            raise StructuredResponseError(stage, 'Неизвестный контракт ответа модели', raw)
        arguments = function.get('arguments')
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError as exc:
                raise StructuredResponseError(stage, str(exc), raw) from exc
        parameters=next(t['function']['parameters'] for t in payload['tools']
            if isinstance(t,dict) and isinstance(t.get('function'),dict)
            and t['function'].get('name')==_RESPONSE_TOOL)
        # Envelope ownership is chosen by the caller contract, never inferred
        # from a semantic field literally named "result".
        wrapped=wrapped_result
        if not isinstance(arguments,dict):
            raise StructuredResponseError(stage, 'Аргументы результата не являются объектом', raw)
        if wrapped:
            if set(arguments)!={'result'}:
                raise StructuredResponseError(stage, 'Нет единственного поля result в аргументах', raw)
            value=arguments['result']
        elif (set(arguments)=={'result'} and 'result' not in parameters.get('properties',{})
                and parameters.get('additionalProperties') is False):
            # Earlier providers emitted the old transport wrapper. Unwrap only
            # this exact envelope; never rewrite semantic keys or invent values.
            value=arguments['result']
        else:
            value=arguments
        if not wrapped and (not isinstance(value,dict)
                or not set(parameters.get('required',[])).issubset(value)
                or (parameters.get('additionalProperties') is False
                    and set(value)-set(parameters.get('properties',{})))):
            raise StructuredResponseError(stage, 'Аргументы не соответствуют полям контракта', raw)
        content = json.dumps(value, ensure_ascii=False)
        _validate_response_root(value,contract,stage,raw)
        return {**result, 'message': {**message, 'content': content}}, True
    # Compatibility with providers that choose a text answer even with tools.
    # Accept only valid JSON, without changing keys, values or punctuation.
    raw = str(message.get('content') or '')
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.I).strip()
    try:
        value=json.loads(text)
    except ValueError as exc:
        raise StructuredResponseError(stage, str(exc), raw) from exc
    _validate_response_root(value,contract,stage,raw)
    return {**result, 'message': {**message, 'content': text}}, False

@contextmanager
def request_deadline(seconds, *, max_call_seconds=None, retry_timeouts=True,
                      abort_case_on_timeout=False, reviewer_reserve_seconds=0,
                      reviewer_backup_reserve_seconds=0):
    """Bound calls; acceptance can abandon one case after a model timeout."""
    if isinstance(seconds,bool) or not isinstance(seconds,(int,float)) or seconds<=0:
        raise ValueError('case deadline must be a positive number of seconds')
    if (max_call_seconds is not None and
            (isinstance(max_call_seconds,bool) or not isinstance(max_call_seconds,(int,float))
             or max_call_seconds<=0)):
        raise ValueError('per-call deadline must be a positive number of seconds')
    if type(retry_timeouts) is not bool or type(abort_case_on_timeout) is not bool:
        raise ValueError('timeout policy flags must be boolean')
    for value in (reviewer_reserve_seconds,reviewer_backup_reserve_seconds):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or value<0:
            raise ValueError('reviewer reserves must be non-negative numbers')
    if reviewer_reserve_seconds+reviewer_backup_reserve_seconds>=seconds:
        raise ValueError('reviewer reserves must leave time for author work')
    scope={'deadline':time.monotonic()+float(seconds),'expired':False,
           'aborted_on_model_timeout':False,
           'author_budget_exhausted':False,
           'reviewer_reserve_seconds':float(reviewer_reserve_seconds),
           'reviewer_backup_reserve_seconds':float(reviewer_backup_reserve_seconds),
           'max_call_seconds':float(max_call_seconds) if max_call_seconds is not None else None,
           'retry_timeouts':retry_timeouts,
           'abort_case_on_timeout':abort_case_on_timeout}
    token=_CASE_DEADLINE.set(scope)
    try:
        yield scope
    finally:
        _CASE_DEADLINE.reset(token)

def _prepare_response_contract(payload):
    """Keep the same response contract across native and prompt-only providers.

    Local Ollama can constrain decoding with a JSON schema. Ollama Cloud
    currently cannot, so the schema must also be present in the prompt and
    callers must still validate the returned data. Never mutate caller input.
    """
    from text_providers import is_ollama_cloud_tag, parse_cloud_model
    prepared = dict(payload)
    contract = payload.get('format')
    if contract != 'json' and not isinstance(contract, dict):
        return prepared, None
    messages = [dict(item) for item in payload.get('messages', [])]
    instruction = 'Верни только JSON, без Markdown и пояснений вне JSON.'
    if isinstance(contract, dict):
        encoded = json.dumps(contract, ensure_ascii=False, separators=(',', ':'))
        instruction += '\nJSON Schema ответа: ' + encoded
    else:
        encoded = None
    if encoded is None or not any(encoded in str(m.get('content', '')) for m in messages):
        if messages and messages[0].get('role') == 'system' and isinstance(messages[0].get('content'), str):
            messages[0]['content'] += '\n' + instruction
        else:
            messages.insert(0, {'role': 'system', 'content': instruction})
    prepared['messages'] = messages
    model = str(payload.get('model') or '')
    if is_ollama_cloud_tag(model) and parse_cloud_model(model) is None:
        prepared.pop('format', None)
        from model_protocol import uses_prompt_route
        if not prepared.get('tools') and not uses_prompt_route(model,contract):
            prepared['tools'] = [{'type': 'function', 'function': {
                'name': _RESPONSE_TOOL,
                'description': 'Передать единственный полный результат по заданному контракту. Не выполняет внешних действий.',
                'parameters': ({'type':'object','additionalProperties':True} if contract=='json' else
                    contract if isinstance(contract,dict) and contract.get('type')=='object' else {'type': 'object', 'additionalProperties': False,
                               'required': ['result'], 'properties': {
                                   'result': contract if isinstance(contract, dict) else {'type': 'object'},
                               }}),
            }}]
            direct=(contract=='json' or isinstance(contract,dict) and contract.get('type')=='object')
            transport_instruction=(
                'Передай поля результата непосредственно как типизированные аргументы. Не добавляй обёртки. '
                if direct else 'Передай единственный аргумент result, его значение — результат по JSON Schema. ')
            messages[0]['content'] += (
                '\nВызови return_structured_result ровно один раз. '+transport_instruction+
                'Не кодируй значение JSON как строку. Следуй схеме аргументов функции. '
                'Это только передача результата, внешние действия не выполняются.')
        mode = 'prompt_schema' if encoded else 'prompt_json'
    elif parse_cloud_model(model) is not None:
        # API providers keep their existing JSON-object route; the full schema
        # is supplied in the prompt rather than falsely claiming native support.
        prepared['format'] = 'json'
        mode = 'prompt_schema' if encoded else 'json_object'
    else:
        mode = 'native_schema' if encoded else 'json_object'
    return prepared, mode


def _request_chat(payload, *, stage, timeout=300, attempts=2, report=None,
                 endpoint='http://127.0.0.1:11434/api/chat'):
    """Single model transport boundary for generation and validation roles.

    Plain model tags use Ollama. Explicit provider/model tags use the configured
    cloud route through text_providers. Paid OpenAI routing requires both an
    explicit model selection and OPENAI_API_KEY. Production
    callers share retry policy and content-free timing events through here.
    """
    from model_protocol import prepare_model_payload, note_empty_tool_result
    requested_contract=payload.get('format')
    requested_limit=(payload.get('options') or {}).get('num_predict')
    base_payload=prepare_model_payload(payload)
    wrapped_result=isinstance(requested_contract,dict) and requested_contract.get('type')!='object'
    payload, contract_mode = _prepare_response_contract(base_payload)
    model = str(payload.get('model') or '').strip()
    from text_providers import parse_cloud_model
    cloud_model = parse_cloud_model(model)
    if __import__('os').environ.get('AI_SMM_QA_LOCAL_ONLY')=='1':
        allowed=json.loads(__import__('os').environ.get('AI_SMM_QA_ALLOWED_MODELS','[]'))
        from text_providers import is_ollama_cloud_tag
        if cloud_model is not None or is_ollama_cloud_tag(model) or model not in allowed:raise RuntimeError('QA forbids unapproved/cloud model transport')
    req = None
    if cloud_model is None:
        req = urllib.request.Request(endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json; charset=utf-8'})
        evidence=_UTF8_TRANSPORT.get()
        if evidence is not None:
            evidence.update(request_sha256=hashlib.sha256(req.data).hexdigest(),
                request_bytes=len(req.data),request_utf8=req.data.decode('utf-8',errors='strict'))
    for attempt in range(1, attempts + 1):
        from cooperative_cancellation import check_cancelled
        check_cancelled()
        scope=_CASE_DEADLINE.get()
        role=_MODEL_CALL_ROLE.get()
        if scope is not None and role=='author' and scope.get('aborted_on_model_timeout'):
            raise AuthorExecutionAborted(stage,'acceptance case stopped after a model-call timeout')
        if scope is not None and role=='author' and scope.get('author_budget_exhausted'):
            raise OllamaStageError(stage,'author budget ended to preserve independent-review time')
        call_timeout=timeout
        if scope is not None:
            remaining=scope['deadline']-time.monotonic()
            if remaining<=0:
                scope['expired']=True
                if report:
                    report('case_deadline_exceeded',stage_name=stage)
                raise OllamaStageError(stage,'acceptance author-case time budget exceeded')
            reserve=(scope['reviewer_reserve_seconds']+scope['reviewer_backup_reserve_seconds']
                     if role=='author' else
                     scope['reviewer_backup_reserve_seconds'] if role=='reviewer' else 0.0)
            usable=remaining-reserve
            if usable<=0:
                if role=='author':
                    scope['author_budget_exhausted']=True
                    if report:
                        report('author_budget_reserved_for_review',stage_name=stage,
                               reserve_seconds=(scope['reviewer_reserve_seconds']+
                                                scope['reviewer_backup_reserve_seconds']))
                    raise OllamaStageError(stage,'author budget ended to preserve independent-review time')
                if role=='reviewer':
                    if report:
                        report('reviewer_backup_budget_reserved',stage_name=stage,
                               reserve_seconds=scope['reviewer_backup_reserve_seconds'])
                    raise OllamaStageError(stage,'primary reviewer budget ended to preserve backup-review time')
                scope['expired']=True
                if report:
                    report('case_deadline_exceeded',stage_name=stage)
                raise OllamaStageError(stage,'acceptance case time budget exhausted')
            limits=[float(timeout),usable]
            if scope.get('max_call_seconds') is not None:
                limits.append(scope['max_call_seconds'])
            call_timeout=min(limits)
        started = time.monotonic()
        from diagnostic_report import record_model_event
        record_model_event('model_transport_request',stage=stage,attempt=attempt,role=role,payload=payload)
        try:
            if cloud_model is None:
                from local_text_runtime import configured_for,request_local
                if configured_for(model):
                    result=request_local(payload,timeout=call_timeout)
                    record_model_event('local_model_transport',stage=stage,backend='llama_cpp')
                else:
                    with urllib.request.urlopen(req, timeout=call_timeout) as response:
                        raw_response=response.read()
                        evidence=_UTF8_TRANSPORT.get()
                        if evidence is not None:
                            evidence.update(response_sha256=hashlib.sha256(raw_response).hexdigest(),
                                response_bytes=len(raw_response),response_utf8_valid=False)
                        decoded=raw_response.decode('utf-8',errors='strict')
                        if evidence is not None:evidence['response_utf8_valid']=True
                        record_model_event('model_transport_body',stage=stage,attempt=attempt,
                            body=decoded)
                        result=json.loads(decoded)
            else:
                from text_providers import call_text
                options = payload.get('options') if isinstance(payload.get('options'), dict) else {}
                temperature = options.get('temperature', 0.2)
                response_format = 'json_object' if payload.get('format') == 'json' else None
                result = call_text(model, payload.get('messages') or [], timeout=call_timeout,
                                   temperature=temperature, response_format=response_format)
            check_cancelled()
            if scope is not None and time.monotonic()>=scope['deadline']:
                scope['expired']=True
            if not isinstance(result, dict):
                raise ValueError('Ollama returned a non-object response')
            try:
                result, used_tool = _decode_response_contract(result, payload, stage,
                    wrapped_result=wrapped_result, require_json=contract_mode is not None,contract=requested_contract)
            except StructuredResponseError as exc:
                exc.response_diagnostics = response_diagnostics(result)
                synthetic=any(isinstance(t,dict) and t.get('function',{}).get('name')==_RESPONSE_TOOL
                              for t in payload.get('tools',[]))
                if synthetic and note_empty_tool_result(model,requested_contract,result):
                    exc.response_diagnostics['next_structured_route']='prompt_json'
                elif (synthetic and 'Аргументы не соответствуют полям контракта' in str(exc)):
                    from model_protocol import note_incompatible_tool_result
                    if note_incompatible_tool_result(model,requested_contract,result):
                        exc.response_diagnostics['next_structured_route']='prompt_json'
                effective_limit=(payload.get('options') or {}).get('num_predict')
                if requested_limit is not None:
                    exc.response_diagnostics.update(requested_num_predict=requested_limit,
                        effective_num_predict=effective_limit)
                raise
            actual_contract_mode = 'tool_arguments' if used_tool else contract_mode
            if report:
                report('ollama_call' if cloud_model is None else 'cloud_model_call', stage_name=stage, attempt=attempt,
                       duration_s=round(time.monotonic()-started, 2), outcome='ok',
                       provider=cloud_model[0] if cloud_model else 'ollama', model=model,
                       response_contract_mode=actual_contract_mode)
            return result
        except urllib.error.HTTPError as exc:
            # HTTPError is a URLError subclass: handle it before the broad
            # network-error branch. A failed provider response is not a model
            # draft. Retry only explicitly transient statuses within the
            # caller's existing attempt/deadline budget. Never retry quota,
            # authentication, request-contract errors, or a model timeout.
            from io import BytesIO
            try:raw_error=exc.read()
            except Exception:raw_error=b''
            finally:exc.close()
            record_model_event('model_provider_error',stage=stage,attempt=attempt,status=exc.code,
                body=raw_error.decode('utf-8','replace'))
            body = _provider_response_body(BytesIO(raw_error))
            error = ProviderHTTPError(stage, exc.code, body)
            retry = exc.code in (500, 502, 503, 504) and attempt < attempts
            if report:
                report('ollama_call' if cloud_model is None else 'cloud_model_call',
                       stage_name=stage, attempt=attempt,
                       duration_s=round(time.monotonic()-started, 2), outcome='provider_error',
                       provider=cloud_model[0] if cloud_model else 'ollama', model=model,
                       error_type='ProviderHTTPError', http_status=exc.code,
                       response_body_chars=len(body), retry_scheduled=retry)
            if retry:
                continue
            raise error from exc
        except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
            import traceback
            record_model_event('model_transport_error',stage=stage,attempt=attempt,
                error_type=type(exc).__name__,error=str(exc),traceback=traceback.format_exc())
            is_timeout = isinstance(exc, (TimeoutError, socket.timeout)) or isinstance(getattr(exc, 'reason', None), (TimeoutError, socket.timeout))
            check_cancelled()
            if scope is not None and time.monotonic()>=scope['deadline']:
                scope['expired']=True
            if report:
                report('ollama_call' if cloud_model is None else 'cloud_model_call', stage_name=stage, attempt=attempt,
                       duration_s=round(time.monotonic()-started, 2),
                       outcome='timeout' if is_timeout else 'connection_error',
                       provider=cloud_model[0] if cloud_model else 'ollama', model=model,
                       error_type=type(exc).__name__)
            if (scope is not None and is_timeout and scope.get('abort_case_on_timeout')
                    and role=='author'):
                scope['aborted_on_model_timeout']=True
                if report:
                    report('case_aborted_after_model_timeout',stage_name=stage)
                raise AuthorExecutionAborted(stage,'acceptance case stopped after a model-call timeout') from exc
            if scope is not None and is_timeout and role in ('reviewer','reviewer_backup') and report:
                report('reviewer_model_timeout',stage_name=stage)
            if (not is_timeout or attempt == attempts or
                    (scope is not None and not scope.get('retry_timeouts',True))):
                raise OllamaStageError(stage, f'{type(exc).__name__}: {exc}') from exc
            time.sleep(min(attempt, 2))
        except RuntimeError as exc:
            import traceback
            record_model_event('model_transport_error',stage=stage,attempt=attempt,
                error_type=type(exc).__name__,error=str(exc),traceback=traceback.format_exc())
            status=exc.status_code if isinstance(exc,ProviderHTTPError) else None
            retry=status in (500,502,503,504) and attempt<attempts
            if report:
                extra=({'http_status':status,'retry_scheduled':retry,
                        'provider_error_code':getattr(exc,'provider_error_code','')}
                       if status is not None else {})
                report('ollama_call' if cloud_model is None else 'cloud_model_call', stage_name=stage, attempt=attempt,
                       duration_s=round(time.monotonic()-started, 2),
                       outcome='provider_error', provider=cloud_model[0] if cloud_model else 'ollama',
                       model=model, error_type=type(exc).__name__,**extra)
            if retry:
                continue
            if isinstance(exc,ProviderHTTPError):
                raise
            raise OllamaStageError(stage, str(exc)) from exc
        except ValueError as exc:
            import traceback
            record_model_event('model_transport_error',stage=stage,attempt=attempt,
                error_type=type(exc).__name__,error=str(exc),traceback=traceback.format_exc())
            if report:
                extra = ({'response_diagnostics': exc.response_diagnostics,
                          'response_contract_mode': contract_mode}
                         if isinstance(exc, StructuredResponseError) else {})
                report('ollama_call' if cloud_model is None else 'cloud_model_call', stage_name=stage, attempt=attempt,
                       duration_s=round(time.monotonic()-started, 2),
                       outcome='invalid_response', provider=cloud_model[0] if cloud_model else 'ollama',
                       model=model, error_type=type(exc).__name__, **extra)
            if isinstance(exc, StructuredResponseError):
                if (exc.response_diagnostics.get('next_structured_route')=='prompt_json'
                        and attempt<attempts):
                    payload,contract_mode=_prepare_response_contract(base_payload)
                    if cloud_model is None:
                        req=urllib.request.Request(endpoint,
                            data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),
                            headers={'Content-Type':'application/json'})
                    continue
                raise
            raise OllamaStageError(stage, f'{type(exc).__name__}: {exc}') from exc


def request_chat(payload, *, stage, timeout=300, attempts=2, report=None,
                 endpoint='http://127.0.0.1:11434/api/chat'):
    """Trace the existing gateway without changing any retry or verdict policy."""
    from diagnostic_report import record_model_event
    import traceback
    started=time.monotonic()
    record_model_event('model_call_started',stage=stage,role=_MODEL_CALL_ROLE.get(),payload=payload)
    try:
        result=_request_chat(payload,stage=stage,timeout=timeout,attempts=attempts,report=report,endpoint=endpoint)
    except Exception as exc:
        record_model_event('model_call_failed',stage=stage,error_type=type(exc).__name__,
            error=str(exc),traceback=traceback.format_exc(),duration_s=round(time.monotonic()-started,3))
        raise
    record_model_event('model_call_completed',stage=stage,response=result,
        duration_s=round(time.monotonic()-started,3))
    return result
