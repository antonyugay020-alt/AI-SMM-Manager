"""Single application/acceptance entry for requested text work.

The same immutable task/source contract reaches generation, editing and final
review. Existing artifact executors retain their checkpoints and acceptance
gates. This service owns routing, not generated prose or media side effects.
"""
from text_task_contract import TextTaskContract, bind_creative_permission


def run_text_task(request, context, *, model='qwen3:8b', review_model=None,
                  progress=None, planner=None, compound_runner=None, quality_cycle=None,
                  on_initial_response=None):
    from task_semantics import semantic_task
    with semantic_task(context):
        return _run_text_task(request,context,model=model,review_model=review_model,progress=progress,
            planner=planner,compound_runner=compound_runner,quality_cycle=quality_cycle,
            on_initial_response=on_initial_response)


def _run_text_task(request, context, *, model, review_model, progress, planner,
                   compound_runner, quality_cycle, on_initial_response):
    from agent_runtime import plan_task
    from compound_tasks import is_compound_request
    from production_pipeline import run_document_task
    from generic_text_review import run_quality_cycle
    if context.get('task_mode')=='resume_missing':
        raise ValueError('Не найдено исходное задание для продолжения; генерация не начата')
    contract = TextTaskContract.create(request, context)
    state = contract.bind(context)
    state['substance_review_required']=True
    if progress:
        progress({'stage': 'text_task_contract_bound', **contract.metadata()})
    semantic=context.get('semantic_authorized') is True
    kind=context.get('semantic_execution_kind')
    if semantic and state.get('semantic_requested_media') is True:
        raise ValueError('Текстовый исполнитель не создаёт готовые медиафайлы; требуется уточнение результата до генерации')
    if (kind=='bundle') if semantic else is_compound_request(request):
        result = (compound_runner or run_document_task)(request, state, model=model,
                    review_model=review_model, progress=progress)
    else:
        def report(stage, **details):
            if progress:
                progress({'stage': stage, **details})
        from contextlib import ExitStack
        with ExitStack() as scopes:
            # Establish reviewer capacity before spending production author work.
            # Explicit injected planners/cycles remain offline test adapters.
            if planner is None and quality_cycle is None:
                from generic_text_review import select_reviewer_models, acceptance_model_pool
                if progress:progress({'stage':'reviewer_qualification_started'})
                _,eligible=select_reviewer_models(model,reserve_reviser=True,return_pool=True)
                scopes.enter_context(acceptance_model_pool(eligible))
            pending = state.get('pending_text_candidate')
            if state.get('task_mode') == 'resume' and isinstance(pending, dict) and pending.get('draft'):
                import hashlib
                expected=hashlib.sha256(request.encode('utf-8')).hexdigest()
                if pending.get('request_sha256') != expected:
                    raise ValueError('Сохранённый черновик принадлежит другому заданию')
                proposal=pending.get('content_contract') or (pending.get('quality_review') or {}).get('content_contract')
                initial = {'action': 'write', 'instruction': request, 'answer': str(pending['draft']),
                           'content_contract':proposal}
            else:
                from agent_runtime import write_requested_artifact, route_text_intent
                if planner is not None:
                    initial=planner(request,state,model=model,report=report)
                elif semantic:
                    initial=write_requested_artifact(request,state,model=model,report=report)
                else:
                    decision=route_text_intent(request,state,model=model,report=report)
                    if decision.get('action')=='write':
                        report('text_author_started',model=model)
                        initial=write_requested_artifact(request,state,model=model,report=report)
                    else:
                        initial=decision
            if on_initial_response:
                on_initial_response(initial)
            if isinstance(initial,dict) and initial.get('action')=='write':
                permission=bind_creative_permission(request,initial.get('content_contract'))
                if permission:
                    state['proposed_content_contract']=permission
            result = (quality_cycle or run_quality_cycle)(request, initial, state, model=model, report=report)
    if not isinstance(result, dict):
        raise ValueError('Текстовый executor не вернул результат по контракту')
    return {**result, 'instruction':contract.request,'execution_contract': contract.metadata()}
