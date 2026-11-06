"""Transactional, replayable removal of imported/generated project results.

SQLite owns deletion intent; a project file is moved to the project's trash
only after that intent commits. Replaying a crash after the rename verifies
immutable bytes, then atomically removes its registry row and confirms the
intent. Unrelated projects and unregistered files cannot be removed.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import os
import sqlite3
from sqlite_transaction_core import own
from project_file_boundary import require_managed_file,require_managed_folder,reject_path_link


def _now():return datetime.now(timezone.utc).isoformat()


def _digest(path):
    digest=sha256();size=0
    with Path(path).open('rb') as reader:
        for chunk in iter(lambda:reader.read(1024*1024),b''):
            digest.update(chunk);size+=len(chunk)
    return digest.hexdigest(),size


def install(db):
    db.execute('''CREATE TABLE IF NOT EXISTS project_result_retirements (
        intent_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, result_id TEXT NOT NULL,
        source_path TEXT NOT NULL, destination_path TEXT NOT NULL, sha256 TEXT NOT NULL,
        source_size INTEGER NOT NULL, status TEXT NOT NULL
        CHECK(status IN ('pending','done')), created_at TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        UNIQUE(project_id,result_id))''')
    db.execute('''CREATE INDEX IF NOT EXISTS project_retirements_pending
        ON project_result_retirements(status,project_id,created_at,intent_id)''')


@dataclass(frozen=True)
class RetirementReport:
    committed: tuple[str,...]
    failed: tuple[tuple[str,str],...]


class ProjectResultRetirement:
    def __init__(self,db,workspace):
        if not isinstance(db,sqlite3.Connection) or not callable(workspace):
            raise TypeError('Result retirement needs SQLite and workspace resolver')
        self.db,self.workspace=db,workspace

    def _free(self):
        if self.db.in_transaction:
            raise RuntimeError('Result retirement cannot commit another transaction')

    def _project(self,project_id):
        if not isinstance(project_id,str) or not project_id or not self.db.execute(
            'SELECT 1 FROM projects WHERE id=?',(project_id,)).fetchone():
            raise ValueError('Unknown result project')
        root=Path(self.workspace(project_id))
        reject_path_link(root)
        return root

    def request(self,project_id,paths):
        self._free()
        paths=tuple(str(p) for p in paths)
        if not paths or len(set(paths))!=len(paths):
            raise ValueError('Select unique registered results')
        with own(self.db):
            root=self._project(project_id)
            install(self.db)
            trash=require_managed_folder(root,'.trash/results')
            intents=[]
            for raw in paths:
                source=require_managed_file(root,raw,('processed','ready','video'))
                row=self.db.execute('''SELECT id FROM project_files
                    WHERE project_id=? AND kind='result' AND path=?''',
                    (project_id,str(source))).fetchone()
                if not row:
                    raise ValueError('Selected result is not registered in this project')
                prior=self.db.execute('''SELECT intent_id,status FROM project_result_retirements
                    WHERE project_id=? AND result_id=?''',(project_id,row[0])).fetchone()
                if prior:
                    if prior[1]!='pending':raise ValueError('Result already removed')
                    intents.append(prior[0]);continue
                if not source.is_file():raise FileNotFoundError('Registered result missing')
                digest,size=_digest(source)
                ident=uuid4().hex
                target=require_managed_file(root,trash/(ident+'_'+source.name),('.trash/results',))
                if target.exists() or target.is_symlink():raise FileExistsError('Result trash path occupied')
                self.db.execute('''INSERT INTO project_result_retirements VALUES
                    (?,?,?,?,?,?,?,'pending',?)''',
                    (ident,project_id,row[0],str(source),str(target),digest,size,_now()))
                intents.append(ident)
        return self.recover(project_id=project_id,intent_ids=tuple(intents))

    def recover(self,*,project_id=None,intent_ids=None,limit=100):
        self._free()
        if type(limit) is not int or not 1<=limit<=1000:raise ValueError('Invalid retirement batch limit')
        if project_id is not None:self._project(project_id)
        query='''SELECT intent_id,project_id,result_id,source_path,destination_path,sha256,source_size
            FROM project_result_retirements WHERE status='pending' '''
        args=[]
        if project_id is not None:query+='AND project_id=? ';args.append(project_id)
        if intent_ids is not None:
            ids=tuple(intent_ids)
            if not ids:return RetirementReport((),())
            query+='AND intent_id IN ('+','.join('?' for _ in ids)+') ';args.extend(ids)
        query+='ORDER BY created_at,intent_id LIMIT ?';args.append(limit)
        done=[];failed=[]
        for ident,pid,result_id,raw_source,raw_target,digest,size in self.db.execute(query,args).fetchall():
            try:
                root=self._project(pid)
                source=require_managed_file(root,raw_source,('processed','ready','video'))
                target=require_managed_file(root,raw_target,('.trash/results',))
                trash=require_managed_folder(root,'.trash/results')
                existing=self.db.execute('''SELECT id,path,kind FROM project_files
                    WHERE project_id=? AND id=?''',(pid,result_id)).fetchone()
                if existing!=(result_id,str(source),'result'):
                    raise ValueError('Result registry no longer matches its retirement intent')
                if source.exists() or source.is_symlink():
                    if not source.is_file() or source.is_symlink() or _digest(source)!=(digest,size):
                        raise ValueError('Result changed since retirement intent was accepted')
                    if target.exists() or target.is_symlink():
                        raise FileExistsError('Both source and trash destination exist')
                    trash.mkdir(parents=True,exist_ok=True)
                    require_managed_folder(root,'.trash/results')
                    os.replace(source,target)
                if not target.is_file() or target.is_symlink() or _digest(target)!=(digest,size):
                    raise ValueError('Retired file is missing or has altered bytes')
                with own(self.db):
                    expected=self.db.execute('''SELECT project_id,result_id,source_path,destination_path,status
                        FROM project_result_retirements WHERE intent_id=?''',(ident,)).fetchone()
                    if expected!=(pid,result_id,str(source),str(target),'pending'):
                        raise RuntimeError('Retirement intent changed before confirmation')
                    changed=self.db.execute('''DELETE FROM project_files
                        WHERE project_id=? AND id=? AND kind='result' AND path=?''',
                        (pid,result_id,str(source))).rowcount
                    if changed!=1:raise RuntimeError('Result registry removal not confirmed')
                    self.db.execute("UPDATE project_result_retirements SET status='done' WHERE intent_id=?",(ident,))
                done.append(ident)
            except (OSError,ValueError,RuntimeError,sqlite3.Error) as exc:
                failed.append((ident,str(exc)))
        return RetirementReport(tuple(done),tuple(failed))
