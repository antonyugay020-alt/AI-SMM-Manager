"""Retrieve project-owned text automatically without upgrading AI drafts to facts."""
from __future__ import annotations

from typing import Any


def load_project_knowledge(db, project_id: str, *, current_message: str = '',
                           max_user_messages: int = 30, max_documents: int = 10,
                           total_document_chars: int = 9000,
                           total_history_chars: int = 7000) -> dict[str, Any]:
    """Return prior project material with explicit provenance and bounded size.

    User-authored project history and the saved project description are factual
    sources. Existing AI/project documents are contextual memory only; they are
    never silently treated as factual evidence. Binary assets (especially visual
    references) are not opened or passed to the model by this function.
    """
    verified = []
    row = db.execute('SELECT description FROM projects WHERE id=?', (str(project_id),)).fetchone()
    description = row[0].strip() if row and isinstance(row[0], str) else ''
    if description:
        verified.append({'source_type': 'project_description', 'title': 'Описание проекта',
                         'content': description, 'verified': True})

    rows = db.execute(
        'SELECT content,created_at FROM messages WHERE project_id=? AND role=? '
        'ORDER BY id DESC LIMIT ?', (str(project_id), 'user', int(max_user_messages) + 1)).fetchall()
    remaining_history=max(0,int(total_history_chars))
    selected_history=[]
    for content, created_at in rows:
        if not isinstance(content, str) or not content.strip() or content.strip() == current_message.strip():
            continue
        if len(content) > remaining_history:
            # User-authored source text is never silently truncated into
            # misleading evidence. Prefer a smaller/recent whole source.
            continue
        selected_history.append({'source_type': 'user_project_history',
                         'title': 'Предыдущее сообщение пользователя проекта',
                         'content': content.strip(), 'created_at': str(created_at or ''),
                         'verified': True})
        remaining_history-=len(content)
    verified.extend(reversed(selected_history))

    docs = db.execute(
        'SELECT kind,title,content,updated_at FROM manager_documents WHERE project_id=? '
        'ORDER BY updated_at DESC,rowid DESC LIMIT ?', (str(project_id), int(max_documents))).fetchall()
    memory_docs = []
    remaining = max(0, int(total_document_chars))
    for kind, title, content, updated_at in docs:
        if not isinstance(content, str) or not content.strip() or remaining <= 0:
            continue
        original = content.strip()
        included = original[:remaining]
        remaining -= len(included)
        memory_docs.append({'kind': str(kind or ''), 'title': str(title or ''),
                            'content': included, 'updated_at': str(updated_at or ''),
                            'truncated': len(included) < len(original),
                            'authority': 'prior_project_document_context_only'})
    return {'verified_project_materials': verified,
            'existing_documents': memory_docs,
            'memory_policy': ('Prior generated documents may inform continuity and avoid duplication; '
                              'they are not proof of business facts unless separately user-confirmed.')}
