"""Single SQLite-authoritative workspace resolver for the application service graph.

The GUI and every Qt-free service use the SAME connection and registered
project location. No per-operation second SQLite connection, mutable storage
settings, or guessed fallback may redirect an existing project's data.
"""
from __future__ import annotations
from pathlib import Path
import re
import sqlite3

_ID = re.compile(r'[0-9a-f]{32}\Z')

class ProjectWorkspaceResolver:
    def __init__(self, db: sqlite3.Connection):
        if not isinstance(db, sqlite3.Connection):
            raise TypeError('A project workspace requires the application SQLite connection')
        self.db = db

    def __call__(self, project_id: str) -> Path:
        if not isinstance(project_id, str) or not _ID.fullmatch(project_id):
            raise ValueError('Invalid immutable project ID')
        row = self.db.execute('''SELECT l.folder FROM projects p
            JOIN project_locations l ON l.project_id=p.id WHERE p.id=?''',
            (project_id,)).fetchone()
        if row is None:
            raise ValueError('Project has no registered workspace')
        root = Path(row[0])
        if not root.is_absolute() or '..' in root.parts:
            raise ValueError('Project workspace must be an absolute canonical location')
        # Check every pre-existing parent: a linked ancestor is as unsafe as
        # a linked project folder, even if the last component is a directory.
        for part in (root, *root.parents):
            if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
                raise ValueError('Project workspace traverses a filesystem link')
        return root
