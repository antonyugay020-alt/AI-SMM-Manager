"""Durable execution attempts and fencing for independent project tasks.

SQLite stores ownership. Qt workers are transient executors, not authorities.
An attempt token prevents a late worker from applying a result after replacement,
project switch, cancellation or restart. A process restart invalidates running
attempts; its tasks stay active for an explicit retry.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4
import sqlite3


def _now():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    project_id: str
    task_id: str


class ExecutionCoordinator:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def _ensure_base(self):
        """Install durable storage without assuming legacy worker rows are unique.

        A pre-constraint database can contain multiple stale running attempts
        for one project. Restart recovery must fence them before UNIQUE indexes
        are installed; ordinary worker operations must never bypass the indexes.
        """
        self.db.execute('''CREATE TABLE IF NOT EXISTS execution_attempts (
          attempt_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
          state TEXT NOT NULL CHECK(state IN ('running','accepted','failed','interrupted')),
          started_at TEXT NOT NULL, finished_at TEXT,
          FOREIGN KEY(task_id) REFERENCES ai_task_registry(task_id) ON DELETE CASCADE,
          FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE)''')
        self.db.execute('''CREATE INDEX IF NOT EXISTS execution_project_states
          ON execution_attempts(project_id,state)''')

    def _ensure_constraints(self):
        """Enforce both worker ownership invariants at the database boundary."""
        self.db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS execution_one_running_task
          ON execution_attempts(task_id) WHERE state='running' ''')
        # A pre-insert SELECT cannot replace a constraint across connections.
        self.db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS execution_one_running_project
          ON execution_attempts(project_id) WHERE state='running' ''')

    def _ensure(self):
        """Normal execution paths fail closed on unmigrated legacy duplicates."""
        self._ensure_base()
        self._ensure_constraints()

    def migrate_legacy_on_bootstrap(self):
        """Install constraints safely without preempting normal app recovery.

        Existing current-schema workers are fenced later by the application's
        cross-service recovery transaction (execution + batch together). Only
        a pre-constraint legacy schema needs immediate global fencing before
        the missing UNIQUE indexes can be created.
        """
        from sqlite_transaction_core import compose
        with compose(self.db, 'execution_coordinator_migration'):
            self._ensure_base()
            names = {row[0] for row in self.db.execute(
                """SELECT name FROM sqlite_master WHERE type='index'
                   AND name IN ('execution_one_running_task',
                                'execution_one_running_project')""")}
            if names == {'execution_one_running_task', 'execution_one_running_project'}:
                self._ensure_constraints()
                return 0
            return self.recover()

    def _transaction(self, action):
        from sqlite_transaction_core import compose
        with compose(self.db, 'execution_coordinator'):
            self._ensure()
            return action()

    def begin(self, project_id, task_id):
        def action():
            row=self.db.execute('''SELECT status FROM ai_task_registry
                    WHERE project_id=? AND task_id=?''',(project_id,task_id)).fetchone()
            if not row or row[0]!='active':
                raise ValueError('Task is not active in the claimed project')
            if self.db.execute("""SELECT 1 FROM execution_attempts
                   WHERE project_id=? AND state='running' LIMIT 1""",
                   (project_id,)).fetchone():
                raise ValueError('Project already has an executing task')
            ident=uuid4().hex
            self.db.execute('''INSERT INTO execution_attempts
                (attempt_id,project_id,task_id,state,started_at)
                VALUES(?,?,?,'running',?)''',(ident,project_id,task_id,_now()))
            return Attempt(ident,project_id,task_id)
        return self._transaction(action)

    def interrupt(self, project_id, task_id):
        """Fence attempts for a paused or cancelled task."""
        def action():
            return self.db.execute("""UPDATE execution_attempts
                SET state='interrupted',finished_at=? WHERE project_id=?
                AND task_id=? AND state='running' """,
                (_now(),project_id,task_id)).rowcount
        return self._transaction(action)

    def verify(self, attempt: Attempt):
        row=self.db.execute('''SELECT a.state,t.status FROM execution_attempts a
            JOIN ai_task_registry t ON t.task_id=a.task_id AND t.project_id=a.project_id
            WHERE a.attempt_id=? AND a.project_id=? AND a.task_id=?''',
            (attempt.attempt_id,attempt.project_id,attempt.task_id)).fetchone()
        if row!=('running','active'):
            raise ValueError('Execution attempt lost ownership or was superseded')

    def settle(self, attempt: Attempt, *, success):
        def action():
            self.verify(attempt)
            self.db.execute('''UPDATE execution_attempts SET state=?,finished_at=?
                 WHERE attempt_id=? AND state='running' ''',
                 ('accepted' if success else 'failed',_now(),attempt.attempt_id))
        return self._transaction(action)

    def settle_after_task_completion(self, attempt: Attempt):
        """Mark a verified attempt accepted inside the same transaction that closes its task.

        This is deliberately separate from settle(), which requires an active task.
        The caller must verify BEFORE closing the task and must own the savepoint.
        """
        changed=self.db.execute('''UPDATE execution_attempts SET state='accepted',finished_at=?
            WHERE attempt_id=? AND project_id=? AND task_id=? AND state='running' ''',
            (_now(),attempt.attempt_id,attempt.project_id,attempt.task_id)).rowcount
        if changed != 1:
            raise ValueError('Execution attempt already settled or lost ownership')

    def recover(self, *, project_id=None):
        """Fence stale workers and restore both unique indexes atomically.

        On startup, global recovery migrates legacy databases with duplicate
        running rows before constraints are created. Scoped recovery preserves
        other projects; if their unmigrated rows violate an index, it fails and
        rolls back instead of silently fencing another project's workers.
        Task statuses, active pointers and checkpoints are never rewritten.
        """
        from sqlite_transaction_core import compose
        with compose(self.db, 'execution_coordinator_recover'):
            self._ensure_base()
            if project_id is None:
                changed = self.db.execute('''UPDATE execution_attempts
                    SET state='interrupted', finished_at=? WHERE state='running' ''',
                    (_now(),)).rowcount
            else:
                changed = self.db.execute('''UPDATE execution_attempts
                    SET state='interrupted', finished_at=?
                    WHERE state='running' AND project_id=?''',
                    (_now(), project_id)).rowcount
            self._ensure_constraints()
            return changed
