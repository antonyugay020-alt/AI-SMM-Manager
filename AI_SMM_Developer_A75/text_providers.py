"""Opt-in cloud text models for randomized acceptance.

Cloud models are never discovered or called unless they are explicitly listed
in AI_SMM_CLOUD_MODELS and their provider key is present in the environment.
OpenRouter models must be pinned to a `:free` model id; this prevents its
dynamic free router from silently changing the model during a comparison.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from provider_errors import ProviderHTTPError

_KEYS = {'groq': 'GROQ_API_KEY', 'openrouter': 'OPENROUTER_API_KEY',
         'gemini': 'GEMINI_API_KEY', 'openai': 'OPENAI_API_KEY',
         'anthropic': 'ANTHROPIC_API_KEY'}
_ANTHROPIC_ALLOWED = frozenset({'claude-sonnet-5-5'})
_GROQ_FREE_MODELS = frozenset({'openai/gpt-oss-20b','openai/gpt-oss-120b',
                               'qwen/qwen3.8-27b'})
_GEMINI_FREE_MODELS = frozenset({'gemini-3.1-flash-lite'})


def parse_cloud_model(model: str):
    value = str(model or '').strip()
    provider, sep, model_id = value.partition('/')
    provider = provider.casefold()
    if not sep or provider not in _KEYS or not model_id.strip():
        return None
    model_id = model_id.strip()
    if provider == 'openrouter' and not model_id.endswith(':free'):
        raise ValueError('Для бесплатного OpenRouter задавайте только точную модель с суффиксом :free')
    if provider == 'groq' and model_id not in _GROQ_FREE_MODELS:
        raise ValueError('Для бесплатного Groq задавайте модель из текущего Free Plan allowlist')
    if provider == 'gemini' and model_id not in _GEMINI_FREE_MODELS:
        raise ValueError('Для бесплатного Gemini разрешена только gemini-3.1-flash-lite')
    if provider == 'anthropic' and model_id not in _ANTHROPIC_ALLOWED:
        raise ValueError('Anthropic: модель не подтверждена для этого подключения')
    return provider, model_id


def is_ollama_cloud_tag(model: str) -> bool:
    """Classify Ollama-hosted cloud tags separately from API-key providers.

    Ollama exposes cloud models through the same local `/api/tags` inventory,
    with tags such as `gemma4:cloud` and `gpt-oss:20b-cloud`. They do not use
    this module's Groq/OpenRouter/Gemini API-key routes.
    """
    name=str(model or '').strip().casefold()
    return name.endswith(':cloud') or name.endswith('-cloud')


def configured_cloud_models(env=None):
    """Return only explicitly allowed free-cloud model ids with configured keys."""
    env = os.environ if env is None else env
    raw = str(env.get('AI_SMM_CLOUD_MODELS', '') or '')
    result, seen = [], set()
    for item in raw.replace(';', ',').split(','):
        name = item.strip()
        if not name:
            continue
        parsed = parse_cloud_model(name)
        if parsed is None:
            raise ValueError('Облачная модель должна иметь вид openai/model, groq/model, openrouter/vendor/model:free или gemini/model')
        provider, _ = parsed
        ack_name={'groq':'AI_SMM_GROQ_FREE_TIER_ACK','gemini':'AI_SMM_GEMINI_FREE_TIER_ACK'}.get(provider)
        if ack_name and str(env.get(ack_name, '')).strip() != '1':
            continue
        if provider == 'openai' and (
                str(env.get('AI_SMM_ENABLE_OPENAI_API', '')).strip() != '1' or
                str(env.get('AI_SMM_ENABLE_OPENAI_ACCEPTANCE', '')).strip() != '1'):
            continue
        if provider == 'anthropic' and (env.get('AI_SMM_ENABLE_ANTHROPIC_API') != '1'
                                        or env.get('AI_SMM_ENABLE_ANTHROPIC_ACCEPTANCE') != '1'):
            continue
        if provider == 'openai' and env is os.environ:
            # Runtime access and model selection must use the same explicit,
            # process-local credential source; do not export the key to child processes.
            from cloud_credentials import get_openai_key
            available_key = get_openai_key()
        else:
            available_key = str(env.get(_KEYS[provider], '') or '').strip()
        if not available_key:
            continue
        if name.casefold() not in seen:
            seen.add(name.casefold())
            result.append(name)
    return result


def _anthropic_chat(model_id, messages, timeout, api_key, temperature, response_format):
    """Native Anthropic Messages API with strict roles, completion and JSON gates."""
    if not isinstance(messages, list) or response_format not in (None, 'json_object'):
        raise ValueError('Anthropic: invalid request')
    system, chat = [], []
    for msg in messages:
        if not isinstance(msg, dict) or msg.get('role') not in ('system', 'user', 'assistant'):
            raise ValueError('Anthropic: unsupported message role')
        content = msg.get('content')
        if not isinstance(content, str) or not content.strip():
            raise ValueError('Anthropic: empty or nontext content')
        role = msg['role']
        if role == 'system':
            if chat:
                raise ValueError('Anthropic: system messages must precede dialogue')
            system.append(content)
        elif chat and chat[-1]['role'] == role:
            chat[-1]['content'] += '\n\n' + content
        else:
            chat.append({'role': role, 'content': content})
    if not chat or chat[0]['role'] != 'user' or chat[-1]['role'] != 'user':
        raise ValueError('Anthropic: expected conversation ending with user prompt')
    if response_format == 'json_object':
        system.append('Output exactly one valid JSON object without Markdown.')
    # Newer Claude variants may reject sampling parameters while native
    # reasoning settings are selected by the model. The independently verified
    # direct Anthropic reviewer uses this same minimal Messages payload.
    body = {'model': model_id, 'max_tokens': 4096, 'messages': chat}
    if system:
        body['system'] = '\n\n'.join(system)
    req = urllib.request.Request(
        'https://api.anthropic.com/v1/messages',
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers={'x-api-key': api_key, 'anthropic-version': '2023-06-01',
                 'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw_response = response.read()
        from diagnostic_report import record_model_event
        record_model_event('cloud_transport_metadata',
                           provider='anthropic', bytes_received=len(raw_response))
        result = json.loads(raw_response)
    if not isinstance(result, dict) or result.get('stop_reason') != 'end_turn':
        raise ValueError('Anthropic: incomplete model response')
    blocks = result.get('content')
    if not isinstance(blocks, list) or not blocks or any(
            not isinstance(block, dict) or block.get('type') != 'text'
            or not isinstance(block.get('text'), str) for block in blocks):
        raise ValueError('Anthropic: unexpected response content')
    text = '\n'.join(block['text'] for block in blocks).strip()
    if not text:
        raise ValueError('Anthropic: empty response')
    if response_format == 'json_object':
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError) as exc:
            raise ValueError('Anthropic: malformed structured output') from exc
        if not isinstance(parsed, dict):
            raise ValueError('Anthropic: structured output must be JSON object')
    usage = result.get('usage')
    return text, usage if isinstance(usage, dict) else {}


def _chat_request(provider, model_id, messages, timeout, api_key, temperature,
                  response_format=None):
    if provider == 'anthropic':
        return _anthropic_chat(model_id, messages, timeout, api_key,
                               temperature, response_format)
    if provider == 'gemini':
        system = '\n'.join(str(m.get('content') or '') for m in messages
                           if isinstance(m, dict) and m.get('role') == 'system')
        inputs = [str(m.get('content') or '') for m in messages
                  if isinstance(m, dict) and m.get('role') != 'system' and m.get('content')]
        body = {'model': model_id, 'input': '\n\n'.join(inputs), 'store': False,
                'generation_config': {'temperature': temperature}}
        if system:
            body['system_instruction'] = system
        req = urllib.request.Request(
            'https://generativelanguage.googleapis.com/v1beta/interactions',
            data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
            headers={'x-goog-api-key': api_key, 'Content-Type': 'application/json'},
            method='POST')
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw_response=response.read()
            from diagnostic_report import record_model_event
            record_model_event('cloud_transport_metadata',provider=provider,bytes_received=len(raw_response))
            result=json.loads(raw_response)
        text = str(result.get('output_text') or '').strip() if isinstance(result, dict) else ''
        if not text and isinstance(result, dict):
            parts = []
            for item in result.get('output', []):
                if not isinstance(item, dict) or item.get('type') != 'model_output':
                    continue
                for part in item.get('content', []):
                    if isinstance(part, dict) and part.get('type') in ('text', 'output_text') and part.get('text'):
                        parts.append(str(part['text']))
            text = '\n'.join(parts).strip()
        if not text:
            raise ValueError('gemini вернул ответ без текста')
        usage = result.get('usage') if isinstance(result, dict) else None
        return text, usage if isinstance(usage, dict) else {}
    if provider == 'openai':
        url = 'https://api.openai.com/v1/chat/completions'
    elif provider == 'groq':
        url = 'https://api.groq.com/openai/v1/chat/completions'
    else:
        url = 'https://openrouter.ai/api/v1/chat/completions'
    body = {'model': model_id, 'messages': messages}
    # OpenAI reasoning models reject sampling parameters such as temperature
    # when reasoning is enabled. Omitting it lets the model use its native
    # reasoning defaults and works across both reasoning and non-reasoning
    # chat models.
    if provider != 'openai':
        body['temperature'] = temperature
    if provider == 'openai' and response_format == 'json_object':
        body['response_format'] = {'type': 'json_object'}
    headers = {'Authorization': 'Bearer ' + api_key,
               'Content-Type': 'application/json'}
    if provider == 'openrouter':
        headers['HTTP-Referer'] = 'https://ai-smm-manager.local'
        headers['X-Title'] = 'AI SMM Manager acceptance test'
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                                 headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw_response=response.read()
        from diagnostic_report import record_model_event
        record_model_event('cloud_transport_metadata',provider=provider,bytes_received=len(raw_response))
        result=json.loads(raw_response)
    choices = result.get('choices') if isinstance(result, dict) else None
    if not isinstance(choices, list) or not choices:
        raise ValueError(f'{provider} вернул пустой ответ')
    message = choices[0].get('message') if isinstance(choices[0], dict) else None
    content = message.get('content') if isinstance(message, dict) else None
    if isinstance(content, list):
        content = '\n'.join(str(x.get('text') or '') for x in content if isinstance(x, dict))
    if not isinstance(content, str) or not content.strip():
        raise ValueError(f'{provider} вернул ответ без текста')
    usage = result.get('usage') if isinstance(result, dict) else None
    return content.strip(), usage if isinstance(usage, dict) else {}


def call_text(model, messages, *, timeout=180, temperature=0.2, response_format=None,
              environ=None):
    parsed = parse_cloud_model(model)
    if parsed is None:
        raise ValueError('Неизвестный формат облачной модели')
    provider, model_id = parsed
    env = os.environ if environ is None else environ
    if provider == 'anthropic' and (
            env.get('AI_SMM_ENABLE_ANTHROPIC_API') != '1'
            or env.get('AI_SMM_ENABLE_ANTHROPIC_ACCEPTANCE') != '1'):
        raise RuntimeError('Anthropic API отключён: требуется явное разрешение платных вызовов')
    key_name = _KEYS[provider]
    if provider == 'openai':
        from cloud_credentials import get_openai_key
        api_key = get_openai_key(environ=environ)
    else:
        api_key = str(env.get(key_name, '') or '').strip()
    if not api_key:
        raise RuntimeError(f'Не задан {key_name}; облачный запрос не отправлен')
    try:
        text, usage = _chat_request(provider, model_id, messages, timeout, api_key,
                                    temperature, response_format=response_format)
    except urllib.error.HTTPError as exc:
        # Keep only non-sensitive structured identifiers; never return provider
        # messages, response bodies, prompts, or authorization material.
        safe_code=''
        try:
            response_body=exc.read().decode('utf-8','replace')
            from diagnostic_report import record_model_event
            record_model_event('cloud_provider_error',provider=provider,status=exc.code)
            payload=json.loads(response_body)
            error=payload.get('error',{}) if isinstance(payload,dict) else {}
            if isinstance(error,dict):
                code=error.get('code')
                if isinstance(code,str) and len(code)<=80 and code.replace('_','').replace('-','').isalnum():
                    safe_code=code
        except Exception:
            pass
        finally:
            exc.close()
        suffix=f' ({safe_code})' if safe_code else ''
        failure=ProviderHTTPError(provider+' API',exc.code)
        failure.provider_error_code=safe_code
        failure.args=(f'{provider}: API HTTP {exc.code}{suffix}; запрос не завершён',)
        raise failure from None
    return {'message': {'content': text}, 'done_reason': 'stop',
            'provider': provider, 'model': model_id, 'usage': usage}
