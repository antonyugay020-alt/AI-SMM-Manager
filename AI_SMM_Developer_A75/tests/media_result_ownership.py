"""Commit a generated image only to its immutable launch project.

Image decoding and full byte evidence are prepared before acquiring the SQLite
write lock. Publication uses a short transaction with project, duplicate and
file-identity fences; a failed fence rolls back the entire registration.
"""
from __future__ import annotations
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone
from PIL import Image
from file_evidence import capture, unchanged
from project_file_boundary import require_managed_file


def commit_owned_image(db, project_id, reserved_output, returned_output, project_root):
    if db.in_transaction:
        raise RuntimeError('Медиа-результат нельзя принимать внутри незавершённой транзакции')
    root = Path(project_root)
    expected = require_managed_file(root, Path(reserved_output), ('processed',))
    actual = require_managed_file(root, Path(returned_output), ('processed',))
    if actual != expected:
        raise ValueError('Исполнитель вернул чужой выходной путь')
    if not expected.is_file():
        raise ValueError('Ожидаемый результат отсутствует')
    ext = expected.suffix.lower()
    if ext not in ('.jpg', '.jpeg', '.png'):
        raise ValueError('Неверный формат медиафайла')
    evidence = capture(expected)
    with Image.open(expected) as im:
        if im.format not in (('PNG',) if ext == '.png' else ('JPEG',)):
            raise ValueError('Формат изображения не соответствует расширению')
        im.verify()
    if not unchanged(evidence):
        raise ValueError('Медиафайл изменён во время проверки')
    try:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone() is None:
            raise ValueError('Проект, запустивший обработку, не существует')
        if not unchanged(evidence):
            raise ValueError('Медиафайл изменён перед сохранением')
        normalized = str(expected)
        if db.execute('SELECT 1 FROM project_files WHERE path=?', (normalized,)).fetchone():
            raise ValueError('Повторный сигнал сохранения того же результата')
        db.execute('INSERT INTO project_files VALUES (?,?,?,?,?,?)',
                   (uuid4().hex, project_id, 'result', expected.name, normalized,
                    datetime.now(timezone.utc).isoformat()))
        if not unchanged(evidence):
            raise ValueError('Медиафайл изменён во время сохранения')
        db.commit()
    except BaseException:
        if db.in_transaction:
            db.rollback()
        raise
    return normalized
