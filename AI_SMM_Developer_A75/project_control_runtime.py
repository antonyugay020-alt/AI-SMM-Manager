"""Project-scoped destructive operations, independent from Qt and language models.

A destructive command is never inferred here. The caller supplies a confirmed
scope and explicit project identity; a complete backup must exist BEFORE the
recoverable deletion protocol starts. Backup failures never mutate project data.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import hashlib
import base64
import re
import json
import os
import sqlite3
import zipfile

from project_deletion import delete_project_data
from project_file_boundary import reject_path_link

SCOPES = frozenset({'documents','project','assets','chat'})

@dataclass(frozen=True)
class DeletionReceipt:
    project_id: str
    scope: str
    backup: Path
    backup_sha256: str
    selected_ids: tuple[str,...]

class ProjectControlRuntime:
    def __init__(self, db:sqlite3.Connection, workspace, backup_root):
        self.db,self.workspace,self.backup_root=db,workspace,Path(backup_root)

    def _require(self,project_id,scope,selected_ids):
        if self.db.in_transaction:
            raise RuntimeError('Unfinished SQLite transaction; deletion not permitted')
        if scope not in SCOPES:raise ValueError('Invalid deletion scope')
        if not isinstance(project_id,str) or not re.fullmatch(r'[0-9a-f]{32}',project_id):
            raise ValueError('Unsafe project identity')
        if not self.db.execute('SELECT 1 FROM projects WHERE id=?',(project_id,)).fetchone():
            raise ValueError('Project does not exist')
        if scope=='project':
            from project_deletion import _reject_active_project_workers
            _reject_active_project_workers(self.db,project_id)
        if scope=='documents':
            if not selected_ids or len(set(selected_ids))!=len(selected_ids):
                raise ValueError('Selected documents must be distinct and nonempty')
            for doc_id in selected_ids:
                if not self.db.execute('SELECT 1 FROM manager_documents WHERE id=? AND project_id=?',
                        (doc_id,project_id)).fetchone():
                    raise ValueError('Document is not part of the selected project')
        elif selected_ids:
            raise ValueError('Only document deletion can select document identities')

    def _project_tables(self):
        tables=[]
        for (name,) in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            if name.startswith('sqlite_'):continue
            quoted='"'+name.replace('"','""')+'"'
            if 'project_id' in {row[1] for row in self.db.execute('PRAGMA table_info('+quoted+')')}:
                tables.append(name)
        return tuple(tables)

    def preview(self,project_id,scope,selected_ids=()):
        selected_ids=tuple(selected_ids)
        self._require(project_id,scope,selected_ids)
        if scope=='documents':
            result=[]
            for ident in selected_ids:
                record=self.db.execute('SELECT kind,title FROM manager_documents WHERE id=? AND project_id=?',
                                       (ident,project_id)).fetchone()
                result.append((ident,*record))
            return tuple(result)
        if scope=='assets':names=('assets',)
        elif scope=='chat':names=('messages',)
        else:names=tuple(name for name in self._project_tables() if name!='project_locations')
        result=[]
        for name in names:
            quoted='"'+name.replace('"','""')+'"'
            count=self.db.execute('SELECT COUNT(*) FROM '+quoted+' WHERE project_id=?', (project_id,)).fetchone()[0]
            result.append((name,count))
        return tuple(result)

    def backup_and_delete(self,project_id,scope,*,selected_ids=()):
        ids=tuple(selected_ids)
        self._require(project_id,scope,ids)
        folder=Path(self.workspace(project_id))
        reject_path_link(folder)
        if not folder.is_dir():raise ValueError('Project workspace is absent')
        backup_root=self.backup_root
        reject_path_link(backup_root)
        # Reject a backup location nested in the project before creating folders.
        if backup_root == folder or backup_root.is_relative_to(folder):
            raise ValueError('Backup directory cannot reside in the deleted project')
        backup_root.mkdir(parents=True,exist_ok=True)
        path=backup_root/('project_'+project_id+'_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex+'.zip')
        reject_path_link(backup_root)
        # This preflight executes after the deletion runtime acquired SQLite's
        # write lock. The backup and deletion therefore share one DB snapshot.
        state={}
        def durable_backup():
            hashes={}
            try:
                with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED) as archive:
                    snapshot={}
                    for table in ('projects',*self._project_tables()):
                        quoted='"'+table.replace('"','""')+'"'
                        cols=[row[1] for row in self.db.execute('PRAGMA table_info('+quoted+')')]
                        if table=='projects':
                            rows=self.db.execute('SELECT * FROM projects WHERE id=?',(project_id,)).fetchall()
                        elif 'project_id' in cols:
                            rows=self.db.execute('SELECT * FROM '+quoted+' WHERE project_id=?',(project_id,)).fetchall()
                        else:continue
                        def encode(v):
                            if isinstance(v,bytes):return {'__sqlite_blob_b64__':base64.b64encode(v).decode('ascii')}
                            if isinstance(v,(str,int,float)) or v is None:return v
                            raise TypeError('Unknown SQLite value in backup')
                        schema=self.db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
                        snapshot[table]={'columns':cols,'rows':[[encode(v) for v in row] for row in rows],
                                         'schema_sql':schema[0] if schema else None}
                    archive.writestr('database_rows.json',json.dumps({'format_version':2,'tables':snapshot},ensure_ascii=False))
                    archive.writestr('deletion_scope.json',json.dumps({'project_id':project_id,'scope':scope,
                                                                       'selected_ids':ids},ensure_ascii=False))
                    for asset in sorted(folder.rglob('*')):
                        if asset.is_symlink() or getattr(asset,'is_junction',lambda:False)():
                            raise ValueError('Linked file inside project; backup aborted')
                        if asset.is_file():
                            name='project_files/'+asset.relative_to(folder).as_posix()
                            data=asset.read_bytes()
                            archive.writestr(name,data)
                            hashes[name]=hashlib.sha256(data).hexdigest()
                    archive.writestr('files_sha256.json',json.dumps(hashes,sort_keys=True))
                with zipfile.ZipFile(path) as archive:
                    if archive.testzip() is not None:raise IOError('Backup failed integrity check')
                    if not {'database_rows.json','deletion_scope.json','files_sha256.json'}<=set(archive.namelist()):
                        raise IOError('Backup manifest missing')
                with path.open('r+b') as handle:
                    os.fsync(handle.fileno())
                state['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            except BaseException:
                if path.exists():path.unlink()
                raise
        # The deletion service first checks and recovers prior staged work,
        # then locks SQLite and invokes preflight before touching user data.
        delete_project_data(self.db,project_id,scope,folder,selected_ids=ids,
                            preflight=durable_backup)
        digest=state['sha256']
        if scope=='documents':
            for doc_id in ids:
                if self.db.execute('SELECT 1 FROM manager_documents WHERE id=? AND project_id=?',
                                   (doc_id,project_id)).fetchone():
                    raise RuntimeError('Deletion was not verified')
        elif scope=='chat':
            if self.db.execute('SELECT 1 FROM messages WHERE project_id=?',(project_id,)).fetchone():
                raise RuntimeError('Chat deletion was not verified')
        elif scope=='project':
            if (self.db.execute('SELECT 1 FROM projects WHERE id=?',(project_id,)).fetchone() or
                    self.db.execute('SELECT 1 FROM project_locations WHERE project_id=?',(project_id,)).fetchone()):
                raise RuntimeError('Project registry deletion was not verified')
        elif scope=='assets':
            if self.db.execute('SELECT 1 FROM assets WHERE project_id=?',(project_id,)).fetchone():
                raise RuntimeError('Assets deletion was not verified')
        return DeletionReceipt(project_id,scope,path,digest,ids)
