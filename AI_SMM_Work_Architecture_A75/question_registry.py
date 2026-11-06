"""Recoverable clarification questions scoped by project and independent turn ID.

The legacy ai_dialogue_questions table holds one currently presented question.
Older questions are suspended rather than lost when a new assignment arrives.
"""
from __future__ import annotations
from datetime import datetime, timezone
from uuid import uuid4


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def ensure_questions(db):
    db.execute('''CREATE TABLE IF NOT EXISTS ai_suspended_questions(
      question_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,
      request TEXT NOT NULL,question TEXT NOT NULL,plan_json TEXT,
      status TEXT NOT NULL CHECK(status IN ('paused','answered','cancelled')),
      created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
      FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE)''')


def paused_questions(db,project_id):
    ensure_questions(db)
    return [dict(zip(('question_id','request','question','plan_json','status'),row))
            for row in db.execute('''SELECT question_id,request,question,plan_json,status
                 FROM ai_suspended_questions WHERE project_id=? AND status='paused'
                 ORDER BY created_at,question_id''',(project_id,))]


def park_current_question(db,project_id):
    ensure_questions(db)
    row=db.execute('''SELECT request,question,plan_json FROM ai_dialogue_questions
                       WHERE project_id=?''',(project_id,)).fetchone()
    if not row:return None
    ident=uuid4().hex;at=timestamp()
    db.execute('INSERT INTO ai_suspended_questions VALUES(?,?,?,?,?,?,?,?)',
               (ident,project_id,*row,'paused',at,at))
    changed=db.execute('DELETE FROM ai_dialogue_questions WHERE project_id=?',(project_id,)).rowcount
    if changed!=1 or db.execute('SELECT 1 FROM ai_dialogue_questions WHERE project_id=?',(project_id,)).fetchone():
        raise ValueError('Не удалось безопасно перенести уточняющий вопрос')
    return ident


def replace_question(db,project_id,request,question,plan_json):
    ensure_questions(db)
    previous=db.execute('''SELECT request,question,plan_json FROM ai_dialogue_questions
                           WHERE project_id=?''',(project_id,)).fetchone()
    if previous and tuple(previous)!=(request,question,plan_json):
        park_current_question(db,project_id)
    db.execute('INSERT OR REPLACE INTO ai_dialogue_questions(project_id,request,question,plan_json) VALUES(?,?,?,?)',
               (project_id,request,question,plan_json))


def finish_paused_question(db,project_id,question_id,request,*,status):
    if status not in ('answered','cancelled'):raise ValueError('Недопустимый статус вопроса')
    ensure_questions(db)
    changed=db.execute('''UPDATE ai_suspended_questions SET status=?,updated_at=?
       WHERE question_id=? AND project_id=? AND request=? AND status='paused' ''',
                       (status,timestamp(),question_id,project_id,request)).rowcount
    if changed!=1:raise ValueError('Уточняющий вопрос изменился после анализа')
