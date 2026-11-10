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
    stopped = Signal(str, str)

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
        from media_task_control import LocalTaskStopped
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
            def check_interrupted():
                if self.isInterruptionRequested():
                    raise LocalTaskStopped('Local worker interrupted')
            check_interrupted()
            output = complete(db, self.project_id, self.job_id, self.query_task,
                              self.fetch_verified_file, workspace, cancel_check=check_interrupted)
            self.done.emit(self.project_id, self.job_id, str(output or ""))
        except LocalTaskStopped:
            self.stopped.emit(self.project_id,self.job_id)
        except Exception as exc:
            # Neither provider exception text nor signed URLs enter Qt dialogs.
            self.failed.emit(self.project_id, self.job_id, type(exc).__name__)
        finally:
            if db is not None:
                db.close()


class KieGenerationWorker(QThread):
    """Separate unpaid preparation and one approved paid submission boundary."""
    done = Signal(str,str,str)
    failed = Signal(str,str,str)
    stopped = Signal(str,str)

    def __init__(self,database_path,workspace_root,project_id,job_id,gateway_factory,
                 *,operation,approval_token=None,request=None,recovery=None,parent=None):
        super().__init__(parent)
        if operation not in ('draft','prepare','submit','reconcile'):
            raise ValueError('Invalid media worker operation')
        self.database_path=str(database_path)
        self.workspace_root=Path(workspace_root)
        self.project_id=project_id
        self.job_id=job_id
        self.gateway_factory=gateway_factory
        self.operation=operation
        self.approval_token=approval_token
        self.request=request
        self.recovery=recovery

    def run(self):
        from media_task_control import LocalTaskStopped,check_active
        from media_generation_runtime import prepare_intent
        db=None
        try:
            if self.isInterruptionRequested():raise LocalTaskStopped('Worker stopped')
            db=sqlite3.connect(Path(self.database_path).resolve().as_uri()+'?mode=rw',uri=True,timeout=30)
            db.execute('PRAGMA foreign_keys=ON')
            def workspace(owner):
                if owner!=self.project_id:raise ValueError('Media worker owner mismatch')
                return self.workspace_root
            if self.operation=='draft':
                from media_generation_runtime import create_intent
                def check():
                    if self.isInterruptionRequested():raise LocalTaskStopped('Draft preparation stopped')
                result=create_intent(db,workspace,self.project_id,self.request,job_id=self.job_id,cancel_check=check)
                self.done.emit(self.project_id,self.job_id,result)
                return
            def check_interrupted():
                if self.isInterruptionRequested():
                    raise LocalTaskStopped('Worker interrupted')
            check_interrupted()
            gateway=self.gateway_factory(db,workspace)
            if self.operation=='reconcile':
                from media_submission_recovery import recover_submission
                result=recover_submission(db,self.project_id,self.job_id,self.recovery['provider_id'],
                    gateway.query_task,gateway.cost_gate,evidence=self.recovery['evidence'],confirmed=True)
            elif self.operation=='prepare':
                result=prepare_intent(db,workspace,self.project_id,self.job_id,gateway,
                                      explicit_upload_consent=True,
                                      cancel_check=check_interrupted)
            else:
                check_active(db,self.project_id,self.job_id)
                if self.isInterruptionRequested():raise LocalTaskStopped('Worker stopped')
                result=gateway.start(self.project_id,self.job_id,explicit_paid_consent=True,
                                     approval_token=self.approval_token)
            self.done.emit(self.project_id,self.job_id,result)
        except LocalTaskStopped:
            self.stopped.emit(self.project_id,self.job_id)
        except Exception as exc:
            self.failed.emit(self.project_id,self.job_id,type(exc).__name__)
        finally:
            if db is not None:db.close()
