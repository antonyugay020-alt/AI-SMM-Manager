"""Transactional boundary between AI worker output and project persistence.

This module deliberately imports no Qt and does not read the current GUI context
on its own. All ownership checks use the task origin captured by the host.
Success effects run only after SQLite has durably accepted the result. The
host remains responsible for rendering and saving individual artifacts.
"""
import sqlite3


def apply_project_result(view, task):
    claim = getattr(view, 'task_execution_claim', None)
    origin = getattr(view, 'task_project_id', None)
    if not origin or origin != view.current_project:
        raise ValueError('Исходный проект задания не подтверждён или изменился')
    if not isinstance(task, dict) or task.get('instruction') != getattr(view, 'task_instruction', None):
        raise ValueError('Результат не принадлежит исходному заданию')
    if claim is not None and getattr(claim, 'project_id', None) != origin:
        raise ValueError('Проект подтверждённого задания изменился')
    if claim is None and (getattr(view, 'task_semantic_kind', None)
                          or task.get('action') in ('compound_documents', 'compound_clarify')):
        raise ValueError('Для составного/семантического задания отсутствует подтверждённый контракт')

    from dialogue_execution import result_transaction, validate_live_plan
    from execution_coordinator import ExecutionCoordinator
    attempt=getattr(view,'task_attempt',None)
    if claim is not None and attempt is None:
        raise ValueError('Нет подтверждённой попытки исполнения')
    if attempt is not None:
        if attempt.project_id != origin:
            raise ValueError('Попытка принадлежит другому проекту')
        ExecutionCoordinator(view.db).verify(attempt)
    deferred = []
    view._result_effects = deferred
    try:
        with result_transaction(view.db):
            if claim is not None:
                validate_live_plan(view.db, origin, claim)
            view._apply_task_result_body(task)
            if attempt is not None:
                ExecutionCoordinator(view.db).settle(attempt,success=True)
    except BaseException:
        # The transaction has rolled back. Refresh transient UI pending
        # state from the durable source rather than discarding a preexisting
        # paused task or trusting an in-memory mutation made before failure.
        try:
            row = view.db.execute('SELECT request,question FROM ai_pending_tasks WHERE project_id=?',
                                  (origin,)).fetchone()
            pending = getattr(view, '_pending_ai_tasks', None)
            if isinstance(pending, dict):
                if row:
                    pending[origin] = {'request': row[0], 'question': row[1]}
                else:
                    pending.pop(origin, None)
        except (sqlite3.Error, AttributeError):
            pass
        try:
            view.load_manager_documents()
        except (sqlite3.Error, AttributeError):
            pass
        raise
    finally:
        view._result_effects = None
    if attempt is not None:
        view.task_attempt=None
    # The SQLite commit cannot be undone by a later UI/diagnostic callback.
    # A failed notification must not reclassify a persisted result as failed.
    # Durable file completion is separately handled by ai_file_effect_outbox.
    errors = []
    for callback in deferred:
        try:
            callback()
        except Exception as exc:
            errors.append({'effect':getattr(callback, '__name__', type(callback).__name__),
                           'error':type(exc).__name__ + ': ' + str(exc)[:250]})
    if errors:
        view._result_post_commit_errors = errors
        try:
            view.record_project_diagnostic('post_commit_effects_failed',
                                           {'errors':errors,'result_already_committed':True}, origin)
        except Exception:
            pass
    else:
        view._result_post_commit_errors = []

