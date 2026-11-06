"""Single Qt-independent owner of publication queue, scheduling and media review.

No background publishing or external API calls occur here. Every mutation is
project-scoped, verified against local media ownership and transactionally
committed. GUI controls collect input and display receipts only.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sqlite3
from uuid import uuid4
from sqlite_transaction_core import own
from project_file_boundary import require_managed_file,require_managed_folder,reject_path_link

_ALLOWED_MEDIA=('ready','processed','video')
_ALLOWED_REVIEW=frozenset({'Ожидает проверки','Одобрен','Отклонён'})


def _utc():
    return datetime.now(timezone.utc).isoformat()


def install(db):
    db.execute("""CREATE TABLE IF NOT EXISTS publication_media_rejections (
        intent_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
        source_path TEXT NOT NULL, destination_path TEXT NOT NULL,
        sha256 TEXT NOT NULL, size INTEGER NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','done')),
        created_at TEXT NOT NULL, completed_at TEXT,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE)""")
    db.execute("""CREATE TABLE IF NOT EXISTS publication_ready_ingest (
        intent_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
        spool_path TEXT NOT NULL, destination_path TEXT NOT NULL,
        sha256 TEXT NOT NULL, size INTEGER NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','done')),
        created_at TEXT NOT NULL, completed_at TEXT,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE)""")
    db.execute("""CREATE INDEX IF NOT EXISTS publication_ready_ingest_replay
        ON publication_ready_ingest(state,project_id,created_at)""")
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS publication_rejection_pending
        ON publication_media_rejections(project_id,source_path) WHERE state='pending'""")
    db.execute("""CREATE INDEX IF NOT EXISTS publication_rejection_replay
        ON publication_media_rejections(state,project_id,created_at)""")


def file_digest(path):
    digest=hashlib.sha256()
    length=0
    with Path(path).open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):
            digest.update(block)
            length+=len(block)
    return digest.hexdigest(),length


class PublicationRuntime:
    def __init__(self, db: sqlite3.Connection, workspace):
        if not isinstance(db, sqlite3.Connection) or not callable(workspace):
            raise TypeError('Publication service requires shared SQLite and a workspace resolver')
        self.db,self.workspace=db,workspace

    def _project(self, pid):
        if not isinstance(pid,str) or not pid.strip():
            raise ValueError('Missing project identity')
        if not self.db.execute('SELECT 1 FROM projects WHERE id=?',(pid,)).fetchone():
            raise ValueError('Unknown project')

    def _file(self,pid,path):
        file=require_managed_file(Path(self.workspace(pid)),path,_ALLOWED_MEDIA)
        if not file.is_file():
            raise ValueError('Missing owned publication media')
        return str(file)

    def _exclusive(self):
        if self.db.in_transaction:
            raise RuntimeError('Another operation owns the SQLite transaction')

    def import_ready_sources(self,pid,sources,*,timestamp=None):
        """Stage immutable copies before persisting replayable publication intents.

        Original source paths are never deleted. A partial batch yields separate
        verified receipts per source; each pending file can be recovered after
        a crash without the original file or its external drive.
        """
        self._exclusive()
        self._project(pid)
        root=Path(self.workspace(pid))
        ready=require_managed_folder(root,'ready')
        spool=require_managed_folder(root,'ready_spool')
        for folder in (ready,spool):
            folder.mkdir(parents=True,exist_ok=True)
            reject_path_link(folder)
        done=[];failed=[]
        extensions={'.jpg','.jpeg','.png','.webp','.mp4','.mov'}
        for raw in tuple(sources):
            intent=None
            try:
                source=Path(raw)
                reject_path_link(source)
                if not source.is_file() or source.suffix.lower() not in extensions:
                    raise ValueError('Unsupported or missing publication input')
                # Existing project-owned ready media is registered without a duplicate.
                if source.is_absolute() and source.parent==ready:
                    with own(self.db):
                        self.db.execute("""INSERT OR IGNORE INTO publication_media_review
                            (project_id,media_path,review_status,updated_at)
                            VALUES(?,?,?,?)""",(pid,str(source),'Ожидает проверки',timestamp or _utc()))
                    done.append(str(source))
                    continue
                intent=uuid4().hex
                stage=spool/(intent+'.blob')
                destination=ready/(intent+'_'+source.name)
                if stage.exists() or destination.exists() or stage.is_symlink() or destination.is_symlink():
                    raise FileExistsError('Publication staging path is occupied')
                digest=hashlib.sha256()
                size=0
                try:
                    with stage.open('xb') as output, source.open('rb') as inp:
                        for block in iter(lambda:inp.read(1024*1024),b''):
                            output.write(block)
                            digest.update(block)
                            size+=len(block)
                        output.flush()
                        os.fsync(output.fileno())
                    if file_digest(stage)!=(digest.hexdigest(),size):
                        raise IOError('Publication media changed during staging')
                except BaseException:
                    if stage.is_file() and not stage.is_symlink():stage.unlink()
                    raise
                with own(self.db):
                    install(self.db)
                    self._project(pid)
                    self.db.execute("""INSERT INTO publication_ready_ingest
                        (intent_id,project_id,spool_path,destination_path,sha256,size,state,created_at)
                        VALUES(?,?,?,?,?,?,'pending',?)""",
                        (intent,pid,str(stage),str(destination),digest.hexdigest(),size,timestamp or _utc()))
                result=self.recover_ready_ingest(project_id=pid,intent_ids=(intent,))
                if result['done']:
                    done.append(str(destination))
                else:
                    failed.append((str(raw),str(result['failed'])))
            except (OSError,ValueError,sqlite3.Error,RuntimeError) as exc:
                # A committed intent retains its stage file for future replay.
                failed.append((str(raw),str(exc)))
        return {'done':tuple(done),'failed':tuple(failed)}

    def recover_ready_ingest(self,*,project_id=None,intent_ids=None,limit=100):
        """Durably project staged media into ready and its review ledger."""
        self._exclusive()
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError('Invalid recovery batch size')
        install(self.db)
        query="""SELECT intent_id,project_id,spool_path,destination_path,sha256,size
            FROM publication_ready_ingest WHERE state='pending'"""
        args=[]
        if project_id is not None:
            self._project(project_id)
            query+=' AND project_id=?';args.append(project_id)
        if intent_ids is not None:
            ids=tuple(intent_ids)
            if not ids:return {'done':(), 'failed':()}
            query+=' AND intent_id IN ('+','.join('?' for _ in ids)+')';args.extend(ids)
        query+=' ORDER BY created_at,intent_id LIMIT ?';args.append(limit)
        done=[];failed=[]
        for ident,pid,raw_spool,raw_dest,sha,size in self.db.execute(query,args).fetchall():
            try:
                root=Path(self.workspace(pid))
                stage=require_managed_file(root,raw_spool,('ready_spool',))
                dest=require_managed_file(root,raw_dest,('ready',))
                ready=require_managed_folder(root,'ready')
                ready.mkdir(parents=True,exist_ok=True)
                reject_path_link(ready)
                source_exists=stage.is_file();dest_exists=dest.is_file()
                if source_exists and dest_exists:
                    raise FileExistsError('Both staged and published media exist')
                if not source_exists and not dest_exists:
                    raise FileNotFoundError('Media stage and final path are both missing')
                if source_exists:
                    if file_digest(stage)!=(sha,size):
                        raise ValueError('Staged media failed checksum verification')
                    if dest.exists() or dest.is_symlink():
                        raise FileExistsError('Destination became occupied')
                    os.replace(stage,dest)
                if file_digest(dest)!=(sha,size):
                    raise ValueError('Final media failed checksum verification')
                with own(self.db):
                    current=self.db.execute("""SELECT project_id,spool_path,destination_path,state
                        FROM publication_ready_ingest WHERE intent_id=?""",(ident,)).fetchone()
                    if current!=(pid,raw_spool,raw_dest,'pending'):
                        raise ValueError('Ingest intent no longer matches this project')
                    self.db.execute("""INSERT OR IGNORE INTO publication_media_review
                        (project_id,media_path,review_status,updated_at)
                        VALUES(?,?,?,?)""",(pid,raw_dest,'Ожидает проверки',_utc()))
                    self.db.execute("""UPDATE publication_ready_ingest SET state='done',completed_at=?
                        WHERE intent_id=? AND state='pending'""",(_utc(),ident))
                done.append(ident)
            except (OSError,ValueError,sqlite3.Error,RuntimeError) as exc:
                failed.append((ident,str(exc)))
        return {'done':tuple(done),'failed':tuple(failed)}

    def jobs(self,pid):
        self._project(pid)
        return tuple(self.db.execute('''SELECT id,media_path,channel,schedule_mode,publish_at,status
            FROM publication_jobs AS j WHERE j.project_id=?
            AND NOT EXISTS (SELECT 1 FROM publication_media_rejections AS r
                WHERE r.project_id=j.project_id AND r.source_path=j.media_path AND r.state='pending')
            ORDER BY j.created_at,j.id''',(pid,)).fetchall())

    def media_status(self,pid,path):
        self._project(pid)
        row=self.db.execute('''SELECT review_status FROM publication_media_review
            WHERE project_id=? AND media_path=?''',(pid,str(path))).fetchone()
        return row[0] if row else 'Ожидает проверки'

    def review_media(self,pid,paths,status,*,timestamp=None):
        self._exclusive()
        if status not in _ALLOWED_REVIEW:
            raise ValueError('Unsupported media review state')
        with own(self.db):
            self._project(pid)
            owned=tuple(self._file(pid,path) for path in paths)
            if len(set(owned))!=len(owned):
                raise ValueError('Media path selected more than once')
            for path in owned:
                if self.db.execute("SELECT 1 FROM publication_media_rejections WHERE project_id=? AND source_path=? AND state='pending'",(pid,path)).fetchone():
                    raise ValueError('Media rejection is pending; review is blocked')
                self.db.execute('''INSERT OR REPLACE INTO publication_media_review
                    (project_id,media_path,review_status,updated_at) VALUES(?,?,?,?)''',
                    (pid,path,status,timestamp or _utc()))
                if status!='Одобрен':
                    self.db.execute('''UPDATE publication_jobs SET status='Черновик'
                        WHERE project_id=? AND media_path=?''',(pid,path))
            return len(owned)

    def enqueue(self,pid,paths,channel,*,timestamp=None,dedupe=False,video_only=False):
        self._exclusive()
        if not isinstance(channel,str) or not channel.strip():
            raise ValueError('Missing publication channel')
        with own(self.db):
            self._project(pid)
            owned=tuple(self._file(pid,path) for path in paths)
            if len(set(owned))!=len(owned):
                raise ValueError('Duplicate media selection')
            for path in owned:
                if self.db.execute("SELECT 1 FROM publication_media_rejections WHERE project_id=? AND source_path=? AND state='pending'",(pid,path)).fetchone():
                    raise ValueError('Rejected media cannot be queued for publication')
                approval=self.db.execute('''SELECT review_status FROM publication_media_review
                    WHERE project_id=? AND media_path=?''',(pid,path)).fetchone()
                if approval!=('Одобрен',):
                    raise ValueError('All media must be approved before scheduling')
            if video_only and any(Path(path).suffix.lower() not in ('.mp4','.mov') for path in owned):
                raise ValueError('This publication channel accepts video only')
            ids=[]
            for path in owned:
                if dedupe:
                    existing=self.db.execute('''SELECT id FROM publication_jobs WHERE project_id=? AND media_path=? AND channel=?''',
                        (pid,path,channel)).fetchone()
                    if existing:
                        ids.append(existing[0])
                        continue
                ident=uuid4().hex
                self.db.execute('''INSERT INTO publication_jobs
                    (id,project_id,media_path,channel,schedule_mode,created_at)
                    VALUES(?,?,?,?,?,?)''',
                    (ident,pid,path,channel,'По графику блока',timestamp or _utc()))
                ids.append(ident)
            return tuple(ids)

    def schedule(self,pid,job_id,mode,at=''):
        self._exclusive()
        if mode not in ('Индивидуально','По графику блока'):
            raise ValueError('Invalid publication scheduling mode')
        if mode=='Индивидуально':
            if not isinstance(at,str):raise ValueError('Invalid date')
            datetime.strptime(at,'%Y-%m-%d %H:%M')
        elif at:
            raise ValueError('Block scheduling does not accept an individual date')
        with own(self.db):
            self._project(pid)
            result=self.db.execute('''UPDATE publication_jobs SET schedule_mode=?,publish_at=?
                WHERE id=? AND project_id=?''',(mode,at,job_id,pid))
            return result.rowcount==1

    def delete(self,pid,job_id):
        self._exclusive()
        with own(self.db):
            self._project(pid)
            return self.db.execute('''DELETE FROM publication_jobs WHERE id=? AND project_id=?''',
                                   (job_id,pid)).rowcount==1

    def block_rule(self,pid,channel):
        self._project(pid)
        return self.db.execute('''SELECT frequency,publish_time FROM publication_block_rules
            WHERE project_id=? AND channel=?''',(pid,channel)).fetchone()

    def save_block_rule(self,pid,channel,frequency,publish_time):
        self._exclusive()
        if not isinstance(channel,str) or not channel.strip() or not isinstance(frequency,str) or not frequency.strip():
            raise ValueError('Missing publication rule')
        datetime.strptime(publish_time,'%H:%M')
        with own(self.db):
            self._project(pid)
            self.db.execute('''INSERT OR REPLACE INTO publication_block_rules
                (project_id,channel,frequency,publish_time) VALUES(?,?,?,?)''',
                (pid,channel,frequency,publish_time))
            return True

    def reject_media(self,pid,paths,*,timestamp=None):
        """Commit intents first, then replay moves. Never delete source on rollback.

        Files remain recoverable after a crash at any point between rename and
        final SQLite acknowledgement. A failure leaves an inspectable intent.
        """
        self._exclusive()
        with own(self.db):
            self._project(pid)
            root=Path(self.workspace(pid))
            rejected=require_managed_folder(root,'rejected')
            owned=tuple(self._file(pid,path) for path in paths)
            if len(set(owned))!=len(owned):
                raise ValueError('Duplicate media selection')
            intents=[]
            install(self.db)
            for source in owned:
                existing=self.db.execute("""SELECT intent_id FROM publication_media_rejections
                    WHERE project_id=? AND source_path=? AND state='pending'""",(pid,source)).fetchone()
                if existing:
                    intents.append(existing[0])
                    continue
                digest,size=file_digest(source)
                ident=uuid4().hex
                dest=rejected/(ident+'_'+Path(source).name)
                reject_path_link(dest)
                if dest.exists():
                    raise FileExistsError('Rejection target is already occupied')
                self.db.execute("""INSERT INTO publication_media_rejections
                    (intent_id,project_id,source_path,destination_path,sha256,size,state,created_at)
                    VALUES(?,?,?,?,?,?,'pending',?)""",
                    (ident,pid,source,str(dest),digest,size,timestamp or _utc()))
                intents.append(ident)
        return self.recover_rejections(project_id=pid,intent_ids=tuple(intents))

    def recover_rejections(self,*,project_id=None,intent_ids=None,limit=100):
        """Replay one bounded batch safely across restarts and independent projects."""
        self._exclusive()
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError('Invalid replay bound')
        install(self.db)
        query="""SELECT intent_id,project_id,source_path,destination_path,sha256,size
            FROM publication_media_rejections WHERE state='pending'"""
        args=[]
        if project_id is not None:
            self._project(project_id)
            query+=' AND project_id=?';args.append(project_id)
        if intent_ids is not None:
            ids=tuple(intent_ids)
            if not ids:
                return {'done':(), 'failed':()}
            query+=' AND intent_id IN ('+','.join('?' for _ in ids)+')'
            args.extend(ids)
        query+=' ORDER BY created_at,intent_id LIMIT ?'
        args.append(limit)
        done=[];failed=[]
        for ident,pid,raw_src,raw_dest,digest,size in self.db.execute(query,args).fetchall():
            try:
                root=Path(self.workspace(pid))
                source=require_managed_file(root,raw_src,_ALLOWED_MEDIA)
                dest=require_managed_file(root,raw_dest,('rejected',))
                folder=require_managed_folder(root,'rejected')
                folder.mkdir(parents=True,exist_ok=True)
                reject_path_link(folder)
                src_exists=source.is_file()
                dst_exists=dest.is_file()
                if src_exists and dst_exists:
                    raise FileExistsError('Both source and destination exist: ambiguous media move')
                if not src_exists and not dst_exists:
                    raise FileNotFoundError('Pending media and destination both missing')
                if src_exists:
                    if file_digest(source)!=(digest,size):
                        raise ValueError('Source changed since deletion was authorized')
                    if dest.exists() or dest.is_symlink():
                        raise FileExistsError('Rejection destination became occupied')
                    os.replace(source,dest)
                if file_digest(dest)!=(digest,size):
                    raise ValueError('Moved media has different bytes')
                with own(self.db):
                    row=self.db.execute("""SELECT project_id,source_path,destination_path,state
                        FROM publication_media_rejections WHERE intent_id=?""",(ident,)).fetchone()
                    if row!=(pid,raw_src,raw_dest,'pending'):
                        raise ValueError('Rejection intent changed while replaying')
                    self.db.execute('DELETE FROM publication_jobs WHERE project_id=? AND media_path=?',(pid,raw_src))
                    self.db.execute('DELETE FROM publication_media_review WHERE project_id=? AND media_path=?',(pid,raw_src))
                    self.db.execute("""UPDATE publication_media_rejections SET state='done',completed_at=?
                        WHERE intent_id=? AND state='pending'""",(_utc(),ident))
                done.append(ident)
            except (OSError,sqlite3.Error,ValueError,RuntimeError) as exc:
                failed.append((ident,str(exc)))
        return {'done':tuple(done),'failed':tuple(failed)}

    def job_paths(self,pid,job_ids):
        self._project(pid)
        found=[]
        for jid in job_ids:
            if not isinstance(jid,str):continue
            row=self.db.execute('''SELECT media_path FROM publication_jobs
                WHERE project_id=? AND id=?''',(pid,jid)).fetchone()
            if not row:continue
            try:found.append(self._file(pid,row[0]))
            except (OSError,ValueError):continue
        return tuple(found)
