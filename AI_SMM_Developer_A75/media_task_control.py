"""Durable cooperative local stop; never claims to cancel a remote paid task."""
import sqlite3
from datetime import datetime,timezone


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def _owned_state(db,owner,job):
    row=db.execute('SELECT state FROM kie_media_tasks WHERE id=? AND project_id=?',(job,owner)).fetchone()
    if row is None or db.execute('SELECT 1 FROM projects WHERE id=?',(owner,)).fetchone() is None:
        raise ValueError('Media task does not belong to an existing project')
    return row[0]


class LocalTaskStopped(RuntimeError):
    pass


def install(db):
    db.execute('''CREATE TABLE IF NOT EXISTS media_task_controls (
        job_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
        stop_requested INTEGER NOT NULL CHECK(stop_requested IN (0,1)),
        updated_at TEXT NOT NULL)''')


def stopped(db, project_id, job_id):
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='media_generation_intents'").fetchone():
        intent=db.execute('SELECT stop_requested FROM media_generation_intents WHERE id=? AND project_id=?',(job_id,project_id)).fetchone()
        if intent and intent[0]:return True
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='media_task_controls'").fetchone():
        return False
    row=db.execute('SELECT stop_requested FROM media_task_controls WHERE job_id=? AND project_id=?',
                   (job_id,project_id)).fetchone()
    return bool(row and row[0])


def check_active(db, project_id, job_id):
    if stopped(db,project_id,job_id):
        raise LocalTaskStopped('Local media processing stopped; remote task may continue')


def request_stop(db, project_id, job_id):
    if db.in_transaction:
        raise RuntimeError('Foreign transaction owns media stop')
    try:
        db.execute('BEGIN IMMEDIATE')
        state=_owned_state(db,project_id,job_id)
        if state in ('completed','failed'):
            db.rollback()
            return False
        install(db)
        db.execute('''INSERT INTO media_task_controls VALUES(?,?,1,?)
            ON CONFLICT(job_id) DO UPDATE SET stop_requested=1,updated_at=excluded.updated_at
            WHERE media_task_controls.project_id=excluded.project_id''',
            (job_id,project_id,utcnow()))
        db.commit()
        return True
    except BaseException:
        db.rollback()
        raise


def resume_confirmed(db, project_id, job_id):
    if db.in_transaction:
        raise RuntimeError('Foreign transaction owns media resume')
    try:
        db.execute('BEGIN IMMEDIATE')
        state=_owned_state(db,project_id,job_id)
        if state!='submitted':
            raise RuntimeError('Only confirmed submitted tasks may resume local retrieval')
        install(db)
        db.execute('UPDATE media_task_controls SET stop_requested=0,updated_at=? WHERE job_id=? AND project_id=?',
                   (utcnow(),job_id,project_id))
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='media_generation_intents'").fetchone():
            db.execute('UPDATE media_generation_intents SET stop_requested=0 WHERE id=? AND project_id=?',(job_id,project_id))
        db.commit()
    except BaseException:
        db.rollback()
        raise
