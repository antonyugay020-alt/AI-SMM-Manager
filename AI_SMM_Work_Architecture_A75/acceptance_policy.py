"""Bound retry behavior used only by randomized synthetic acceptance runs."""
from __future__ import annotations

# One probe call must leave room for a model's mandatory reasoning plus JSON.
# The wall-clock timeout, single-call policy and strict verdict are unchanged.
REVIEW_PROBE_OUTPUT_TOKENS = 1024


def is_full_delivery_acceptance(context) -> bool:
    """A live randomized full-profile run must exercise production recovery.

    A first-pass benchmark remains useful, but must not masquerade as a
    full-delivery run. The mode is explicit and bound to the test case, not
    inherited from the host environment or inferred from a model response.
    """
    return (isinstance(context, dict)
            and context.get('randomized_acceptance_run') is True
            and context.get('acceptance_execution_mode') == 'full_delivery')


def is_first_pass_acceptance(context) -> bool:
    """Keep fast synthetic benchmarks separate from production-like delivery."""
    return (isinstance(context, dict)
            and context.get('randomized_acceptance_run') is True
            and not is_full_delivery_acceptance(context))


def is_targeted_recovery(context) -> bool:
    """A bounded correction is a different phase from initial sample generation."""
    return isinstance(context,dict) and context.get('task_mode')=='final_review_repair'


def initial_generation_only(context) -> bool:
    return is_first_pass_acceptance(context) and not is_targeted_recovery(context)


def attempt_limit(context, production_limit: int) -> int:
    """Measure the first generated result in acceptance; preserve production recovery."""
    if type(production_limit) is not int or production_limit < 1:
        raise ValueError('Лимит производственных попыток должен быть положительным целым числом')
    return 1 if is_first_pass_acceptance(context) else production_limit


def final_repair_limit(context, production_limit: int) -> int:
    """Separate first-draft measurement from bounded delivery recovery.

    Synthetic workflows keep one initial generation and never restart the task.
    An explicit host mode permits one correction after a complete, actionable
    review. The first-pass verdict remains separate from final acceptance.
    """
    if type(production_limit) is not int or production_limit < 0:
        raise ValueError('Лимит итоговой правки должен быть неотрицательным целым числом')
    if not is_first_pass_acceptance(context):
        return production_limit
    return min(1, production_limit) if context.get('acceptance_execution_mode') == 'bounded_workflow' else 0


def workflow_outcome_metrics(workflow_type, events, review=None, *, execution_mode='bounded_workflow'):
    """Report initial quality independently from a repaired delivery verdict."""
    if execution_mode not in ('bounded_workflow', 'full_delivery'):
        raise ValueError('Unknown acceptance execution mode')
    standalone = workflow_type == 'standalone_post'
    first_pass = False
    repaired = bool((review or {}).get('repair_applied')) if standalone else False
    for event in events:
        if not isinstance(event, dict):
            continue
        if not standalone and event.get('stage') == 'autonomous_retry':
            repaired = True
        if standalone and event.get('stage') == 'text_first_pass_review_result':
            first_pass = event.get('passed') is True
            break
        if not standalone and event.get('stage') == 'final_review_result' and event.get('attempt') == 1:
            first_pass = event.get('status') == 'PASS'
    return {'execution_mode': execution_mode,
            'first_pass_status': 'PASS' if first_pass else 'FAIL',
            'targeted_repair_applied': repaired}
