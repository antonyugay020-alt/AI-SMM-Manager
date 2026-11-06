"""Durable revision and derived-export lifecycle for user-edited documents.

The canonical document and its pending export intent commit in one SQLite
transaction. The filesystem is a replayable projection, not a second database.
A failed renderer never rolls back a committed user edit. A later restart
replays pending exports without trusting persisted paths or callbacks.
"""
from __future__ import annotations
from sqlite_transaction_core import own
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from project_file_boundary import require_managed_file

FOLDERS={'strategy':'strategies','plans':'diagrams','calendar':'content_plans',
         'tasks':'texts','analytics':'reports'}


def _stem(title, document_id):
    safe=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',str(title)).strip(' .')[:75] or 'document'
    return safe+'_'+document_id[:8]


def _paths(root, ident, kind, title):
    if kind not in FOLDERS:
        raise ValueError('Unknown document kind')
    stem=_stem(title,ident)
    return (require_managed_file(root,Path(root)/FOLDERS[kind]/(stem+'.txt'),(FOLDERS[kind],)),
            require_managed_file(root,Path(root)/'pdf'/(stem+'.pdf'),('pdf',)),
            require_managed_file(root,Path(root)/'diagrams'/(stem+'.png'),('diagrams',)))


def _revision(kind,title,content,diagram_png):
    h=hashlib.sha256()
    for part in (kind,title,content):
        b=part.encode('utf-8');h.update(len(b).to_bytes(8,'big'));h.update(b)
    if diagram_png is not None:
        h.update(hashlib.sha256(diagram_png).digest())
    return h.hexdigest()


def install(db):
    db.execute('''CREATE TABLE IF NOT EXISTS manager_document_exports (
         document_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
         revision_sha256 TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
         diagram_png BLOB, orphan_json TEXT NOT NULL DEFAULT '[]',
         state TEXT NOT NULL CHECK(state IN ('pending','done')),
         attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
         created_at TEXT NOT NULL)''')
    db.execute('''CREATE INDEX IF NOT EXISTS manager_document_exports_pending
        ON manager_document_exports(state,created_at)''')


@dataclass(frozen=True)
class SaveReceipt:
    project_id: str
    document_id: str
    revision_sha256: str
    committed: bool
    exported: bool


def register(db,root,*,project_id,document_id,kind,title,content,updated_at,diagram_png=None):
    if not db.in_transaction:raise RuntimeError('Editor change requires owned SQLite transaction')
    if not (isinstance(project_id,str) and project_id and isinstance(document_id,str) and document_id):
        raise ValueError('Invalid project/document identity')
    if kind not in FOLDERS or not isinstance(title,str) or not title.strip() or not isinstance(content,str):
        raise ValueError('Invalid edited document')
    if diagram_png is not None:
        if not isinstance(diagram_png,bytes) or not diagram_png.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('Diagram must be valid PNG bytes')
    base=Path(root)
    nextpaths=_paths(base,document_id,kind,title)
    row=db.execute('SELECT kind,title FROM manager_documents WHERE id=? AND project_id=?',
                   (document_id,project_id)).fetchone()
    if not row:raise ValueError('Document does not belong to project')
    oldpaths=_paths(base,document_id,row[0],row[1])
    install(db)
    prior=db.execute('SELECT orphan_json FROM manager_document_exports WHERE document_id=? AND project_id=?',
                     (document_id,project_id)).fetchone()
    orphans=json.loads(prior[0]) if prior else []
    if not isinstance(orphans,list):raise ValueError('Corrupt old export intent')
    # All orphans are recomputed from kind/title, never from SQLite paths.
    if (row[0],row[1])!=(kind,title):orphans.append([row[0],row[1]])
    # Same 8-character UUID suffix can collide: reject collisions deterministically.
    for other_id,other_kind,other_title in db.execute('''SELECT id,kind,title FROM manager_documents
         WHERE project_id=? AND id<>?''',(project_id,document_id)):
        if other_kind in FOLDERS and set(_paths(base,other_id,other_kind,other_title)) & set(nextpaths):
            raise ValueError('Another document owns the target export name')
    # Other documents' queued former names are still reserved until their
    # deletion/export replay is committed. Current names alone are insufficient
    # to prevent a rename stealing a pending prior revision's file slot.
    for other_id,queued_orphans in db.execute('''SELECT document_id,orphan_json FROM manager_document_exports
         WHERE project_id=? AND document_id<>?''',(project_id,document_id)):
        pairs=json.loads(queued_orphans)
        if not isinstance(pairs,list):raise ValueError('Corrupt foreign export reservation')
        for pair in pairs:
            if (not isinstance(pair,list) or len(pair)!=2 or
                    pair[0] not in FOLDERS or not isinstance(pair[1],str)):
                raise ValueError('Corrupt foreign export reservation')
            if set(_paths(base,other_id,pair[0],pair[1])) & set(nextpaths):
                raise ValueError('Export name reserved by another document revision')
    allowed=set(oldpaths)
    for pair in orphans:
        if not isinstance(pair,list) or len(pair)!=2 or pair[0] not in FOLDERS or not isinstance(pair[1],str):
            raise ValueError('Corrupt queued orphan identity')
        allowed.update(_paths(base,document_id,pair[0],pair[1]))
    for path in nextpaths:
        if path not in allowed and path.exists():
            raise FileExistsError('Destination already belongs to another file: '+str(path))
    revision=_revision(kind,title,content,diagram_png)
    changed=db.execute('''UPDATE manager_documents SET kind=?,title=?,content=?,updated_at=?
        WHERE id=? AND project_id=?''',(kind,title,content,updated_at,document_id,project_id))
    if changed.rowcount!=1:raise ValueError('Document update changed no rows')
    unique=sorted(set((x[0],x[1]) for x in orphans))
    db.execute('''INSERT INTO manager_document_exports
        (document_id,project_id,revision_sha256,kind,title,diagram_png,orphan_json,state,created_at)
        VALUES(?,?,?,?,?,?,?,'pending',?)
        ON CONFLICT(document_id) DO UPDATE SET
          project_id=excluded.project_id,revision_sha256=excluded.revision_sha256,
          kind=excluded.kind,title=excluded.title,diagram_png=excluded.diagram_png,
          orphan_json=excluded.orphan_json,state='pending',last_error='',created_at=excluded.created_at''',
        (document_id,project_id,revision,kind,title,diagram_png,
         json.dumps(unique,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
    return revision


def save(db, workspace,exporter,*,project_id,document_id,kind,title,content,updated_at,diagram_png=None):
    """Accept an edit durably, then attempt immediate best-effort export."""
    if db.in_transaction:raise RuntimeError('A different transaction owns the SQLite connection')
    with own(db):
        revision=register(db,workspace(project_id),project_id=project_id,document_id=document_id,
                          kind=kind,title=title,content=content,updated_at=updated_at,diagram_png=diagram_png)
    report=drain(db,workspace,exporter,project_id=project_id)
    row=db.execute('SELECT state,revision_sha256 FROM manager_document_exports WHERE document_id=? AND project_id=?',
                   (document_id,project_id)).fetchone()
    return SaveReceipt(project_id,document_id,revision,True,
                       bool(row and row[0]=='done' and row[1]==revision))


def drain(db,workspace,exporter,*,project_id=None,limit=100):
    if db.in_transaction:raise RuntimeError('Cannot replay exports inside foreign transaction')
    if type(limit) is not int or not 1<=limit<=1000:raise ValueError('Invalid replay limit')
    install(db);db.commit()
    q='''SELECT document_id,project_id,revision_sha256,kind,title,diagram_png,orphan_json
         FROM manager_document_exports WHERE state='pending' '''
    args=[]
    if project_id is not None:q+='AND project_id=? ';args.append(project_id)
    q+='ORDER BY created_at,document_id LIMIT ?';args.append(limit)
    done=failed=0
    for ident,pid,revision,kind,title,png,orphans_json in db.execute(q,args).fetchall():
        try:
            row=db.execute('SELECT kind,title,content FROM manager_documents WHERE id=? AND project_id=?',
                           (ident,pid)).fetchone()
            if not row or (row[0],row[1])!=(kind,title) or _revision(kind,title,row[2],png)!=revision:
                raise ValueError('Document changed or export identity corrupted')
            root=Path(workspace(pid))
            txt,pdf,diagram=_paths(root,ident,kind,title)
            exporter(pid,ident,kind,title,row[2],diagram_png=png)
            if txt.read_bytes()!=row[2].encode('utf-8'):
                raise RuntimeError('TXT export readback mismatch')
            if pdf.stat().st_size<20 or not pdf.read_bytes().startswith(b'%PDF-'):
                raise RuntimeError('PDF export invalid')
            if png is not None and diagram.read_bytes()!=png:
                raise RuntimeError('PNG export readback mismatch')
            orphans=json.loads(orphans_json)
            if not isinstance(orphans,list):raise ValueError('Corrupt orphan list')
            protected=set(_paths(root,ident,kind,title))
            for orphan_kind,orphan_title in orphans:
                for old in _paths(root,ident,orphan_kind,orphan_title):
                    if old in protected:continue
                    if old.is_symlink() or getattr(old,'is_junction',lambda:False)():
                        raise RuntimeError('Orphan path became a link')
                    if old.exists():
                        if not old.is_file():raise RuntimeError('Orphan is not a regular file')
                        old.unlink()
            db.execute("UPDATE manager_document_exports SET state='done',last_error='' WHERE document_id=? AND project_id=? AND revision_sha256=? AND state='pending'",
                       (ident,pid,revision))
            db.commit();done+=1
        except Exception as exc:
            if db.in_transaction:db.rollback()
            db.execute("UPDATE manager_document_exports SET attempts=attempts+1,last_error=? WHERE document_id=? AND project_id=? AND state='pending'",
                       (str(exc)[:500],ident,pid));db.commit();failed+=1
    pending=db.execute("SELECT count(*) FROM manager_document_exports WHERE state='pending'").fetchone()[0]
    return {'completed':done,'failed':failed,'pending':pending}
