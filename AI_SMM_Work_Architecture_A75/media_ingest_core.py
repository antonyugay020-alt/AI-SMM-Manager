"""Qt-free, durable ingestion of project media and references.

Input bytes are staged and fsynced before the SQLite intent is committed;
project-facing file paths are published only by an idempotent replay worker.
On crash, a committed intent can be replayed even when the original import
source has disappeared. No user-supplied filesystem path is replayed from SQL.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
import hashlib
import os
import re
import sqlite3
from stable_file_source import source_identity,descriptor_identity,open_source
from project_file_boundary import require_managed_folder,require_managed_file
from media_spool_lease import spool_lease

_NAME=re.compile(r'[0-9a-f]{32}\.[a-z0-9]{1,12}\Z')
_SHA=re.compile(r'[a-f0-9]{64}\Z')
_KINDS={'asset':'originals','reference':'references','result':'processed','video':'processed'}


def _now():return datetime.now(timezone.utc).isoformat()


def _sync_folder(folder):
    if os.name=='posix':
        fd=os.open(folder,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
        try:os.fsync(fd)
        finally:os.close(fd)


def _digest(path):
    h=hashlib.sha256();length=0
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            h.update(chunk);length+=len(chunk)
    return h.hexdigest(),length


def install(db):
    """Atomically install or upgrade media receipts without committing caller SQL.

    A crash during CREATE/COPY/DROP/RENAME must leave the previous schema and
    its receipts intact. A savepoint also works inside ingest's BEGIN IMMEDIATE.
    """
    db.execute('SAVEPOINT media_ingest_schema')
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS media_ingest_jobs (
            id TEXT PRIMARY KEY,project_id TEXT NOT NULL,kind TEXT NOT NULL
            CHECK(kind IN ('asset','reference','result','video')),source_sha256 TEXT NOT NULL,
            source_size INTEGER NOT NULL,filename TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('pending','done')),
            attempts INTEGER NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL)''')
        # One schema migration for existing installations; rebuild preserves all receipts.
        sql=db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='media_ingest_jobs'").fetchone()[0]
        if "'result'" not in sql or "'video'" not in sql:
            db.execute("""CREATE TABLE media_ingest_jobs_v2 (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL
                CHECK(kind IN ('asset','reference','result','video')), source_sha256 TEXT NOT NULL,
                source_size INTEGER NOT NULL, filename TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('pending','done')),
                attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL)""")
            db.execute("""INSERT INTO media_ingest_jobs_v2
                SELECT id,project_id,kind,source_sha256,source_size,filename,
                       state,attempts,last_error,created_at FROM media_ingest_jobs""")
            db.execute('DROP TABLE media_ingest_jobs')
            db.execute('ALTER TABLE media_ingest_jobs_v2 RENAME TO media_ingest_jobs')
        db.execute('''CREATE INDEX IF NOT EXISTS media_ingest_jobs_pending
            ON media_ingest_jobs(state,project_id,created_at)''')

        db.execute('RELEASE SAVEPOINT media_ingest_schema')
    except BaseException:
        db.execute('ROLLBACK TO SAVEPOINT media_ingest_schema')
        db.execute('RELEASE SAVEPOINT media_ingest_schema')
        raise

def _require_owner(db, project_id):
    """Fail closed on deleted or foreign projects before media I/O.

    Recovery may process an entire queue or one selected project. Neither
    mode may publish an orphan receipt after its owning project was removed.
    """
    if not isinstance(project_id, str) or not re.fullmatch(r'[0-9a-f]{32}', project_id):
        raise ValueError('Invalid project identity')
    if db.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone() is None:
        raise ValueError('Project does not exist in SQLite')


def _root(workspace,project_id):
    if not re.fullmatch(r'[0-9a-f]{32}',str(project_id)):
        raise ValueError('Invalid project identity')
    root=Path(workspace(project_id))
    if not root.is_absolute():raise ValueError('Workspace is not absolute')
    require_managed_folder(root,'.ingest_spool')
    return root


def _spool(root,sha):
    if not _SHA.fullmatch(str(sha)):raise ValueError('Corrupt source digest')
    folder=require_managed_folder(root,'.ingest_spool')
    return folder,require_managed_file(root,folder/sha,('.ingest_spool',))


def _publish_independent_copy(root, source, target, sha, size, kind):
    """Publish fsynced, verified bytes without linking the immutable spool.

    A temporary file in the destination directory is an independent copy.
    Linking that temporary inode into the final name is atomic and refuses
    to overwrite a concurrent publication. The temporary name is removed
    even if verification or publication fails.
    """
    folder=require_managed_folder(root,_KINDS[kind])
    temporary=require_managed_file(
        root,folder/('.publish-'+uuid4().hex+'.tmp'),(_KINDS[kind],))
    try:
        with source.open('rb') as reader, temporary.open('xb') as writer:
            for block in iter(lambda:reader.read(1024*1024),b''):
                writer.write(block)
            writer.flush()
            os.fsync(writer.fileno())
        if _digest(temporary)!=(sha,size):
            raise ValueError('Independent media publication copy failed verification')
        try:
            os.link(temporary,target)
        except FileExistsError:
            # Another worker may have published the same receipt. Do not
            # replace it, and never follow a link planted during the race.
            require_managed_file(root,target,(_KINDS[kind],))
            if not target.is_file() or _digest(target)!=(sha,size):
                raise ValueError('Existing destination content conflicts with registered media')
        _sync_folder(folder)
    finally:
        temporary.unlink(missing_ok=True)


def _source_identity(path):
    """Comparable pathname identity without mixing path-stat and descriptor-stat semantics."""
    return source_identity(path)


def stage_source(root,source,*,check=None):
    """Stage stable bytes with cooperative stop before durable SQL publication."""
    if check is not None and not callable(check):
        raise TypeError('Media staging cancellation check must be callable')
    def checkpoint():
        if check is not None:check()
    checkpoint()
    src=Path(source)
    before=_source_identity(src)
    folder=require_managed_folder(root,'.ingest_spool')
    folder.mkdir(parents=True,exist_ok=True)
    tmp=require_managed_file(root,folder/('.import-'+uuid4().hex+'.tmp'),('.ingest_spool',))
    sha=hashlib.sha256();length=0
    try:
        fd=open_source(src)
        with os.fdopen(fd,'rb') as reader,tmp.open('xb') as writer:
            opened=descriptor_identity(reader.fileno(),src)
            if opened!=before:
                raise ValueError('Source changed before media staging')
            while True:
                checkpoint()
                block=reader.read(1024*1024)
                checkpoint()
                if not block:break
                sha.update(block);length+=len(block);writer.write(block)
            # Metadata alone misses writes through pre-existing memory mappings
            # on Windows. Verify copied bytes against the still-owned handle.
            reader.seek(0);verified=hashlib.sha256()
            while True:
                checkpoint()
                block=reader.read(1024*1024)
                checkpoint()
                if not block:break
                verified.update(block)
            if verified.digest()!=sha.digest():
                raise ValueError('Source changed during media staging (content verification)')
            closed=descriptor_identity(reader.fileno(),src)
            if (closed!=before
                    or _source_identity(src)!=before or length!=before[2]):
                raise ValueError('Source changed during media staging')
            checkpoint()
            writer.flush();os.fsync(writer.fileno())
        checkpoint()
        _,target=_spool(root,sha.hexdigest())
        checkpoint()
        try:os.link(tmp,target)
        except FileExistsError:
            observed,size=_digest(target)
            if observed!=sha.hexdigest() or size!=length:
                raise ValueError('Conflicting immutable media spool')
        _sync_folder(folder)
        return sha.hexdigest(),length
    finally:tmp.unlink(missing_ok=True)


@dataclass(frozen=True)
class IngestReceipt:
    project_id:str
    item_id:str
    final_path:Path
    committed:bool
    published:bool
    duplicate:bool=False


def _ingest_under_lease(db,workspace,source,*,project_id,kind,original_name=None,item_id=None,authorize=None):
    if db.in_transaction:raise RuntimeError('Existing SQLite transaction owns connection')
    if kind not in _KINDS:raise ValueError('Invalid ingest kind')
    _require_owner(db,project_id)
    base=_root(workspace,project_id)
    managed_folder=require_managed_folder(base,_KINDS[kind])
    if kind in ('result','video'):managed_folder.mkdir(parents=True,exist_ok=True)
    src=Path(source)
    ext=src.suffix.lower()
    if not re.fullmatch(r'\.[a-z0-9]{1,12}',ext):raise ValueError('Unsupported filename extension')
    sha,length=stage_source(base,src,check=authorize)
    item_id=item_id or uuid4().hex
    if not re.fullmatch(r'[a-f0-9]{32}',item_id):raise ValueError('Invalid imported item identity')
    folder=require_managed_folder(base,_KINDS[kind])
    dest=require_managed_file(base,folder/(item_id+ext),(_KINDS[kind],))
    if kind in ('result','video') and (dest.exists() or dest.is_symlink()):
        raise FileExistsError('An imported result cannot overwrite an existing file')
    label=original_name if original_name is not None else src.name
    if not isinstance(label,str) or not label or '\x00' in label:raise ValueError('Invalid source display name')
    try:
        db.execute('BEGIN IMMEDIATE')
        if authorize is not None:
            authorize()
        install(db)
        if kind=='asset':
            found=db.execute('SELECT id,stored_path FROM assets WHERE project_id=? AND sha256=?',
                             (project_id,sha)).fetchone()
            if found:
                old_id,old_path=found
                previous=Path(old_path)
                require_managed_file(base,previous,('originals',))
                if not _NAME.fullmatch(previous.name):
                    raise ValueError('Legacy asset has an invalid managed filename')
                published=previous.is_file() and _digest(previous)==(sha,length)
                if not published:
                    existing_job=db.execute('SELECT id FROM media_ingest_jobs WHERE id=? AND project_id=?',
                                            (old_id,project_id)).fetchone()
                    if existing_job:
                        db.execute("UPDATE media_ingest_jobs SET state='pending',last_error='' WHERE id=? AND project_id=?",
                                   (old_id,project_id))
                    else:
                        db.execute('''INSERT INTO media_ingest_jobs
                           (id,project_id,kind,source_sha256,source_size,filename,state,created_at)
                           VALUES(?,?,?,?,?,?,'pending',?)''',
                           (old_id,project_id,'asset',sha,length,previous.name,_now()))
                db.commit()
                if not published:
                    replay(db,workspace,project_id=project_id)
                    published=previous.is_file() and _digest(previous)==(sha,length)
                return IngestReceipt(project_id,old_id,previous,True,published,True)
        if kind=='asset':
            db.execute('INSERT INTO assets(id,project_id,original_name,stored_path,sha256,imported_at) VALUES(?,?,?,?,?,?)',
                       (item_id,project_id,label,str(dest),sha,_now()))
        else:
            db.execute('INSERT INTO project_files(id,project_id,kind,display_name,path,created_at) VALUES(?,?,?,?,?,?)',
                       (item_id,project_id,kind,label,str(dest),_now()))
        db.execute('''INSERT INTO media_ingest_jobs
           (id,project_id,kind,source_sha256,source_size,filename,state,created_at)
           VALUES(?,?,?,?,?,?,'pending',?)''',
           (item_id,project_id,kind,sha,length,dest.name,_now()))
        db.commit()
    except BaseException:
        if db.in_transaction:db.rollback()
        raise
    replay(db,workspace,project_id=project_id)
    state=db.execute('SELECT state FROM media_ingest_jobs WHERE id=? AND project_id=?',
                     (item_id,project_id)).fetchone()
    return IngestReceipt(project_id,item_id,dest,True,bool(state and state[0]=='done'))


def ingest(db,workspace,source,*,project_id,kind,original_name=None,item_id=None,authorize=None):
    """Fence staging, immutable spool identity and SQL intent against GC.

    The same permanent OS lock is held by spool GC. Never release it between
    staging a previously unreferenced blob and committing its first receipt.
    """
    if db.in_transaction:raise RuntimeError('Existing SQLite transaction owns connection')
    if kind not in _KINDS:raise ValueError('Invalid ingest kind')
    _require_owner(db,project_id)
    root=_root(workspace,project_id)
    # Preserve fail-fast validation before creating the permanent lock inode.
    if not re.fullmatch(r'\.[a-z0-9]{1,12}',Path(source).suffix.lower()):
        raise ValueError('Unsupported filename extension')
    with spool_lease(root,check=authorize):
        return _ingest_under_lease(db,workspace,source,project_id=project_id,
            kind=kind,original_name=original_name,item_id=item_id,authorize=authorize)


def replay(db,workspace,*,project_id=None,limit=100):
    if db.in_transaction:raise RuntimeError('Cannot replay import inside a foreign transaction')
    if type(limit) is not int or not 1<=limit<=1000:raise ValueError('Invalid replay limit')
    if project_id is not None:
        _require_owner(db,project_id)
    install(db);db.commit()
    query='''SELECT id,project_id,kind,source_sha256,source_size,filename
        FROM media_ingest_jobs WHERE state='pending' '''
    args=[]
    if project_id is not None:query+='AND project_id=? ';args.append(project_id)
    query+='ORDER BY created_at,id LIMIT ?';args.append(limit)
    completed=failed=0
    for ident,pid,kind,sha,size,name in db.execute(query,args).fetchall():
        try:
            if kind not in _KINDS or not _NAME.fullmatch(name):
                raise ValueError('Corrupt import identity')
            _require_owner(db,pid)
            root=_root(workspace,pid)
            _,source=_spool(root,sha)
            if _digest(source)!=(sha,size):raise ValueError('Spool bytes are missing or corrupted')
            folder=require_managed_folder(root,_KINDS[kind])
            target=require_managed_file(root,folder/name,(_KINDS[kind],))
            table,column=('assets','stored_path') if kind=='asset' else ('project_files','path')
            selected='stored_path,sha256' if kind=='asset' else 'path,kind'
            row=db.execute('SELECT '+selected+' FROM '+table+' WHERE id=? AND project_id=?',
                           (ident,pid)).fetchone()
            if not row or row[0]!=str(target) or row[1]!=(sha if kind=='asset' else kind):
                raise ValueError('Media registry identity changed')
            folder.mkdir(parents=True,exist_ok=True)
            require_managed_file(root,target,(_KINDS[kind],))
            if target.exists():
                if not target.is_file() or _digest(target)!=(sha,size):
                    raise ValueError('Existing destination content conflicts with registered media')
            else:
                _publish_independent_copy(root,source,target,sha,size,kind)
            if _digest(target)!=(sha,size):raise ValueError('Media readback failed')
            db.execute("UPDATE media_ingest_jobs SET state='done',last_error='' WHERE id=? AND project_id=? AND state='pending'",
                       (ident,pid));db.commit();completed+=1
        except Exception as exc:
            if db.in_transaction:db.rollback()
            db.execute("UPDATE media_ingest_jobs SET attempts=attempts+1,last_error=? WHERE id=? AND project_id=? AND state='pending'",
                       (str(exc)[:500],ident,pid));db.commit();failed+=1
    pending_sql="SELECT COUNT(*) FROM media_ingest_jobs WHERE state='pending'"
    pending_args=()
    if project_id is not None:
        pending_sql+=" AND project_id=?"
        pending_args=(project_id,)
    pending=db.execute(pending_sql,pending_args).fetchone()[0]
    return dict(completed=completed,failed=failed,pending=pending)


def _prune_unreferenced_spool_under_lease(db,workspace,*,project_id,max_delete=100,min_age_seconds=86400):
    """Bounded GC of crash-before-commit leftovers; never remove referenced blobs.

    A staged blob may precede its still-uncommitted SQLite intent. Age-based
    quarantine protects that window across processes. Completed and pending
    media retain their content-addressed spool for restart self-healing.
    """
    import time
    if db.in_transaction:raise RuntimeError('Spool GC requires an unowned connection')
    if type(max_delete) is not int or not 1<=max_delete<=1000:
        raise ValueError('Invalid GC limit')
    if type(min_age_seconds) not in (int,float) or min_age_seconds<86400:
        raise ValueError('GC retention must be at least 24 hours')
    _require_owner(db,project_id)
    root=_root(workspace,project_id)
    folder=require_managed_folder(root,'.ingest_spool')
    if not folder.exists():return {'deleted':0,'retained':0}
    if not folder.is_dir():raise ValueError('Spool location is not a directory')
    install(db);db.commit()
    now=time.time();removed=kept=0
    for candidate in folder.iterdir():
        if removed>=max_delete:break
        if candidate.is_symlink() or getattr(candidate,'is_junction',lambda:False)():
            raise ValueError('Spool contains an unsafe link')
        if not candidate.is_file():raise ValueError('Spool contains an unexpected directory')
        require_managed_file(root,candidate,('.ingest_spool',))
        name=candidate.name
        if name=='.lease':continue  # permanent cross-process lifetime lock
        if not _SHA.fullmatch(name):
            kept+=1;continue  # temporary import may still be in progress
        if now-candidate.stat().st_mtime<min_age_seconds:
            kept+=1;continue
        if db.execute('SELECT 1 FROM media_ingest_jobs WHERE project_id=? AND source_sha256=? LIMIT 1',
                      (project_id,name)).fetchone():
            kept+=1;continue
        if db.execute('SELECT 1 FROM assets WHERE project_id=? AND sha256=? LIMIT 1',
                      (project_id,name)).fetchone():
            kept+=1;continue
        candidate.unlink();removed+=1
    if removed:_sync_folder(folder)
    return {'deleted':removed,'retained':kept}


def prune_unreferenced_spool(db,workspace,*,project_id,max_delete=100,min_age_seconds=86400):
    """Serialize reference checks and deletion with every durable import intent."""
    if db.in_transaction:raise RuntimeError('Spool GC requires an unowned connection')
    if type(max_delete) is not int or not 1<=max_delete<=1000:
        raise ValueError('Invalid GC limit')
    if type(min_age_seconds) not in (int,float) or min_age_seconds<86400:
        raise ValueError('GC retention must be at least 24 hours')
    _require_owner(db,project_id)
    root=_root(workspace,project_id)
    folder=require_managed_folder(root,'.ingest_spool')
    if not folder.exists():return {'deleted':0,'retained':0}
    with spool_lease(root):
        return _prune_unreferenced_spool_under_lease(db,workspace,
            project_id=project_id,max_delete=max_delete,min_age_seconds=min_age_seconds)
