"""Durable, typed post-commit filesystem effects for AI document completion.

SQLite stores the intent in the *same* transaction as the document. The runner
retries idempotent file operations after commit or on restart. Nothing here
runs arbitrary serialized callbacks or trusts a path from the database.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from durable_json import atomic_write_json
from project_file_boundary import require_managed_file

_RECEIPT = re.compile(r'text_receipt_(?:task_[0-9a-f]{32}|[0-9a-f]{16})\.json\Z')
_PENDING = re.compile(r'pending_text_(?:task_[0-9a-f]{32}|[0-9a-f]{16})\.json\Z')
_KINDS = ('export_document', 'verify_receipt', 'clear_pending')


def install(db):
    """Atomically install or recover document-effect schema, including an
    interrupted legacy rename/copy. Never silently discard an orphan table.
    """
    from file_effect_schema_recovery import install_schema
    install_schema(db)


def _name(name, kind):
    if not isinstance(name, str) or not ( _RECEIPT if kind == 'receipt' else _PENDING).fullmatch(name):
        raise ValueError('Недопустимое имя контрольного файла задания')
    return name


def queue_document_completion(db, *, project_id, artifact_id, content, request,
                              receipt_path, pending_path, project_workspace, receipt_data):
    """Queue both effects while the caller's document transaction is open."""
    if not db.in_transaction:
        raise RuntimeError('Файловые эффекты разрешены только внутри транзакции результата')
    if not re.fullmatch(r'[a-f0-9]{32}', str(project_id)):
        raise ValueError('Не подтверждён проект документа')
    if not isinstance(content, str) or not isinstance(request, str) or not request:
        raise ValueError('Нет исходного документа или поручения')
    # No unchecked absolute path or developer-supplied callback is persisted.
    workspace = Path(project_workspace)
    receipt_file = require_managed_file(workspace, receipt_path, ('drafts',))
    pending_file = require_managed_file(workspace, pending_path, ('drafts',))
    rname = _name(receipt_file.name, 'receipt')
    pname = _name(pending_file.name, 'pending')
    sha = hashlib.sha256(content.encode('utf-8')).hexdigest()
    request_sha = hashlib.sha256(request.encode('utf-8')).hexdigest()
    row = db.execute('SELECT content FROM manager_documents WHERE id=? AND project_id=?',
                     (artifact_id, project_id)).fetchone()
    if not row or hashlib.sha256(row[0].encode('utf-8')).hexdigest() != sha:
        raise ValueError('Содержимое документа в SQLite не совпадает с подтверждённым результатом')
    if (not isinstance(receipt_data, dict) or receipt_data.get('artifact_id') != artifact_id or
            receipt_data.get('artifact_sha256') != sha or receipt_data.get('request_sha256') != request_sha or
            receipt_data.get('request') != request or receipt_data.get('saved_and_read_back') is not False):
        raise ValueError('Данные подтверждения не соответствуют исходному документу')
    encoded = json.dumps(receipt_data, ensure_ascii=False, separators=(',', ':'))
    if len(encoded) > 250_000:
        raise ValueError('Слишком большой файл подтверждения')
    install(db)
    for kind in _KINDS:
        db.execute('''INSERT INTO ai_file_effect_outbox
            (id,project_id,artifact_id,artifact_sha256,request_sha256,
             kind,receipt_name,pending_name,receipt_json,created_at)
             VALUES(?,?,?,?,?,?,?,?,?,?)''',
             (uuid4().hex,project_id,artifact_id,sha,request_sha,kind,rname,pname,encoded,
              datetime.now(timezone.utc).isoformat()))


def _owned_path(workspace, name):
    return require_managed_file(Path(workspace), Path(workspace) / 'drafts' / name, ('drafts',))


def _perform(db, row, workspace, export_document=None):
    _, pid, artifact_id, sha, request_sha, kind, receipt_name, pending_name, receipt_json = row
    _name(receipt_name, 'receipt')
    _name(pending_name, 'pending')
    receipt = _owned_path(workspace, receipt_name)
    pending = _owned_path(workspace, pending_name)
    document = db.execute('SELECT content FROM manager_documents WHERE id=? AND project_id=?',
                          (artifact_id,pid)).fetchone()
    if not document or hashlib.sha256(document[0].encode('utf-8')).hexdigest() != sha:
        raise ValueError('Документ изменён или отсутствует; файлы не затронуты')
    queued = json.loads(receipt_json)
    if (not isinstance(queued, dict) or queued.get('artifact_id') != artifact_id or
            queued.get('artifact_sha256') != sha or queued.get('request_sha256') != request_sha or
            queued.get('saved_and_read_back') is not False):
        raise ValueError('Повреждён защищённый контракт файлового эффекта')
    if kind == 'export_document':
        if export_document is None:
            raise RuntimeError('Document exporter is not available')
        # Exporter is a host-owned capability, not an arbitrary callback from SQLite.
        # Both project and artifact identity were checked against the persisted row.
        export_document(pid, artifact_id, document[0])
        return
    export_state = db.execute('''SELECT state FROM ai_file_effect_outbox
        WHERE project_id=? AND artifact_id=? AND kind='export_document'
        ORDER BY rowid DESC LIMIT 1''', (pid, artifact_id)).fetchone()
    if export_state is not None and export_state[0] != 'done':
        raise RuntimeError('Document export has not been durably verified')
    if kind == 'clear_pending':
        verified = db.execute('''SELECT state FROM ai_file_effect_outbox
            WHERE project_id=? AND artifact_id=? AND kind='verify_receipt'
            ORDER BY rowid DESC LIMIT 1''', (pid, artifact_id)).fetchone()
        if verified is None or verified[0] != 'done':
            raise RuntimeError('Receipt verification must complete before deleting the checkpoint')
    if not receipt.exists():
        if kind != 'verify_receipt':
            raise ValueError('Нет файла подтверждения документа; чекпойнт сохранён')
        atomic_write_json(receipt, {**queued, 'saved_and_read_back':True})
    if not receipt.is_file():
        raise ValueError('Подтверждение не является файлом')
    record = json.loads(receipt.read_text(encoding='utf-8'))
    if (not isinstance(record, dict) or record.get('artifact_id') != artifact_id or
            record.get('artifact_sha256') != sha or record.get('request_sha256') != request_sha or
            type(record.get('saved_and_read_back')) is not bool):
        raise ValueError('Подтверждение относится к другому заданию или повреждено')
    if kind == 'verify_receipt':
        if not record['saved_and_read_back']:
            atomic_write_json(receipt, {**record, 'saved_and_read_back':True})
    elif kind == 'clear_pending':
        if not record['saved_and_read_back']:
            raise ValueError('Подтверждение ещё не завершено, старый черновик сохранён')
        if pending.exists():
            if not pending.is_file():
                raise ValueError('Чекпойнт не является файлом')
            saved = json.loads(pending.read_text(encoding='utf-8'))
            if not isinstance(saved, dict) or hashlib.sha256(str(saved.get('request', '')).encode('utf-8')).hexdigest() != request_sha:
                raise ValueError('Чекпойнт относится к другому заданию')
            task_id = re.fullmatch(r'pending_text_task_([0-9a-f]{32})\.json', pending_name)
            if task_id and saved.get('task_id') != task_id.group(1):
                raise ValueError('Чекпойнт принадлежит другому экземпляру задания')
            pending.unlink()
    else:
        raise ValueError('Неизвестный файловый эффект')


def drain(db, project_workspace, *, project_id=None, limit=100, export_document=None):
    """Best-effort idempotent replay. Caller must own a clean SQLite connection.

    Returns counts; errors remain durably queued for the next restart.
    """
    if db.in_transaction:
        raise RuntimeError('Нельзя выполнять файловые эффекты внутри чужой транзакции')
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('Недопустимый размер очереди')
    install(db)
    db.commit()
    args = []
    query = '''SELECT id,project_id,artifact_id,artifact_sha256,request_sha256,kind,
                      receipt_name,pending_name,receipt_json FROM ai_file_effect_outbox WHERE state='pending' '''
    if project_id is not None:
        query += 'AND project_id=? '
        args.append(project_id)
    query += "ORDER BY created_at, CASE kind WHEN 'export_document' THEN 0 WHEN 'verify_receipt' THEN 1 ELSE 2 END, rowid LIMIT ?"
    args.append(limit)
    rows = db.execute(query, args).fetchall()
    completed = failures = 0
    for row in rows:
        ident, pid = row[:2]
        try:
            workspace = project_workspace(pid)
            _perform(db, row, workspace, export_document)
            db.execute("UPDATE ai_file_effect_outbox SET state='done',last_error='' WHERE id=? AND state='pending'", (ident,))
            db.commit()
            completed += 1
        except Exception as exc:
            if db.in_transaction:
                db.rollback()
            try:
                db.execute('''UPDATE ai_file_effect_outbox SET attempts=attempts+1,
                    last_error=? WHERE id=? AND state='pending' ''', (str(exc)[:600],ident))
                db.commit()
            except sqlite3.Error:
                if db.in_transaction:
                    db.rollback()
            failures += 1
    return {'completed':completed,'failed':failures,'pending':db.execute(
        "SELECT count(*) FROM ai_file_effect_outbox WHERE state='pending'").fetchone()[0]}
