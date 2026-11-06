"""Task-level bounded recovery for a compound request, independent of industry.

This module never writes accepted GUI documents or alters another project.
The production executor owns publication checkpoints; the caller persists
verified documents only once the final result is returned.
"""
from __future__ import annotations
import inspect
from completion_gate import can_close_task


def _verified_output(result):
    if not isinstance(result, dict) or result.get('action') != 'compound_documents':
        return False
    docs = result.get('documents')
    if not isinstance(docs, list):
        return False
    kinds = {item.get('kind') for item in docs if isinstance(item, dict) and item.get('valid') is True}
    return can_close_task(kinds, result.get('content_delivery'), require_editor=False)


def _strategy_for_retry(result):
    """Return a usable in-memory strategy without claiming it exists on disk."""
    if not isinstance(result, dict):
        return []
    docs = result.get('documents')
    if not isinstance(docs, list):
        return []
    for doc in docs:
        if (isinstance(doc,dict) and doc.get('kind')=='strategy' and doc.get('valid') is True
                and isinstance(doc.get('content'),str) and doc['content'].strip()):
            return [{'kind':'strategy', 'content':doc['content'],
                     'already_saved':doc.get('already_saved') is True}]
    return []


def invoke_executor(execute, request, context, *, progress=None, model=None):
    """One adapter boundary for initial generation and every targeted revision."""
    kwargs={'progress':progress}
    try:
        parameters=inspect.signature(execute).parameters
        variadic=any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())
        if model is not None and ('model' in parameters or variadic):
            kwargs['model']=model
        if 'progress' not in parameters and not variadic:
            kwargs.pop('progress',None)
    except (TypeError,ValueError):
        pass
    return execute(str(request),context,**kwargs)


def run_compound_with_retry(execute, request, context, progress=None, max_attempts=2, model=None):
    """Make one bounded autonomous recovery; no success without verified delivery.

    A second execution inherits exactly the original request and task-scoped
    checkpoints. A strategy only produced in memory must not be misrepresented
    as already persisted. All accepted publication bytes remain authoritative.
    """
    if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
        raise ValueError('Incorrect retry budget')
    original = str(request)
    state = dict(context)
    result = None
    for attempt in range(max_attempts):
        if attempt:
            state['task_mode'] = 'resume'
            if progress:
                progress({'stage':'autonomous_retry', 'attempt':attempt+1,
                          'reason':'Previous execution did not satisfy task-wide completion gate'})
        result = invoke_executor(execute,original,state,progress=progress,model=model)
        if _verified_output(result):
            return result
        # Infrastructure failure is not failed content. Keep accepted bytes
        # intact and requalify review capacity before the next continuation.
        assessment=(result.get('content_delivery') or {}).get('workflow_assessment') if isinstance(result,dict) else None
        if isinstance(assessment,dict) and assessment.get('state')=='blocked_review':
            return result
        if isinstance(result,dict) and result.get('failure_code')=='author_execution_aborted':
            # The case deadline is terminal. Retain any persisted checkpoints
            # for a later user-controlled resume rather than replaying the task.
            return result
        if isinstance(result,dict) and result.get('failure_code')=='independent_review_unavailable':
            # Review infrastructure failure is not an instruction to mutate or
            # regenerate the document. Preserve the checkpoint for a later
            # review attempt against identical content.
            return result
        if isinstance(result,dict):
            from workflow_recovery import verified_failures
            editor=(result.get('content_delivery') or {}).get('independent_editor')
            if verified_failures(result) or (isinstance(editor,dict)
                    and editor.get('status')=='BLOCKED' and editor.get('review_state')=='rejected'
                    and editor.get('issues')):
                # Concrete stage evidence belongs to the single bounded
                # correction loop. Do not first replay the whole task here.
                return result
        if attempt + 1 < max_attempts:
            problems=[]
            for item in (result.get('documents') if isinstance(result,dict) else []) or []:
                if isinstance(item,dict) and item.get('valid') is not True and item.get('reason'):
                    problems.append(str(item['reason'])[:600])
            if isinstance(result,dict) and result.get('error'):
                problems.append(str(result['error'])[:600])
            if problems:
                state['previous_rejection_issues']='; '.join(problems)[:1000]
            # Do not accept a generated strategy as saved. Preserve an actually
            # saved strategy's receipt from the UI when this is a true resume.
            ready = _strategy_for_retry(result)
            if ready:
                state['resume_documents'] = ready
            # Otherwise retain the already-verified strategy supplied by GUI,
            # if present, rather than regenerating it blindly.
    return result
