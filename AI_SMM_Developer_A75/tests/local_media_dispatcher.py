"""Qt facade for durable local media; independent of API keys and paid ledgers."""
import sqlite3
from pathlib import Path
from PySide6.QtCore import QThread,Signal
from local_media_jobs import create_job,execute_job,stop_job,recover_jobs,list_jobs,LocalMediaStopped


class LocalMediaWorker(QThread):
    done=Signal(str,str,str)
    failed=Signal(str,str,str)
    stopped=Signal(str,str)
    operation='local'

    def __init__(self,database_path,workspace_root,owner,job):
        super().__init__()
        self.database_path=str(database_path);self.workspace_root=Path(workspace_root)
        self.project_id=owner;self.job_id=job

    def run(self):
        db=None
        try:
            db=sqlite3.connect(Path(self.database_path).resolve().as_uri()+'?mode=rw',uri=True,timeout=30)
            db.execute('PRAGMA foreign_keys=ON')
            def workspace(owner):
                if owner!=self.project_id:raise ValueError('Local worker owner mismatch')
                return self.workspace_root
            def check():
                if self.isInterruptionRequested():raise LocalMediaStopped('Local worker interrupted')
            check()
            result=execute_job(db,workspace,self.project_id,self.job_id,cancel_check=check)
            self.done.emit(self.project_id,self.job_id,result)
        except LocalMediaStopped:self.stopped.emit(self.project_id,self.job_id)
        except Exception as exc:self.failed.emit(self.project_id,self.job_id,type(exc).__name__)
        finally:
            if db is not None:db.close()


class LocalMediaDispatcher:
    def __init__(self,db,workspace):
        if not callable(workspace):raise TypeError('Local project workspace resolver required')
        self.db=db;self.workspace=workspace

    def worker(self,owner,*,request=None,job_id=None,dialogue_turn_id=None):
        if job_id is None:
            job_id=create_job(self.db,owner,request,dialogue_turn_id=dialogue_turn_id)
        elif not self.owns(owner,job_id):raise ValueError('Local task does not belong to project')
        path=next((row[2] for row in self.db.execute('PRAGMA database_list') if row[1]=='main'),'')
        if not path:raise RuntimeError('Durable local-media database required')
        return LocalMediaWorker(path,self.workspace(owner),owner,job_id)

    def owns(self,owner,job):
        table=self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_media_jobs'").fetchone()
        return bool(table and self.db.execute('SELECT 1 FROM local_media_jobs WHERE id=? AND project_id=?',(job,owner)).fetchone())

    def tasks(self,owner):return list_jobs(self.db,owner)
    def stop(self,owner,job):return stop_job(self.db,owner,job)
    def recover(self,owner):return recover_jobs(self.db,owner)
