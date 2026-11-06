"""Stage 57: persistent execution receipts and content safety gates.

All database writes are performed by the UI thread. This module never invokes
an LLM and does not claim to assess the editorial quality of a document.
"""
import datetime
import hashlib
import json
import re


def ensure_journal(db):
    db.execute('''CREATE TABLE IF NOT EXISTS ai_execution_journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL,
        request TEXT NOT NULL,
        artifact_kind TEXT NOT NULL,
        artifact_sha256 TEXT NOT NULL,
        status TEXT NOT NULL,
        details TEXT NOT NULL,
        created_at TEXT NOT NULL
    )''')
    db.commit()


def receipt(db, project_id, request, kind, content, status, details=''):
    if status not in ('saved', 'rejected', 'failed', 'reused'):
        raise ValueError('Unsupported execution status')
    ensure_journal(db)
    digest=hashlib.sha256((content or '').encode('utf-8')).hexdigest()
    db.execute('''INSERT INTO ai_execution_journal
        (project_id,request,artifact_kind,artifact_sha256,status,details,created_at)
        VALUES (?,?,?,?,?,?,?)''', (str(project_id), request, kind, digest, status,
        str(details)[:4000], datetime.datetime.now(datetime.timezone.utc).isoformat()))
    db.commit()


def editorial_warnings(kind, content, minimum=1):
    """Flag obvious defects; do not treat heuristics as proof of quality."""
    issues=[]
    if kind != 'calendar':
        return issues
    # Evaluate only actual publication cells. Markdown headers such as
    # "Конкретная тема" are labels, not generated placeholder content.
    from professional_content import count_calendar_entries
    rows=count_calendar_entries(content)
    if len(rows)<minimum:
        issues.append('В календаре недостаточно заполненных строк')
    placeholders=('разберите организационный вопрос', 'покажите на экране конкретные шаги',
                  'практический выпуск', 'соберите реальные вопросы аудитории',
                  'конкретная тема', 'уточните актуальные условия')
    for phrase in placeholders:
        hits=sum(phrase in cell.casefold() for row in rows for cell in row[2:])
        if hits>=4:
            issues.append('Повторяющаяся шаблонная фраза: '+phrase)
    lower=content.casefold()
    # A content plan should not silently assert that it has undergone legal review.
    if re.search(r'юридическ(?:ая|ую) проверк(?:а|у) пройдена',lower):
        issues.append('Неподтверждённое утверждение о юридической проверке')
    return issues
