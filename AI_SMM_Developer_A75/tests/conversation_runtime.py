"""Durable project-scoped conversation intake, independent of Qt and AI providers.

The message, its durable intake receipt, and any transition are committed as a
single operation. Every call is explicitly project/turn-bound; selection in the
UI is never an authority. Incomplete turns remain queryable after a crash.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from uuid import uuid4

from sqlite_transaction_core import own
from task_intake import ensure_intake, record_intake, update_intake


def _now():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class IntakeReceipt:
    project_id: str
    message_id: int
    dialogue_turn_id: str
    intake_id: str


class ConversationRuntime:
    def __init__(self, db: sqlite3.Connection):
        if not isinstance(db, sqlite3.Connection):
            raise TypeError('Conversation runtime requires SQLite')
        self.db = db

    def accept(self, project_id: str, request: str, *, turn_id=None, timestamp=None):
        """Durably accept an instruction before spawning any worker."""
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError('Missing project identity')
        if not isinstance(request, str) or not request.strip():
            raise ValueError('Empty instruction')
        turn_id = turn_id if turn_id is not None else uuid4().hex
        if not isinstance(turn_id, str) or not turn_id.strip():
            raise ValueError('Missing dialogue turn identity')
        with own(self.db):
            ensure_intake(self.db)
            existing = self.db.execute('''SELECT intake_id,message_id,original_request
                FROM ai_task_intake WHERE dialogue_turn_id=?''', (turn_id,)).fetchone()
            if existing:
                if existing[2] != request or self.db.execute('''SELECT project_id
                    FROM ai_task_intake WHERE dialogue_turn_id=?''',(turn_id,)).fetchone()[0] != project_id:
                    raise ValueError('Dialogue turn identity collision')
                return IntakeReceipt(project_id, existing[1], turn_id, existing[0])
            if not self.db.execute('SELECT 1 FROM projects WHERE id=?',(project_id,)).fetchone():
                raise ValueError('Unknown project')
            message = self.db.execute('''INSERT INTO messages
                (project_id,role,content,created_at) VALUES(?,?,?,?)''',
                (project_id,'user',request,timestamp or _now()))
            intake_id = record_intake(self.db,project_id,message.lastrowid,turn_id,request)
            return IntakeReceipt(project_id,message.lastrowid,turn_id,intake_id)

    def snapshot(self, project_id: str, *, selected_id=None, current_message_id=None, ui_context=None):
        """Read a consistent dialogue view inside one application-owned transaction.

        Lazy legacy-schema migration belongs to the core, never a Qt callback.
        The caller supplies project identity explicitly, including for late turns.
        """
        from dialogue_runtime import dialogue_snapshot
        with own(self.db):
            return dialogue_snapshot(self.db,project_id,selected_id=selected_id,
                current_message_id=current_message_id,ui_context=ui_context)

    def transition(self, project_id: str, turn_id: str, state: str):
        """State transition never affects a different project or a finished turn."""
        with own(self.db):
            return update_intake(self.db,project_id,turn_id,state)

    def finish_in_transaction(self, project_id: str, turn_id: str):
        """Finish within the transaction that persists the action's side effects.

        The caller MUST own the SQLite transaction. This prevents a turn being
        marked resolved while its corresponding project mutation rolls back.
        """
        if not self.db.in_transaction:
            raise RuntimeError('Intake completion requires the action transaction')
        return update_intake(self.db,project_id,turn_id,'resolved')

    def link_execution_in_transaction(self, project_id: str, turn_id: str, task_id: str):
        from dialogue_task_links import link_execution_in_transaction
        return link_execution_in_transaction(self.db,project_id,turn_id,task_id)

    @staticmethod
    def finish_linked_in_transaction(db, project_id: str, task_id: str):
        from dialogue_task_links import finish_linked_in_transaction
        return finish_linked_in_transaction(db,project_id,task_id)

    def publish_reply(self, project_id: str, turn_id: str, content: str, *, timestamp=None, clarification=None):
        '''Persist a dialogue response and close its intake in ONE durable commit.

        A retry after commit returns the same message id; a crash before commit
        leaves both the response and intake unchanged. Foreign turns fail closed.
        '''
        if not isinstance(content, str) or not content.strip():
            raise ValueError('Empty response')
        if clarification is not None and (not isinstance(clarification, tuple) or len(clarification) != 2 or not all(isinstance(v,str) for v in clarification)):
            raise ValueError('Invalid clarification payload')
        with own(self.db):
            ensure_intake(self.db)
            row = self.db.execute('''SELECT intake_id,state FROM ai_task_intake
                WHERE project_id=? AND dialogue_turn_id=?''',(project_id,turn_id)).fetchone()
            if row is None:
                raise ValueError('Unknown dialogue turn for project')
            self.db.execute('''CREATE TABLE IF NOT EXISTS ai_dialogue_publications (
                intake_id TEXT PRIMARY KEY, message_id INTEGER NOT NULL UNIQUE,
                content TEXT NOT NULL, FOREIGN KEY(intake_id) REFERENCES ai_task_intake(intake_id),
                FOREIGN KEY(message_id) REFERENCES messages(id))''')
            prior=self.db.execute('''SELECT message_id,content FROM ai_dialogue_publications
                WHERE intake_id=?''',(row[0],)).fetchone()
            if prior is not None:
                if prior[1] != content or row[1] != 'resolved':
                    raise ValueError('Conflicting dialogue publication')
                return prior[0]
            if row[1] != 'resolving':
                raise ValueError('Dialogue is not ready to publish')
            if clarification is not None:
                from question_registry import replace_question
                replace_question(self.db,project_id,clarification[0],content,clarification[1])
            cursor=self.db.execute('''INSERT INTO messages(project_id,role,content,created_at)
                VALUES(?,?,?,?)''',(project_id,'assistant',content,timestamp or _now()))
            self.db.execute('''INSERT INTO ai_dialogue_publications(intake_id,message_id,content)
                VALUES(?,?,?)''',(row[0],cursor.lastrowid,content))
            self.finish_in_transaction(project_id,turn_id)
            return cursor.lastrowid

    def reply(self, project_id: str, content: str, *, timestamp=None):
        """Store a project-bound response without touching the active UI project."""
        if not isinstance(content,str):
            raise ValueError('Invalid reply')
        with own(self.db):
            if not self.db.execute('SELECT 1 FROM projects WHERE id=?',(project_id,)).fetchone():
                raise ValueError('Unknown project')
            return self.db.execute('''INSERT INTO messages
                (project_id,role,content,created_at) VALUES(?,?,?,?)''',
                (project_id,'assistant',content,timestamp or _now())).lastrowid

    def unresolved(self, *, project_id=None, limit=100):
        """Read-only crash-recovery inventory. Never silently discard a request."""
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError('Invalid recovery batch size')
        query = '''SELECT project_id,dialogue_turn_id,original_request,state,message_id
            FROM ai_task_intake WHERE state IN ('received','resolving')'''
        params = []
        if project_id is not None:
            query += ' AND project_id=?'
            params.append(project_id)
        query += ' ORDER BY created_at,intake_id LIMIT ?'
        params.append(limit)
        # A missing table represents a legacy database with no accepted intakes.
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_task_intake'").fetchone():
            return ()
        return tuple(self.db.execute(query,params).fetchall())
