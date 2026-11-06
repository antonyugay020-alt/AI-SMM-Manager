"""Verify and commit local media output to the *launch* project, never GUI selection.

Workers report an untrusted path.  The reserved output path and project owner
are captured before launch; the UI is only updated after a verified DB commit.
"""
from __future__ import annotations
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone
from PIL import Image


def commit_owned_image(db, project_id, reserved_output, returned_output, project_root):
    if db.in_transaction:
        raise RuntimeError('Медиа-результат нельзя принимать внутри незавершённой транзакции')
    root = Path(project_root)
    processed = root / 'processed'
    expected = Path(reserved_output)
    actual = Path(returned_output)
    if any(part.is_symlink() or getattr(part, 'is_junction', lambda: False)()
           for part in (root, processed, expected, actual)):
        raise ValueError('Символические ссылки и junction при записи медиа запрещены')
    if not processed.is_dir() or not expected.is_file() or not actual.is_file():
        raise ValueError('Ожидаемый результат отсутствует')
    if expected.parent.resolve(strict=True) != processed.resolve(strict=True):
        raise ValueError('Выходной путь не принадлежит каталогу проекта')
    if actual.resolve(strict=True) != expected.resolve(strict=True):
        raise ValueError('Исполнитель вернул чужой выходной путь')
    ext = expected.suffix.lower()
    if ext not in ('.jpg', '.jpeg', '.png') or expected.stat().st_size == 0:
        raise ValueError('Неверный или пустой медиафайл')
    with Image.open(expected) as im:
        if im.format not in (('PNG',) if ext == '.png' else ('JPEG',)):
            raise ValueError('Формат изображения не соответствует расширению')
        im.verify()
    if db.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone() is None:
        raise ValueError('Проект, запустивший обработку, не существует')
    normalized = str(expected.resolve(strict=True))
    if db.execute('SELECT 1 FROM project_files WHERE project_id=? AND path=?',
                  (project_id, normalized)).fetchone():
        raise ValueError('Повторный сигнал сохранения того же результата')
    try:
        db.execute('INSERT INTO project_files VALUES (?,?,?,?,?,?)',
                   (uuid4().hex, project_id, 'result', expected.name, normalized,
                    datetime.now(timezone.utc).isoformat()))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return normalized
