"""Project-scoped result admission independent of Qt.

The database is the authority: filesystem products must exist, be regular files,
remain inside their project's managed area and are never trusted by filename.
All metadata changes use a savepoint so callers may compose them with other
application transactions. Caller owns the outer commit.
"""
from __future__ import annotations
from pathlib import Path
import sqlite3
from uuid import uuid4
from datetime import datetime, timezone

VIDEO_TYPES = frozenset({'.mp4', '.mov', '.mkv', '.webm'})
IMAGE_TYPES = frozenset({'.png', '.jpg', '.jpeg', '.webp'})

def _timestamp():
    return datetime.now(timezone.utc).isoformat()

class ProjectResultRegistry:
    def __init__(self, db: sqlite3.Connection, workspace):
        self.db, self.workspace = db, workspace

    def _project(self, project_id):
        if not isinstance(project_id, str) or not project_id or not self.db.execute(
            'SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone():
            raise ValueError('Unknown project identity')

    def _atomic(self, op):
        from sqlite_transaction_core import compose
        with compose(self.db, 'result_registry'):
            return op()

    def _publish(self, operation):
        from sqlite_transaction_core import own
        with own(self.db):
            return operation()

    def publish_message(self, project_id, content, *, role='assistant', timestamp=None):
        return self._publish(lambda: self.admit_message(
            project_id, content, role=role, timestamp=timestamp))

    def publish_video(self, project_id, path, *, timestamp=None):
        return self._publish(lambda: self.admit_video(project_id, path, timestamp=timestamp))

    def admit_message(self, project_id, content, *, role='assistant', timestamp=None):
        def operation():
            self._project(project_id)
            if role not in ('assistant', 'user', 'system') or not isinstance(content, str):
                raise ValueError('Invalid message')
            cursor = self.db.execute(
                'INSERT INTO messages(project_id,role,content,created_at) VALUES(?,?,?,?)',
                (project_id, role, content, timestamp or _timestamp()))
            return cursor.lastrowid
        return self._atomic(operation)

    def _admit_media(self, project_id, path, *, kind, extensions, timestamp=None, require_new=False):
        '''Verify a project-scoped media result without involving Qt.'''
        def operation():
            self._project(project_id)
            if self.workspace is None:
                raise ValueError('Media publication requires a project workspace')
            root=Path(self.workspace(project_id))
            processed=root/'processed'
            candidate=Path(path)
            if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)()
                   for p in (root,processed,candidate)):
                raise ValueError('Symlink result is forbidden')
            try:
                dest=candidate.resolve(strict=True)
                canonical_folder=processed.resolve(strict=True)
            except (OSError,RuntimeError) as exc:
                raise ValueError('Missing or unsafe media artifact') from exc
            if (not dest.is_file() or dest.stat().st_size<=0 or
                    dest.suffix.lower() not in extensions or dest.parent!=canonical_folder):
                raise ValueError('Media artifact does not belong to the project or is empty')
            if kind=='result':
                from PIL import Image
                try:
                    with Image.open(dest) as loaded:
                        loaded.verify()
                except (OSError,ValueError,TypeError) as exc:
                    raise ValueError('Corrupt image result') from exc
            existing=self.db.execute('''SELECT id FROM project_files
                WHERE project_id=? AND kind=? AND path=?''',(project_id,kind,str(dest))).fetchone()
            if existing:
                if require_new:
                    raise ValueError('Batch result was already registered')
                return existing[0]
            ident=uuid4().hex
            self.db.execute('''INSERT INTO project_files
                (id,project_id,kind,display_name,path,created_at) VALUES(?,?,?,?,?,?)''',
                (ident,project_id,kind,dest.name,str(dest),timestamp or _timestamp()))
            return ident
        return self._atomic(operation)

    def admit_video(self, project_id, path, *, timestamp=None):
        return self._admit_media(project_id,path,kind='video',extensions=VIDEO_TYPES,timestamp=timestamp)

    def admit_image(self, project_id, path, *, timestamp=None, require_new=False):
        return self._admit_media(project_id,path,kind='result',extensions=IMAGE_TYPES,timestamp=timestamp,require_new=require_new)
