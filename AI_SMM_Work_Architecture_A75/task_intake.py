"""Durable receipt of an incoming user instruction *before* any AI worker starts.

Intake is not the ai_pending_tasks execution pointer: a dialogue may legitimately
be a question, a read-only action, or a new task. Never overwrite another
instruction when the same wording is submitted twice.
"""
from __future__ import annotations
from datetime import datetime, timezone
from uuid import uuid4


def ensure_intake(db):
    db.execute('''CREATE TABLE IF NOT EXISTS ai_task_intake (
        intake_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
        message_id INTEGER NOT NULL, dialogue_turn_id TEXT NOT NULL UNIQUE,
        original_request TEXT NOT NULL, state TEXT NOT NULL
            CHECK(state IN ('received','resolving','resolved','failed')),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE)''')
    db.execute('CREATE INDEX IF NOT EXISTS ai_task_intake_project_state '
               'ON ai_task_intake(project_id,state,created_at)')


def record_intake(db, project_id, message_id, turn_id, request):
    """Caller commits the message and receipt in the SAME SQLite transaction."""
    if not all(isinstance(x,str) and x.strip() for x in (project_id,turn_id,request)):
        raise ValueError('Intake requires project, dialogue turn and original request')
    if type(message_id) is not int or message_id < 1:
        raise ValueError('Intake requires an already stored message')
    ensure_intake(db)
    row=db.execute('SELECT role,content FROM messages WHERE id=? AND project_id=?',
                   (message_id,project_id)).fetchone()
    if row is None or row[0]!='user' or row[1]!=request:
        raise ValueError('Intake is not bound to its original user message')
    receipt=uuid4().hex
    at=datetime.now(timezone.utc).isoformat()
    db.execute('''INSERT INTO ai_task_intake
        (intake_id,project_id,message_id,dialogue_turn_id,original_request,state,created_at,updated_at)
        VALUES (?,?,?,?,?,'received',?,?)''',
        (receipt,project_id,message_id,turn_id,request,at,at))
    return receipt


def update_intake(db, project_id, turn_id, state):
    """Apply an explicit, project-bound intake state transition.

    A decision by the model is *not* a completed action. The turn stays
    resolving until application side effects are durably accepted.
    Retries of an already recorded transition are idempotent; invalid
    or cross-project transitions fail closed.
    """
    if state not in ('resolving', 'resolved', 'failed'):
        raise ValueError('Unsupported intake transition')
    row = db.execute('SELECT state FROM ai_task_intake WHERE project_id=? AND dialogue_turn_id=?', (project_id, turn_id)).fetchone()
    if row is None:
        raise ValueError('Intake owner is missing')
    current = row[0]
    if current == state:
        return False
    allowed = {
        'received': ('resolving', 'failed'),
        'resolving': ('resolved', 'failed'),
        'resolved': (),
        'failed': (),
    }
    if state not in allowed.get(current, ()):
        raise ValueError('Illegal intake transition: ' + str(current) + ' -> ' + state)
    at = datetime.now(timezone.utc).isoformat()
    changed = db.execute('UPDATE ai_task_intake SET state=?,updated_at=? WHERE project_id=? AND dialogue_turn_id=? AND state=?',
        (state, at, project_id, turn_id, current)).rowcount
    if changed != 1:
        raise RuntimeError('Concurrent change to dialogue intake')
    return True


def recoverable_intakes(db, project_id):
    ensure_intake(db)
    return db.execute('''SELECT intake_id,original_request,state FROM ai_task_intake
        WHERE project_id=? AND state IN ('received','resolving','resolved','failed')
        ORDER BY created_at,intake_id''',(project_id,)).fetchall()
