"""Qt-free durable batch scheduler and publication receipts.

A worker is not successful merely because it exited. Each item needs an
immutable claim token and a verified result in the same database transaction
as result publication. All recovery is scoped to the original project.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import json
import sqlite3
from hashlib import sha256
from sqlite_transaction_core import own
from project_file_boundary import require_managed_file


def _now():
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(path):
    digest=sha256();size=0
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""):
            size+=len(chunk);digest.update(chunk)
    return digest.hexdigest(),size


def install(db):
    db.execute('''CREATE TABLE IF NOT EXISTS batch_item_receipts (
        batch_id TEXT NOT NULL, position INTEGER NOT NULL,
        project_id TEXT NOT NULL, source_path TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending','running','failed','completed')),
        attempt_token TEXT, result_path TEXT, result_sha256 TEXT, result_size INTEGER, source_sha256 TEXT,
        source_size INTEGER, updated_at TEXT NOT NULL,
        PRIMARY KEY(batch_id, position), UNIQUE(batch_id,source_path),
        FOREIGN KEY(batch_id) REFERENCES batch_runs(id) ON DELETE CASCADE)''')
    existing={row[1] for row in db.execute('PRAGMA table_info(batch_item_receipts)')}
    if 'source_sha256' not in existing:db.execute('ALTER TABLE batch_item_receipts ADD COLUMN source_sha256 TEXT')
    if 'source_size' not in existing:db.execute('ALTER TABLE batch_item_receipts ADD COLUMN source_size INTEGER')
    if 'result_sha256' not in existing:db.execute('ALTER TABLE batch_item_receipts ADD COLUMN result_sha256 TEXT')
    if 'result_size' not in existing:db.execute('ALTER TABLE batch_item_receipts ADD COLUMN result_size INTEGER')
    db.execute('''CREATE INDEX IF NOT EXISTS batch_receipts_by_project
        ON batch_item_receipts(project_id,batch_id,status,position)''')


def recover_inflight_batch_receipts(db, *, project_id=None, timestamp=None):
    """Fence orphaned batch claims in an existing cross-service transaction.

    Shared by normal application restart and pre-constraint SQLite migration:
    both worker kinds must commit or roll back together. No filesystem access.
    """
    if not isinstance(db, sqlite3.Connection) or not db.in_transaction:
        raise RuntimeError('Batch restart recovery requires an application transaction')
    if project_id is not None and (not isinstance(project_id,str) or not project_id.strip()
            or not db.execute('SELECT 1 FROM projects WHERE id=?',(project_id,)).fetchone()):
        raise ValueError('Unknown batch project')
    stamp=timestamp or _now()
    if project_id is None:
        return db.execute("""UPDATE batch_item_receipts
            SET status='failed',attempt_token=NULL,updated_at=?
            WHERE status='running' AND EXISTS
                (SELECT 1 FROM batch_runs r WHERE r.id=batch_item_receipts.batch_id
                 AND r.project_id=batch_item_receipts.project_id AND r.status='running')""",
            (stamp,)).rowcount
    return db.execute("""UPDATE batch_item_receipts
            SET status='failed',attempt_token=NULL,updated_at=?
            WHERE project_id=? AND status='running' AND EXISTS
                (SELECT 1 FROM batch_runs r WHERE r.id=batch_item_receipts.batch_id
                 AND r.project_id=batch_item_receipts.project_id AND r.status='running')""",
            (stamp,project_id)).rowcount


@dataclass(frozen=True)
class BatchReceipt:
    batch_id: str
    project_id: str
    prompt: str
    engine_mode: int
    paths: tuple[str,...]
    total: int
    status: str
    missing: tuple[str,...]=()
    blocked: str = ''


@dataclass(frozen=True)
class BatchClaim:
    batch_id: str
    project_id: str
    source_path: str
    token: str


class BatchRuntime:
    def __init__(self,db:sqlite3.Connection,workspace):
        if not isinstance(db,sqlite3.Connection) or not callable(workspace):
            raise TypeError('Durable batches require application SQLite and workspace')
        self.db,self.workspace=db,workspace

    def _check(self,pid):
        if not isinstance(pid,str) or not pid.strip() or not self.db.execute(
                'SELECT 1 FROM projects WHERE id=?',(pid,)).fetchone():
            raise ValueError('Unknown batch project')

    def _free(self):
        if self.db.in_transaction:
            raise RuntimeError('Another operation owns the batch transaction')

    def _validate_sources(self,pid,paths):
        root=Path(self.workspace(pid));valid=[]
        for raw in paths:
            source=require_managed_file(root,raw,('originals',))
            if not source.is_file():
                raise FileNotFoundError('Batch input missing: '+str(source))
            if source.suffix.lower() not in ('.jpg','.jpeg','.png','.webp','.bmp','.tif','.tiff'):
                raise ValueError('Not a supported image source')
            valid.append(str(source))
        if not valid or len(set(valid))!=len(valid):
            raise ValueError('Batch sources missing or repeated')
        return tuple(valid)

    def create(self,pid,paths,prompt,mode,*,batch_id=None,timestamp=None):
        self._free()
        if not isinstance(prompt,str) or not prompt.strip():
            raise ValueError('Empty batch instruction')
        if type(mode) is not int or mode not in (0,1):
            raise ValueError('Unsupported batch engine mode')
        with own(self.db):
            self._check(pid)
            valid=self._validate_sources(pid,paths)
            ident=batch_id or uuid4().hex
            if not isinstance(ident,str) or not ident.strip():
                raise ValueError('Invalid batch identity')
            created=timestamp or _now()
            self.db.execute('''INSERT INTO batch_runs
                (id,project_id,prompt,engine_mode,queue_json,total,status,updated_at)
                VALUES(?,?,?,?,?,?,?,?)''',
                (ident,pid,prompt,mode,json.dumps(valid,ensure_ascii=False),len(valid),'running',created))
            self.db.executemany('''INSERT INTO batch_item_receipts
                (batch_id,position,project_id,source_path,status,source_sha256,source_size,updated_at)
                VALUES(?,?,?,?,'pending',?,?,?)''',
                [(ident,i,pid,path,*_fingerprint(path),created) for i,path in enumerate(valid)])
        return BatchReceipt(ident,pid,prompt,mode,valid,len(valid),'running')

    def _row(self,pid,batch_id):
        self._check(pid)
        row=self.db.execute('''SELECT status,total FROM batch_runs
            WHERE project_id=? AND id=?''',(pid,batch_id)).fetchone()
        if row is None:raise ValueError('Batch belongs to another project or does not exist')
        return row

    def _legacy_guard(self,pid,batch_id,total):
        n=self.db.execute('SELECT COUNT(*) FROM batch_item_receipts WHERE batch_id=? AND project_id=?',
            (batch_id,pid)).fetchone()[0]
        if n!=total:
            raise ValueError('Legacy batch has no verified item ledger; manual reconciliation required')

    def _refresh_queue(self,pid,batch_id,*,status=None,timestamp=None):
        paths=[r[0] for r in self.db.execute('''SELECT source_path FROM batch_item_receipts
            WHERE project_id=? AND batch_id=? AND status!='completed' ORDER BY position''',(pid,batch_id))]
        self.db.execute('''UPDATE batch_runs SET queue_json=?,
            status=COALESCE(?,status),updated_at=? WHERE id=? AND project_id=?''',
            (json.dumps(paths,ensure_ascii=False),status,timestamp or _now(),batch_id,pid))
        return paths

    def advance(self,pid,batch_id,*,timestamp=None):
        '''Atomically select/claim the next durable item, or verify and finish.

        Qt must not infer queue progress from a mutable local list. A claim is
        returned only for the first uncompleted item; None means every receipt
        and on-disk output was verified and the batch was durably completed.
        '''
        self._free()
        with own(self.db):
            state,total=self._row(pid,batch_id)
            if state!='running':raise ValueError('Batch is not running')
            self._legacy_guard(pid,batch_id,total)
            row=self.db.execute('''SELECT source_path,status,source_sha256,source_size
                FROM batch_item_receipts WHERE project_id=? AND batch_id=?
                AND status!='completed' ORDER BY position LIMIT 1''',(pid,batch_id)).fetchone()
            if row is None:
                self._verify_outputs(pid,batch_id)
                self._refresh_queue(pid,batch_id,status='completed',timestamp=timestamp)
                return None
            path,status,digest,size=row
            if status=='running':raise ValueError('Batch item already claimed by a worker')
            self._validate_sources(pid,(path,))
            if digest is None or size is None or _fingerprint(path)!=(digest,size):
                raise ValueError('Batch original changed or has no verified fingerprint')
            token=uuid4().hex
            changed=self.db.execute('''UPDATE batch_item_receipts SET status='running',
                attempt_token=?,result_path=NULL,result_sha256=NULL,result_size=NULL,updated_at=?
                WHERE project_id=? AND batch_id=? AND source_path=?
                AND status IN ('pending','failed')''',
                (token,timestamp or _now(),pid,batch_id,path)).rowcount
            if changed!=1:raise RuntimeError('Batch item claim was not acquired')
            return BatchClaim(batch_id,pid,path,token)

    def _verify_outputs(self,pid,batch_id):
        for result_path,digest,size in self.db.execute('''SELECT result_path,result_sha256,result_size
            FROM batch_item_receipts WHERE project_id=? AND batch_id=?
            ORDER BY position''',(pid,batch_id)):
            if not result_path or not digest or size is None:
                raise ValueError('Batch has a legacy/unverified output receipt')
            output=require_managed_file(Path(self.workspace(pid)),result_path,('processed',))
            if not output.is_file() or _fingerprint(output)!=(digest,size):
                raise ValueError('Batch output missing or modified since publication')

    def claim(self,pid,batch_id,path,*,timestamp=None):
        """Claim one remaining item; retry requires a new token."""
        self._free()
        with own(self.db):
            state,total=self._row(pid,batch_id)
            if state!='running':raise ValueError('Batch is not running')
            self._legacy_guard(pid,batch_id,total)
            first=self.db.execute('''SELECT source_path,status FROM batch_item_receipts
                WHERE project_id=? AND batch_id=? AND status!='completed'
                ORDER BY position LIMIT 1''',(pid,batch_id)).fetchone()
            if first is None or first[0]!=path:
                raise ValueError('Cannot claim an unowned or out-of-order source')
            if first[1]=='running':raise ValueError('Batch item already claimed by a worker')
            self._validate_sources(pid,[path])
            expected=self.db.execute('''SELECT source_sha256,source_size FROM batch_item_receipts
                WHERE project_id=? AND batch_id=? AND source_path=?''',(pid,batch_id,path)).fetchone()
            if not expected or expected[0] is None or expected[1] is None or _fingerprint(path)!=tuple(expected):
                raise ValueError('Batch original changed or has no verified fingerprint')
            token=uuid4().hex
            changed=self.db.execute('''UPDATE batch_item_receipts SET status='running',
                attempt_token=?,result_path=NULL,result_sha256=NULL,result_size=NULL,updated_at=?
                WHERE project_id=? AND batch_id=? AND source_path=?
                AND status IN ('pending','failed')''',
                (token,timestamp or _now(),pid,batch_id,path)).rowcount
            if changed!=1:raise RuntimeError('Batch item claim was not acquired')
        return BatchClaim(batch_id,pid,path,token)

    def authorize_in_transaction(self,claim):
        if not self.db.in_transaction:
            raise RuntimeError('Batch authority must be checked inside publication transaction')
        if not isinstance(claim,BatchClaim):raise ValueError('Invalid batch claim')
        state,total=self._row(claim.project_id,claim.batch_id)
        self._legacy_guard(claim.project_id,claim.batch_id,total)
        if state!='running':raise ValueError('Batch is no longer running')
        item=self.db.execute('''SELECT status,attempt_token FROM batch_item_receipts
            WHERE project_id=? AND batch_id=? AND source_path=?''',
            (claim.project_id,claim.batch_id,claim.source_path)).fetchone()
        if item!=('running',claim.token):
            raise ValueError('Stale or invalid batch worker authority')
        original=self.db.execute('''SELECT source_sha256,source_size FROM batch_item_receipts
            WHERE project_id=? AND batch_id=? AND source_path=?''',
            (claim.project_id,claim.batch_id,claim.source_path)).fetchone()
        checked=require_managed_file(Path(self.workspace(claim.project_id)),claim.source_path,('originals',))
        if not checked.is_file() or original is None or _fingerprint(checked)!=tuple(original):
            raise ValueError('Batch original changed before result publication')

    def accept_in_transaction(self,claim,result_path,*,timestamp=None,expected_sha256=None):
        self.authorize_in_transaction(claim)
        output=require_managed_file(Path(self.workspace(claim.project_id)),result_path,('processed',))
        if not output.is_file():raise FileNotFoundError('Batch result was not saved')
        result_sha,result_size=_fingerprint(output)
        if result_size<=0:raise ValueError('Empty batch result cannot be accepted')
        if expected_sha256 is not None and result_sha!=expected_sha256:
            raise ValueError('Media changed during publication')
        changed=self.db.execute('''UPDATE batch_item_receipts SET status='completed',
            result_path=?,result_sha256=?,result_size=?,updated_at=? WHERE batch_id=? AND project_id=?
            AND source_path=? AND status='running' AND attempt_token=?''',
            (str(output),result_sha,result_size,timestamp or _now(),claim.batch_id,claim.project_id,
             claim.source_path,claim.token)).rowcount
        if changed!=1:raise RuntimeError('Batch receipt was not acknowledged')
        self._refresh_queue(claim.project_id,claim.batch_id,timestamp=timestamp)

    def interrupt(self,claim,*,timestamp=None):
        """Worker failed or exited without a verified result; keep item recoverable."""
        self._free()
        with own(self.db):
            state,total=self._row(claim.project_id,claim.batch_id)
            if state!='running':return False
            changed=self.db.execute('''UPDATE batch_item_receipts SET status='failed',
                attempt_token=NULL,updated_at=? WHERE batch_id=? AND project_id=?
                AND source_path=? AND status='running' AND attempt_token=?''',
                (timestamp or _now(),claim.batch_id,claim.project_id,
                 claim.source_path,claim.token)).rowcount
            if changed:self._refresh_queue(claim.project_id,claim.batch_id,timestamp=timestamp)
            return bool(changed)

    def recover_in_transaction(self, *, project_id=None, timestamp=None):
        """Fence abandoned batch workers within the application's transaction.

        This delegates to the same SQL used by legacy-index bootstrap, so
        execution and batch fencing share a single durable boundary.
        """
        return recover_inflight_batch_receipts(self.db, project_id=project_id, timestamp=timestamp)

    def recover(self,pid,batch_id,*,timestamp=None):
        """After process restart old worker claims cannot retain authority."""
        self._free()
        with own(self.db):
            state,total=self._row(pid,batch_id)
            if state!='running':return 0
            changed=self.db.execute('''UPDATE batch_item_receipts SET status='failed',
                attempt_token=NULL,updated_at=? WHERE project_id=? AND batch_id=?
                AND status='running' ''',(timestamp or _now(),pid,batch_id)).rowcount
            if changed:self._refresh_queue(pid,batch_id,timestamp=timestamp)
            return changed

    def finish(self,pid,batch_id,*,timestamp=None):
        self._free()
        with own(self.db):
            state,total=self._row(pid,batch_id)
            if state!='running':raise ValueError('Batch is not running')
            self._legacy_guard(pid,batch_id,total)
            remaining=self.db.execute('''SELECT COUNT(*) FROM batch_item_receipts
                WHERE project_id=? AND batch_id=? AND status!='completed' ''',(pid,batch_id)).fetchone()[0]
            if remaining:raise ValueError('Cannot finish batch without all verified result receipts')
            self._verify_outputs(pid,batch_id)
            self._refresh_queue(pid,batch_id,status='completed',timestamp=timestamp)

    def cancel(self,pid,batch_id,*,timestamp=None):
        self._free()
        with own(self.db):
            state,total=self._row(pid,batch_id)
            if state!='running':return False
            self.db.execute('''UPDATE batch_runs SET status='cancelled',updated_at=?
                WHERE project_id=? AND id=?''',(timestamp or _now(),pid,batch_id))
            self.db.execute('''UPDATE batch_item_receipts SET attempt_token=NULL,
                status='failed',updated_at=? WHERE project_id=? AND batch_id=?
                AND status='running' ''',(timestamp or _now(),pid,batch_id))
            return True

    def checkpoint(self,pid,batch_id,pending,status='running',*,timestamp=None):
        """Compatibility adapter: never accept caller-supplied completion claims."""
        self._free()
        if status=='completed':
            if pending:raise ValueError('Cannot finish with pending inputs')
            self.finish(pid,batch_id,timestamp=timestamp)
            return True
        if status=='cancelled':return self.cancel(pid,batch_id,timestamp=timestamp)
        if status!='running':raise ValueError('Unsupported batch status')
        actual=self.pending(pid)
        if not actual or actual.batch_id!=batch_id or tuple(pending)!=actual.paths:
            raise ValueError('GUI cannot rewrite durable batch queue')
        return True

    def pending_all(self,pid):
        # Every unfinished batch remains discoverable, independently of newer tasks.
        self._check(pid)
        rows=self.db.execute('''SELECT id,prompt,engine_mode,queue_json,total,status
            FROM batch_runs WHERE project_id=? AND status='running'
            ORDER BY updated_at ASC,id ASC''',(pid,)).fetchall()
        records=[]
        for ident,prompt,mode,queue_json,total,status in rows:
            blocked=''
            try:self._legacy_guard(pid,ident,total)
            except ValueError:
                blocked='Старая очередь без подтверждённого журнала элементов: требуется сверка результатов.'
            if blocked:
                try:paths=json.loads(queue_json)
                except (TypeError,ValueError):paths=[]
                if not isinstance(paths,list) or not all(isinstance(p,str) for p in paths):
                    paths=[]
            else:
                paths=[r[0] for r in self.db.execute('''SELECT source_path FROM batch_item_receipts
                    WHERE project_id=? AND batch_id=? AND status!='completed' ORDER BY position''',(pid,ident))]
            missing=[]
            for path in paths:
                try:
                    f=require_managed_file(Path(self.workspace(pid)),path,('originals',))
                    if not f.is_file():missing.append(path)
                except (ValueError,OSError):missing.append(path)
            records.append(BatchReceipt(ident,pid,prompt,mode,tuple(paths),total,status,tuple(missing),blocked))
        return tuple(records)

    def pending(self,pid):
        records=self.pending_all(pid)
        return records[0] if records else None
