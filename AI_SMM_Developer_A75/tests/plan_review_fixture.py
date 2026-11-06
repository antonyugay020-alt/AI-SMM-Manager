"""Explicit fake semantic plan auditor for legacy execution tests.

Unit fixtures cannot run Ollama; tests of other mechanisms must still
supply a valid plan-review response. Fail-closed behavior is separately
covered by test_architecture_reaudit and test_stage75_orchestration.
"""
import json
import inspect

def with_plan_review(answer):
    def mock(*args, **kwargs):
        if kwargs.get('stage') in ('проверка всего плана', 'повторная проверка всего плана'):
            return json.dumps({'issues': []}, ensure_ascii=False)
        if callable(answer) and 'json_schema' in kwargs:
            try:
                parameters=inspect.signature(answer).parameters.values()
                if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters):
                    kwargs.pop('json_schema',None)
            except (TypeError,ValueError):
                pass
        return answer(*args, **kwargs) if callable(answer) else answer
    return mock
