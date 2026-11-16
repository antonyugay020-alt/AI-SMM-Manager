"""Worker-local cancellation checked at every shared model-call boundary."""
from contextlib import contextmanager,closing
from contextvars import ContextVar
from pathlib import Path
import sqlite3

_CHECK = ContextVar('ai_smm_model_cancellation',default=None)


class TaskCancelled(Exception):
    pass


def check_cancelled():
    callback=_CHECK.get()
    if callback is not None:callback()


@contextmanager
def cancellation_scope(callback):
    token=_CHECK.set(callback)
    try:
        check_cancelled()
        yield
        check_cancelled()
    finally:_CHECK.reset(token)


def worker_check(worker):
    """Read the durable attempt on a worker-owned connection; never touch Qt SQL."""
    def check():
        if worker.isInterruptionRequested():raise TaskCancelled('Задание остановлено пользователем')
        attempt=getattr(worker,'execution_attempt',None)
        database=getattr(worker,'execution_database_path',None)
        if attempt is not None and database:
            with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=rw',uri=True,timeout=30)) as db:
                row=db.execute('''SELECT a.state,t.status FROM execution_attempts a
                    JOIN ai_task_registry t ON a.task_id=t.task_id AND a.project_id=t.project_id
                    WHERE a.attempt_id=? AND a.project_id=? AND a.task_id=?''',
                    (attempt.attempt_id,attempt.project_id,attempt.task_id)).fetchone()
            if row!=('running','active'):raise TaskCancelled('Execution attempt lost ownership')
    return check
