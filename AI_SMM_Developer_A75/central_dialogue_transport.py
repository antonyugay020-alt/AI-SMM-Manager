"""GPT-first central dialogue transport, independent from task generation.

Fallback is explicit and reported; content acceptance policies remain unchanged.
Only transport failures allow local fallback, never schema/contract violations.
"""
from __future__ import annotations
import os


def _unambiguously_casual(payload):
    """Only shortcut unmistakably casual turns; ambiguous/project requests use GPT."""
    import json
    messages = payload.get('messages') or []
    if not messages or not isinstance(messages[-1], dict):
        return False
    try:
        content = json.loads(messages[-1].get('content', ''))
    except (TypeError, ValueError):
        return False
    if not isinstance(content, dict) or not isinstance(content.get('utterance'), str):
        return False
    phrase = content['utterance'].strip().lower().strip(' .!?…')
    return phrase in {'привет', 'здравствуй', 'здравствуйте', 'добрый день',
                      'доброе утро', 'добрый вечер', 'как дела', 'спасибо',
                      'благодарю', 'hi', 'hello', 'thanks', 'thank you'}


def default_central_model(env=None):
    """Prefer the explicitly configured OpenAI model over a stale hardcoded ID.

    This selects a model, not an entitlement or successful API probe. Real
    request failures still follow the existing logged hybrid fallback policy.
    """
    env = os.environ if env is None else env
    explicit = str(env.get('AI_SMM_CENTRAL_MODEL') or '').strip()
    if explicit:
        return explicit
    if str(env.get('AI_SMM_ENABLE_OPENAI_API', '')).strip() == '1':
        raw = str(env.get('AI_SMM_CLOUD_MODELS') or '')
        for token in raw.replace(';', ',').split(','):
            model = token.strip()
            if model.casefold().startswith('openai/') and model[7:].strip():
                return model
    return 'openai/gpt-5'


def request_central_dialogue(payload, *, stage, timeout=180, attempts=1, report=None):
    primary = str(payload.get('model') or default_central_model()).strip()
    mode = os.environ.get('AI_SMM_DIALOGUE_MODE', 'local').strip().lower()
    if mode not in {'hybrid', 'gpt', 'local'}:
        raise ValueError('AI_SMM_DIALOGUE_MODE должен быть hybrid, gpt или local')
    local = str(os.environ.get('AI_SMM_LOCAL_DIALOGUE_MODEL', 'qwen3:8b')).strip()
    if mode == 'local' or (mode == 'hybrid' and primary.startswith('openai/') and _unambiguously_casual(payload)):
        if not local or local.startswith('openai/'):
            raise ValueError('Локальная модель диалога должна быть моделью Ollama')
        if report:
            report('central_local_dialogue', model=local, reason='local_mode' if mode == 'local' else 'casual_turn')
        from ollama_resilience import request_chat
        return request_chat({**payload, 'model': local}, stage=stage,
                            timeout=timeout, attempts=attempts, report=report)
    if not primary.startswith('openai/'):
        from ollama_resilience import request_chat
        return request_chat(payload, stage=stage, timeout=timeout, attempts=attempts, report=report)
    from text_providers import call_text
    cloud_payload = {'model': primary, 'messages': payload['messages']}
    try:
        # The model's JSON is *always* checked against DialogueContract.schema()
        # in understand_dialogue; a JSON-object transport is not acceptance.
        return call_text(primary, cloud_payload['messages'], timeout=timeout,
                         temperature=0, response_format='json_object')
    except (OSError, RuntimeError, ValueError) as exc:
        if os.environ.get('AI_SMM_CENTRAL_FALLBACK', '1').strip() == '0':
            raise
        backup = str(os.environ.get('AI_SMM_CENTRAL_FALLBACK_MODEL', 'qwen3:8b')).strip()
        if not backup or backup.startswith('openai/'):
            raise RuntimeError('Некорректная локальная резервная модель') from exc
        if report:
            report('central_coordinator_fallback', primary=primary,
                   backup=backup, failure_type=type(exc).__name__,
                   http_status=getattr(exc, 'status_code', None),
                   provider_error_code=getattr(exc, 'provider_error_code', '') or '')
        from ollama_resilience import request_chat
        return request_chat({**payload, 'model': backup}, stage=stage,
                            timeout=timeout, attempts=attempts, report=report)
