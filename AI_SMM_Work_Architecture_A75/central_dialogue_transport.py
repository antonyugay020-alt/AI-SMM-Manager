"""Local-first central dialogue transport, independent from task generation.

Fallback is explicit and reported; content acceptance policies remain unchanged.
Only transport failures allow local fallback, never schema/contract violations.
"""
from __future__ import annotations
import os


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
    return str(env.get('AI_SMM_LOCAL_DIALOGUE_MODEL') or 'qwen3:8b').strip()


def request_central_dialogue(payload, *, stage, timeout=180, attempts=1, report=None):
    primary = str(payload.get('model') or default_central_model()).strip()
    mode = os.environ.get('AI_SMM_DIALOGUE_MODE', 'hybrid').strip().lower()
    if mode not in {'hybrid', 'gpt', 'local'}:
        raise ValueError('AI_SMM_DIALOGUE_MODE должен быть hybrid, gpt или local')
    local = str(os.environ.get('AI_SMM_LOCAL_DIALOGUE_MODEL', 'qwen3:8b')).strip()
    if mode in {'local','hybrid'}:
        from text_providers import parse_cloud_model
        if not local or parse_cloud_model(local) is not None:
            raise ValueError('Локальная модель диалога должна быть моделью Ollama')
        from ollama_resilience import request_chat
        if report:report('central_local_dialogue',model=local,reason='local_first')
        try:
            return request_chat({**payload,'model':local},stage=stage,
                                timeout=timeout,attempts=attempts,report=report)
        except (OSError,RuntimeError) as exc:
            from provider_errors import ProviderHTTPError
            import urllib.error
            current=exc
            transport=False
            for _ in range(8):
                if isinstance(current,(OSError,urllib.error.URLError,ProviderHTTPError)):
                    transport=True;break
                current=getattr(current,'__cause__',None)
                if current is None:break
            if mode=='local' or not transport or parse_cloud_model(primary) is None:
                raise
            if report:report('central_cloud_fallback',primary=local,backup=primary,
                             failure_type=type(exc).__name__)
            # Cloud paid authority is still required at the shared transport.
            from text_providers import call_text
            return call_text(primary,payload['messages'],timeout=timeout,
                             temperature=0,response_format='json_object')
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
    except (OSError, RuntimeError) as exc:
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
