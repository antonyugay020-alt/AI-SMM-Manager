"""Qt-free project registry and on-demand durable workspace projection.

Project listing is read-only and does not touch the filesystem. Workspace
reconciliation is performed only for an explicit selected project, with a
bounded, resumable maintenance operation for background recovery.
"""
from dataclasses import dataclass
from pathlib import Path
from project_lifecycle import register_project, reconcile_project

@dataclass(frozen=True)
class ProjectSummary:
    id: str
    name: str
    category: str | None

@dataclass(frozen=True)
class RecoveryBatch:
    succeeded: tuple[str, ...]
    errors: dict[str, str]
    next_cursor: str | None

class ProjectRuntime:
    def __init__(self, db):
        self.db=db

    def list_projects(self):
        return [ProjectSummary(*row) for row in self.db.execute(
            'SELECT id,name,category FROM projects ORDER BY created_at DESC,id DESC')]

    def create(self, root, name, category, description, timestamp, *, project_id=None):
        pid=register_project(self.db,root,name,category,description,timestamp,project_id=project_id)
        # Registry commit survives a failed filesystem projection; repair is idempotent.
        reconcile_project(self.db,pid)
        return pid

    def activate(self, project_id):
        # Validation of project identity and projection happens before UI switches.
        return reconcile_project(self.db,project_id)

    def recover_batch(self, *, cursor=None, batch_size=32):
        if self.db.in_transaction:
            raise RuntimeError('Cannot reconcile during an active transaction')
        if not isinstance(batch_size,int) or not 1 <= batch_size <= 512:
            raise ValueError('Batch size outside safe range')
        if cursor is not None and not isinstance(cursor,str):
            raise ValueError('Invalid cursor')
        # Stable key pagination; no offset scans, full project table locks or GUI blocking loop.
        rows=self.db.execute('SELECT id FROM projects WHERE id>? ORDER BY id LIMIT ?',
                             (cursor or '', batch_size+1)).fetchall()
        selection=rows[:batch_size]
        success=[]; errors={}
        for (pid,) in selection:
            try: self.activate(pid); success.append(pid)
            except (OSError,ValueError,RuntimeError) as exc: errors[pid]=str(exc)
        next_cursor=selection[-1][0] if len(rows)>batch_size else None
        return RecoveryBatch(tuple(success),errors,next_cursor)
