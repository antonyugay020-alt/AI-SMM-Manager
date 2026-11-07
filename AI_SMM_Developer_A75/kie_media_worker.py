"""One explicit result refresh on a private SQLite connection and native QThread.

No paid submission, upload, automatic polling loop or forced thread termination.
Project ownership is frozen before launch; the GUI receives only safe status.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
from PySide6.QtCore import QThread, Signal
from kie_media_jobs import complete
from kie_media_download import download_result


class KieResultWorker(QThread):
    done = Signal(str, str, str)
    failed = Signal(str, str, str)

    def __init__(self, database_path, workspace_root, project_id, job_id,
                 query_task, *, fetch_verified_file=None, parent=None):
        super().__init__(parent)
        self.database_path = str(database_path)
        self.workspace_root = Path(workspace_root)
        self.project_id = project_id
        self.job_id = job_id
        self.query_task = query_task
        self.fetch_verified_file = fetch_verified_file or download_result

    def run(self):
        db = None
        try:
            # mode=rw prevents a stale configuration from creating an empty DB.
            uri = Path(self.database_path).resolve().as_uri()+"?mode=rw"
            db = sqlite3.connect(uri, uri=True, timeout=30)
            db.execute("PRAGMA foreign_keys=ON")
            def workspace(owner):
                if owner != self.project_id:
                    raise ValueError("media worker owner mismatch")
                return self.workspace_root
            output = complete(db, self.project_id, self.job_id, self.query_task,
                              self.fetch_verified_file, workspace)
            self.done.emit(self.project_id, self.job_id, str(output or ""))
        except Exception as exc:
            # Neither provider exception text nor signed URLs enter Qt dialogs.
            self.failed.emit(self.project_id, self.job_id, type(exc).__name__)
        finally:
            if db is not None:
                db.close()
