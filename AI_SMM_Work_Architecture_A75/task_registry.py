"""Durable, project-scoped identities for independent pending assignments.

The legacy ai_pending_tasks row remains the current active execution pointer;
paused assignments are never overwritten and can be selected explicitly by ID.
No user language is classified in this module.
"""
from __future__ import annotations
from datetime import datetime, timezone
from uuid import uuid4


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def ensure_registry(db):
    db.execute('''CREATE TABLE IF NOT EXISTS ai_task_registry (
        task_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
        request TEXT NOT NULL, question TEXT NOT NULL,
        revision_id TEXT, status TEXT NOT NULL
        CHECK(status IN ('active','paused','completed','cancelled')),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        legacy_origin INTEGER NOT NULL DEFAULT 0 CHECK(legacy_origin IN (0,1)),
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE)''')
    if 'legacy_origin' not in {r[1] for r in db.execute('PRAGMA table_info(ai_task_registry)')}:
        db.execute('ALTER TABLE ai_task_registry ADD COLUMN legacy_origin INTEGER NOT NULL DEFAULT 0')
    db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS ai_task_registry_single_active
                  ON ai_task_registry(project_id) WHERE status='active' ''')
    db.execute('''CREATE TABLE IF NOT EXISTS ai_task_contexts (
        task_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, context_json TEXT NOT NULL,
        FOREIGN KEY(task_id) REFERENCES ai_task_registry(task_id) ON DELETE CASCADE)''')
    # Migrate legacy state without changing or silently cancelling any work.
    existing = {r[1] for r in db.execute('PRAGMA table_info(ai_pending_tasks)')}
    if not {'project_id','request','question','revision_id'} <= existing:
        return
    for project_id, request, question, revision_id in db.execute(
            'SELECT project_id,request,question,revision_id FROM ai_pending_tasks').fetchall():
        active = db.execute("SELECT task_id,request FROM ai_task_registry WHERE project_id=? AND status='active'",
                            (project_id,)).fetchone()
        if active is None:
            at = _stamp()
            db.execute('''INSERT INTO ai_task_registry
                       (task_id,project_id,request,question,revision_id,status,created_at,updated_at,legacy_origin)
                       VALUES(?,?,?,?,?,?,?,?,1)''',
                       (uuid4().hex,project_id,request,question,revision_id,'active',at,at))
        elif active[1] != request:
            # Inconsistent legacy pointer: do not resolve ownership by guessing.
            raise ValueError('Активное задание и legacy указатель расходятся')
        # Bind existing R97 contexts to an identity before the request-keyed
        # compatibility table can be overwritten by a later identical task.
        row=db.execute("SELECT task_id,legacy_origin FROM ai_task_registry WHERE project_id=? AND status='active'",
                       (project_id,)).fetchone()
        # Once a task belongs to the new registry, never reconstruct its
        # missing contract from the ambiguous request-keyed legacy table.
        if row[1] and not db.execute('SELECT 1 FROM ai_task_contexts WHERE task_id=?',(row[0],)).fetchone():
            has_table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_semantic_tasks'").fetchone()
            if has_table:
                context=db.execute('SELECT context_json FROM ai_semantic_tasks WHERE project_id=? AND request=?',
                                   (project_id,request)).fetchone()
                if context:
                    db.execute('INSERT INTO ai_task_contexts VALUES(?,?,?)',
                               (row[0],project_id,context[0]))


def paused_tasks(db, project_id):
    ensure_registry(db)
    return [dict(zip(('task_id','request','question','revision_id','status','legacy_origin'),row))
            for row in db.execute('''SELECT task_id,request,question,revision_id,status,legacy_origin
                FROM ai_task_registry WHERE project_id=? AND status='paused'
                ORDER BY created_at,task_id''',(project_id,))]


def activate(db, project_id, request, question, revision_id=None, *, task_id=None, force_new=False):
    """Switch active pointer; preserve older assignment and its checkpoints."""
    ensure_registry(db)
    active = db.execute('''SELECT task_id,request FROM ai_task_registry
                            WHERE project_id=? AND status='active' ''',(project_id,)).fetchone()
    if task_id:
        selected = db.execute('''SELECT request,status FROM ai_task_registry
                                 WHERE project_id=? AND task_id=?''',(project_id,task_id)).fetchone()
        if not selected or selected[0] != request or selected[1] not in ('active','paused'):
            raise ValueError('Задание больше недоступно для продолжения')
    elif active and active[1]==request and not force_new:
        task_id=active[0]
    else:
        task_id=uuid4().hex
    at=_stamp()
    if active and active[0]!=task_id:
        db.execute('''UPDATE ai_task_registry SET status='paused',updated_at=?
                      WHERE project_id=? AND task_id=? AND status='active' ''',(at,project_id,active[0]))
    selected=db.execute('SELECT 1 FROM ai_task_registry WHERE project_id=? AND task_id=?',
                        (project_id,task_id)).fetchone()
    if selected:
        db.execute('''UPDATE ai_task_registry SET question=?,revision_id=?,status='active',updated_at=?
                      WHERE project_id=? AND task_id=?''',(question,revision_id,at,project_id,task_id))
    else:
        db.execute('''INSERT INTO ai_task_registry
                   (task_id,project_id,request,question,revision_id,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)''',
                   (task_id,project_id,request,question,revision_id,'active',at,at))
    # Do not rely on an implicit UNIQUE constraint in legacy test/migrated DBs.
    db.execute('DELETE FROM ai_pending_tasks WHERE project_id=?',(project_id,))
    db.execute('''INSERT INTO ai_pending_tasks(project_id,request,question,revision_id)
                  VALUES(?,?,?,?)''',(project_id,request,question,revision_id))
    return task_id


def finish(db,project_id,request,*,task_id=None,status='completed'):
    if status not in ('completed','cancelled'):
        raise ValueError('Недопустимый завершающий статус')
    ensure_registry(db)
    active=db.execute('''SELECT task_id,request FROM ai_task_registry
                          WHERE project_id=? AND status='active' ''',(project_id,)).fetchone()
    if not active or active[1]!=request:
        raise ValueError('Завершаемое задание не является текущим')
    # Request text is not an execution identity. A delayed response must never
    # finish a different task with identical wording. Legacy callers without
    # an identity remain supported only when the request is unambiguous.
    if task_id is not None and active[0]!=task_id:
        raise ValueError('Результат относится к другому идентификатору задания')
    if task_id is None and db.execute('''SELECT 1 FROM ai_task_registry
            WHERE project_id=? AND request=? AND task_id<>? LIMIT 1''',
            (project_id,request,active[0])).fetchone():
        raise ValueError('Невозможно завершить задание по неоднозначному тексту запроса')
    changed=db.execute('DELETE FROM ai_pending_tasks WHERE project_id=? AND request=?',
                       (project_id,request)).rowcount
    if changed!=1:raise ValueError('Не подтверждена очистка текущего задания')
    db.execute('UPDATE ai_task_registry SET status=?,updated_at=? WHERE task_id=? AND project_id=?',
               (status,_stamp(),active[0],project_id))
    return active[0]


def cancel_paused(db,project_id,task_id,request):
    ensure_registry(db)
    changed=db.execute('''UPDATE ai_task_registry SET status='cancelled',updated_at=?
          WHERE project_id=? AND task_id=? AND request=? AND status='paused' ''',
                       (_stamp(),project_id,task_id,request)).rowcount
    if changed!=1:raise ValueError('Приостановленное задание изменилось')


def task_context_json(db,project_id,task_id):
    if not task_id:return None
    ensure_registry(db)
    row=db.execute('SELECT context_json FROM ai_task_contexts WHERE task_id=? AND project_id=?',
                   (task_id,project_id)).fetchone()
    return row[0] if row else None
