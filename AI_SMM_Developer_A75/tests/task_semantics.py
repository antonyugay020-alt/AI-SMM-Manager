"""Task-local model-resolved execution parameters, never language heuristics."""
from contextlib import contextmanager
from contextvars import ContextVar
import copy

_active=ContextVar('ai_smm_semantic_task',default=None)


def current_semantics():return _active.get()


def semantic_parameters(context):
    if context.get('semantic_authorized') is not True:return None
    kind=context.get('semantic_execution_kind')
    if kind not in ('bundle','strategy','calendar','plans','tasks','analytics'):
        raise ValueError('Некорректный тип текстового исполнителя')
    value={'kind':kind,'schedule':copy.deepcopy(context.get('semantic_schedule')),
           'requested_media':context.get('semantic_requested_media',False)}
    if type(value['requested_media']) is not bool:raise ValueError('Некорректный признак медиа-результатов')
    schedule=value['schedule']
    if kind=='bundle' and schedule is None:raise ValueError('AI не определил объём календаря')
    if schedule is not None:
        if (not isinstance(schedule,dict) or set(schedule)!={'weeks','per_week','total'}
                or any(type(v) is not int for v in schedule.values())
                or not 1<=schedule['weeks']<=26 or not 1<=schedule['per_week']<=7
                or schedule['total']!=schedule['weeks']*schedule['per_week']):
            raise ValueError('Несогласованные параметры календаря')
    return value


@contextmanager
def semantic_task(context):
    token=_active.set(semantic_parameters(context))
    try:yield
    finally:_active.reset(token)


def ai_content_review_active():
    """Typed workflow delegates content meaning to independent AI reviewers."""
    return _active.get() is not None
