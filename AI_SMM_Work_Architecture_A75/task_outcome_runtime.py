"""Qt-independent transaction owner for unfinished tasks and their responses."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from sqlite_transaction_core import own
from task_artifacts import store_pending_task

@dataclass(frozen=True)
class TaskOutcomeReceipt:
    project_id: str
    message_id: int
    task_id: str | None
    state: str

class TaskOutcomeRuntime:
    def __init__(self, db):
        if not isinstance(db,sqlite3.Connection):
            raise TypeError('A shared SQLite connection is required')
        self.db=db

    def _check(self,project_id,answer):
        if not isinstance(project_id,str) or not project_id.strip():
            raise ValueError('Missing project identity')
        if not isinstance(answer,str) or not answer.strip():
            raise ValueError('Empty task response')
        if not self.db.execute('SELECT 1 FROM projects WHERE id=?',(project_id,)).fetchone():
            raise ValueError('Unknown project')

    def park_in_transaction(self, project_id, request, question, *, revision_id=None,
                            task_id=None, force_new=False):
        """Persist an unfinished assignment under the ongoing result commit.

        A partial document cannot cancel or complete the original request.
        This operation makes no independent transaction or user-facing reply.
        """
        if not self.db.in_transaction:
            raise RuntimeError('A caller-owned task transaction is required')
        if not isinstance(project_id,str) or not project_id:
            raise ValueError('Missing project identity')
        if not isinstance(request,str) or not request.strip() or not isinstance(question,str) or not question.strip():
            raise ValueError('Missing unfinished task request or question')
        if not self.db.execute('SELECT 1 FROM projects WHERE id=?',(project_id,)).fetchone():
            raise ValueError('Unknown project')
        return store_pending_task(self.db,project_id,request,question,revision_id,
            task_id=task_id,force_new=force_new)

    def record_answer_in_transaction(self,project_id,answer,*,timestamp=None):
        """Append a response atomically with the owning result or dialogue UoW."""
        if not self.db.in_transaction:
            raise RuntimeError('A caller-owned transaction is required')
        self._check(project_id,answer)
        msg=self.db.execute('INSERT INTO messages(project_id,role,content,created_at) VALUES(?,?,?,?)',
            (project_id,'assistant',answer,timestamp or datetime.now(timezone.utc).isoformat())).lastrowid
        return TaskOutcomeReceipt(project_id,msg,None,'answer')

    def park_and_reply_in_transaction(self,project_id,request,question,answer,*,revision_id=None,timestamp=None,task_id=None,force_new=False):
        """Store pending task and response under one existing result transaction."""
        if not self.db.in_transaction:
            raise RuntimeError('A caller-owned transaction is required')
        if not isinstance(request,str) or not request.strip() or not isinstance(question,str) or not question.strip():
            raise ValueError('Missing pending task request or question')
        self._check(project_id,answer)
        task=store_pending_task(self.db,project_id,request,question,revision_id,task_id=task_id,force_new=force_new)
        msg=self.db.execute('INSERT INTO messages(project_id,role,content,created_at) VALUES(?,?,?,?)',
            (project_id,'assistant',answer,timestamp or datetime.now(timezone.utc).isoformat())).lastrowid
        return TaskOutcomeReceipt(project_id,msg,task,'pending')

    def record_answer(self,project_id,answer,*,timestamp=None):
        if self.db.in_transaction:
            raise RuntimeError('Another operation owns the transaction')
        with own(self.db):
            return self.record_answer_in_transaction(project_id,answer,timestamp=timestamp)

    def park_and_reply(self,project_id,request,question,answer,*,revision_id=None,timestamp=None,task_id=None,force_new=False):
        if self.db.in_transaction:
            raise RuntimeError('Another operation owns the transaction')
        with own(self.db):
            return self.park_and_reply_in_transaction(project_id,request,question,answer,
                revision_id=revision_id,timestamp=timestamp,task_id=task_id,force_new=force_new)
