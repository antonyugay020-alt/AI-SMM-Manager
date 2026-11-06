'''Single Qt-independent transaction boundary for task state transitions.

Every mutation of the active task pointer and its journal is atomic. Callers
may already own a transaction; SAVEPOINT preserves their enclosing decision.
Nothing in this service commits another subsystem's transaction.
'''
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
from uuid import uuid4

from task_registry import activate as _activate, finish as _finish, cancel_paused as _cancel_paused


@contextmanager
def _atomic(db):
    from sqlite_transaction_core import compose
    with compose(db, 'task_lifecycle'):
        yield


def _log(db, project_id, task_id, action, before, after):
    db.execute('''CREATE TABLE IF NOT EXISTS ai_task_transitions (
        event_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
        action TEXT NOT NULL, status_before TEXT, status_after TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE)''')
    db.execute('''CREATE INDEX IF NOT EXISTS ai_task_transitions_by_task
        ON ai_task_transitions(project_id,task_id,created_at)''')
    db.execute('INSERT INTO ai_task_transitions VALUES(?,?,?,?,?,?,?)',
        (uuid4().hex,project_id,task_id,action,before,after,
         datetime.now(timezone.utc).isoformat()))


class TaskLifecycle:
    def __init__(self, db):
        self.db = db

    def activate(self, project_id, request, question, revision_id=None, *, task_id=None, force_new=False):
        with _atomic(self.db):
            old = self.db.execute('SELECT task_id FROM ai_task_registry WHERE project_id=? AND status=?',
                                  (project_id,'active')).fetchone() if self._registry_exists() else None
            ident = _activate(self.db, project_id, request, question, revision_id,
                              task_id=task_id, force_new=force_new)
            if old and old[0] != ident:
                from execution_coordinator import ExecutionCoordinator
                ExecutionCoordinator(self.db).interrupt(project_id,old[0])
                _log(self.db,project_id,old[0],'pause','active','paused')
            if not old or old[0] != ident:
                _log(self.db,project_id,ident,'activate','paused' if task_id else None,'active')
            return ident

    def finish(self, project_id, request, *, task_id=None, status='completed'):
        with _atomic(self.db):
            ident = _finish(self.db,project_id,request,task_id=task_id,status=status)
            if status=='cancelled':
                from execution_coordinator import ExecutionCoordinator
                ExecutionCoordinator(self.db).interrupt(project_id,ident)
            _log(self.db,project_id,ident,'finish' if status=='completed' else 'cancel','active',status)
            from dialogue_task_links import finish_linked_in_transaction
            finish_linked_in_transaction(self.db,project_id,ident)
            return ident

    def cancel_paused(self, project_id, task_id, request):
        with _atomic(self.db):
            _cancel_paused(self.db,project_id,task_id,request)
            from execution_coordinator import ExecutionCoordinator
            ExecutionCoordinator(self.db).interrupt(project_id,task_id)
            _log(self.db,project_id,task_id,'cancel','paused','cancelled')
            from dialogue_task_links import finish_linked_in_transaction
            finish_linked_in_transaction(self.db,project_id,task_id)

    def begin_execution(self, project_id, task_id):
        from execution_coordinator import ExecutionCoordinator
        return ExecutionCoordinator(self.db).begin(project_id, task_id)

    def recover_execution(self, *, project_id=None):
        from execution_coordinator import ExecutionCoordinator
        return ExecutionCoordinator(self.db).recover(project_id=project_id)

    def _registry_exists(self):
        return bool(self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_task_registry'").fetchone())
