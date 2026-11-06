"""Qt-independent authority for task lifecycle and asynchronous execution.

Registry changes, worker fencing, and transition journal use ONE SQLite savepoint.
The caller owns the final commit; an exception rolls back all three subsystems.
"""
from __future__ import annotations
from dataclasses import dataclass
from uuid import uuid4
from task_lifecycle import TaskLifecycle
from execution_coordinator import ExecutionCoordinator, Attempt

@dataclass(frozen=True)
class ExecutionReceipt:
    project_id: str
    task_id: str
    attempt: Attempt

class ExecutionRuntime:
    def __init__(self, db):
        self.db = db
        self.tasks = TaskLifecycle(db)
        self.attempts = ExecutionCoordinator(db)

    def _atomic(self, operation):
        from sqlite_transaction_core import compose
        with compose(self.db, 'execution_runtime'):
            return operation()

    def activate(self, project_id, request, question, revision_id=None, *, task_id=None, force_new=False):
        return self._atomic(lambda: self.tasks.activate(project_id,request,question,revision_id,task_id=task_id,force_new=force_new))

    def begin(self, project_id, task_id):
        def op():
            attempt = self.attempts.begin(project_id,task_id)
            return ExecutionReceipt(project_id,task_id,attempt)
        return self._atomic(op)

    def authorize_result(self, receipt: ExecutionReceipt, *, active_project_id: str):
        """Read-only authorization before applying ANY worker side effects.

        Completion itself re-checks ownership in the committing transaction;
        this gate prevents a stale callback from writing files or touching
        another project's visible state before that transaction starts.
        """
        if not isinstance(receipt, ExecutionReceipt):
            raise ValueError('Missing execution receipt')
        if (receipt.project_id != receipt.attempt.project_id or
                receipt.task_id != receipt.attempt.task_id):
            raise ValueError('Inconsistent execution identity')
        if receipt.project_id != active_project_id:
            raise ValueError('Worker result belongs to a different active project')
        self.attempts.verify(receipt.attempt)
        return True

    def finish(self, receipt: ExecutionReceipt, request: str):
        def op():
            if receipt.project_id != receipt.attempt.project_id or receipt.task_id != receipt.attempt.task_id:
                raise ValueError('Inconsistent execution identity')
            self.attempts.verify(receipt.attempt)
            ident = self.tasks.finish(receipt.project_id,request,task_id=receipt.task_id)
            self.attempts.settle_after_task_completion(receipt.attempt)
            return ident
        return self._atomic(op)

    def fail(self, receipt: ExecutionReceipt):
        return self._atomic(lambda:self.attempts.settle(receipt.attempt,success=False))

    def cancel(self, project_id, request, *, task_id):
        return self._atomic(lambda:self.tasks.finish(project_id,request,task_id=task_id,status='cancelled'))

    def cancel_paused(self, project_id, task_id, request):
        return self._atomic(lambda:self.tasks.cancel_paused(project_id,task_id,request))

    def recover(self, *, project_id=None):
        return self._atomic(lambda:self.attempts.recover(project_id=project_id))
