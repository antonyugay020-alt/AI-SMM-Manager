"""Qt-free binding of dialogue receipts to durable task identities.

No dependency on presentation, semantic parsing, or task lifecycle: both
ConversationRuntime and TaskLifecycle participate in this shared boundary.
All mutations require the enclosing application SQLite transaction.
"""
from datetime import datetime, timezone
from task_intake import ensure_intake, update_intake

def _now():
    return datetime.now(timezone.utc).isoformat()

def ensure_task_links(db):
    """Durable identity graph shared by dialogue and execution lifecycles."""
    db.execute('''CREATE TABLE IF NOT EXISTS ai_dialogue_task_links (
        project_id TEXT NOT NULL, dialogue_turn_id TEXT NOT NULL,
        task_id TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(project_id, dialogue_turn_id),
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(dialogue_turn_id) REFERENCES ai_task_intake(dialogue_turn_id) ON DELETE CASCADE,
        FOREIGN KEY(task_id) REFERENCES ai_task_registry(task_id) ON DELETE CASCADE)''')
    db.execute('''CREATE INDEX IF NOT EXISTS ai_dialogue_task_links_task
        ON ai_dialogue_task_links(project_id,task_id)''')

def link_execution_in_transaction(db, project_id: str, turn_id: str, task_id: str):
    """Bind a turn to an execution in its originating SQLite commit.

    A launch is not completion. Many turns may point to one task,
    but a turn cannot be moved onto a different task.
    """
    if not db.in_transaction:
        raise RuntimeError('Dialogue/execution binding requires the owner transaction')
    ensure_intake(db)
    ensure_task_links(db)
    intake=db.execute('''SELECT state FROM ai_task_intake
        WHERE project_id=? AND dialogue_turn_id=?''',(project_id,turn_id)).fetchone()
    task=db.execute('''SELECT status FROM ai_task_registry
        WHERE project_id=? AND task_id=?''',(project_id,task_id)).fetchone()
    if intake != ('resolving',) or task != ('active',):
        raise ValueError('Dialogue and task must be active in the same project')
    prior=db.execute('''SELECT task_id FROM ai_dialogue_task_links
        WHERE project_id=? AND dialogue_turn_id=?''',(project_id,turn_id)).fetchone()
    if prior is not None:
        if prior[0] != task_id:
            raise ValueError('Dialogue turn was already bound to another task')
        return False
    db.execute('''INSERT INTO ai_dialogue_task_links
        (project_id,dialogue_turn_id,task_id,created_at) VALUES(?,?,?,?)''',
        (project_id,turn_id,task_id,_now()))
    return True

def finish_linked_in_transaction(db, project_id: str, task_id: str):
    """Terminal task transition and dialogue receipts commit atomically."""
    if not db.in_transaction:
        raise RuntimeError('Task/dialogue completion requires the owner transaction')
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_dialogue_task_links'").fetchone():
        return 0
    turns=db.execute('''SELECT dialogue_turn_id FROM ai_dialogue_task_links
        WHERE project_id=? AND task_id=?''',(project_id,task_id)).fetchall()
    count=0
    for (turn,) in turns:
        row=db.execute('''SELECT state FROM ai_task_intake
            WHERE project_id=? AND dialogue_turn_id=?''',(project_id,turn)).fetchone()
        if row is None or row[0] not in ('resolving','resolved'):
            raise ValueError('Linked dialogue receipt has an invalid state')
        if row[0]=='resolving':
            update_intake(db,project_id,turn,'resolved')
            count+=1
    return count

