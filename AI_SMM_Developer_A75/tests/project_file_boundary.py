"""Fail-closed, lexical ownership checks for project media paths.

`resolve()` alone is insufficient: resolving both a symlinked project folder and
its allowed folder yields equal paths even when the data is outside the project.
"""
from pathlib import Path


def _safe_path(value):
    try:
        return Path(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('Некорректный путь файла или каталога проекта') from exc


def reject_path_link(path):
    """Reject linked components, including ancestors of the workspace root.

    A linked parent is as unsafe as a linked leaf: otherwise a lexical path
    may be inside the project while its filesystem target is elsewhere.
    Inspect without resolving, so the link itself cannot disappear from view.
    """
    path = _safe_path(path)
    for component in (path, *path.parents):
        if component.is_symlink() or getattr(component, 'is_junction', lambda: False)():
            raise ValueError('Путь проекта проходит через ссылку: ' + str(component))
    return path


def require_managed_folder(project_root, relative):
    base = _safe_path(project_root)
    if not base.is_absolute():
        raise ValueError('Неабсолютный путь каталога проекта')
    reject_path_link(base)
    rel = _safe_path(relative)
    if rel.is_absolute() or not rel.parts or any(p in ('.', '..') for p in rel.parts):
        raise ValueError('Некорректный каталог проекта')
    cursor = base
    for part in rel.parts:
        cursor = cursor / part
        reject_path_link(cursor)
    if not cursor.is_relative_to(base):
        raise ValueError('Каталог вне проекта')
    return cursor


def require_managed_file(project_root, file_path, allowed_folders):
    base = _safe_path(project_root)
    source = _safe_path(file_path)
    reject_path_link(base)
    reject_path_link(source)
    if not source.is_absolute() or not base.is_absolute():
        # Stored file identities must be canonical lexical paths, not CWD-based.
        raise ValueError('Неабсолютный путь файла проекта')
    for folder in allowed_folders:
        directory = require_managed_folder(base, folder)
        if source.parent == directory and source.name not in ('', '.', '..'):
            return source
    raise ValueError('Файл не принадлежит разрешённой папке проекта: ' + str(source))
