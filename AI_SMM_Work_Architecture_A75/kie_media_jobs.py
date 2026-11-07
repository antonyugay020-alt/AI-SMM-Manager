"""Durable, project-scoped Kie media task orchestration.

The paid submit boundary is intentionally not retried automatically after an
uncertain network result. An HTTP timeout might already have created a task.
File upload and Get Task Details are supplied as adapters until their exact
provider OpenAPI schemas are verified. Never invent endpoints.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from kie_media_contracts import extract_task_id, TASK_ID

SUPPORTED = frozenset((
    "nano-banana-pro", "google/nano-banana-edit",
    "kling-3.0-omni/transformation",
    "kling-3.0-omni/reference-to-video"))
TERMINAL = frozenset(("completed", "failed", "submission_unknown"))


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def install(db: sqlite3.Connection):
    db.execute("""CREATE TABLE IF NOT EXISTS kie_media_tasks (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
        model TEXT NOT NULL, payload_json TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL, state TEXT NOT NULL,
        provider_task_id TEXT UNIQUE, output_path TEXT,
        result_sha256 TEXT, cost_reservation_id TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        error_code TEXT, CHECK (state IN (
        'prepared','submitting','submitted','completed','failed',
        'submission_unknown')))""")
    columns={row[1] for row in db.execute("PRAGMA table_info(kie_media_tasks)")}
    if "result_sha256" not in columns:
        db.execute("ALTER TABLE kie_media_tasks ADD COLUMN result_sha256 TEXT")
    if "cost_reservation_id" not in columns:
        db.execute("ALTER TABLE kie_media_tasks ADD COLUMN cost_reservation_id TEXT")
    db.execute("""CREATE INDEX IF NOT EXISTS kie_media_task_owner
        ON kie_media_tasks(project_id,created_at)""")


def _project_exists(db, project_id):
    if not isinstance(project_id, str) or not project_id:
        raise ValueError("invalid project owner")
    if db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
        raise ValueError("unknown project owner")


def prepare(db, project_id, payload):
    """Record immutable paid intent. Caller provides approved uploaded URLs."""
    if db.in_transaction:
        raise RuntimeError("foreign SQLite transaction in progress")
    _project_exists(db, project_id)
    if not isinstance(payload, dict) or payload.get("model") not in SUPPORTED:
        raise ValueError("unsupported Kie media model")
    if not isinstance(payload.get("input"), dict):
        raise ValueError("missing validated media input")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    if len(canonical.encode("utf-8")) > 160_000:
        raise ValueError("Kie media request exceeds size limit")
    ident = uuid4().hex
    at = utcnow()
    try:
        db.execute("BEGIN IMMEDIATE")
        install(db)
        db.execute("""INSERT INTO kie_media_tasks
            (id, project_id, model, payload_json, payload_sha256, state,
             created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'prepared', ?, ?)""",
            (ident,project_id,payload["model"],canonical,
             hashlib.sha256(canonical.encode("utf-8")).hexdigest(),at,at))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return ident


def _owned_row(db, project_id, job_id):
    _project_exists(db, project_id)
    row=db.execute("""SELECT model,payload_json,payload_sha256,state,
        provider_task_id,output_path,result_sha256 FROM kie_media_tasks
        WHERE id=? AND project_id=?""",(job_id,project_id)).fetchone()
    if row is None:
        raise ValueError("job does not belong to requested project")
    if hashlib.sha256(row[1].encode("utf-8")).hexdigest()!=row[2]:
        raise ValueError("persisted Kie task payload was modified")
    return row


def submit(db, project_id, job_id, create_task, *, enable_paid=False,
           payment_reservation=None, cost_gate=None):
    """Attempt one paid provider call; a crash/timeout cannot auto-resubmit."""
    if enable_paid is not True:
        raise RuntimeError("paid Kie media generation must be explicitly enabled")
    from durable_cost_gate import DurableCostGate
    if not isinstance(cost_gate, DurableCostGate):
        raise RuntimeError("durable payment gate required at paid transport boundary")
    if db.in_transaction:
        raise RuntimeError("foreign SQLite transaction in progress")
    row=_owned_row(db,project_id,job_id)
    if row[3]!="prepared":
        raise RuntimeError("Kie task already submitted or outcome uncertain")
    cost_gate.assert_reservation(payment_reservation,"kie.ai","media:"+job_id)
    try:
        db.execute("BEGIN IMMEDIATE")
        updated=db.execute("""UPDATE kie_media_tasks
            SET state='submitting',cost_reservation_id=?,updated_at=?
            WHERE id=? AND project_id=? AND state='prepared'""",
            (payment_reservation,utcnow(),job_id,project_id)).rowcount
        if updated!=1:raise RuntimeError("task submission fenced by another worker")
        db.commit()
    except BaseException:
        db.rollback()
        raise
    try:
        # Revalidate immediately before the provider transport; a grant from
        # another task, a settled grant or a frozen ledger cannot be reused.
        cost_gate.assert_reservation(payment_reservation,"kie.ai","media:"+job_id)
        result=create_task(
            json.loads(row[1]), payment_reservation=payment_reservation,
            operation="media:"+job_id, cost_gate=cost_gate)
        provider_id=extract_task_id(result)
    except Exception:
        db.execute("""UPDATE kie_media_tasks SET state='submission_unknown',
            error_code='provider_response_unconfirmed',updated_at=?
            WHERE id=? AND project_id=? AND state='submitting'""",
            (utcnow(),job_id,project_id))
        db.commit()
        raise
    try:
        updated=db.execute("""UPDATE kie_media_tasks
            SET state='submitted',provider_task_id=?,error_code=NULL,updated_at=?
            WHERE id=? AND project_id=? AND (state='submitting' OR
            (state='submission_unknown' AND error_code='interrupted_paid_submission'))""",
            (provider_id,utcnow(),job_id,project_id)).rowcount
        if updated!=1:
            raise RuntimeError("confirmed provider task could not be recorded")
        db.commit()
    except BaseException:
        db.rollback()
        # A confirmed response can still collide with an existing provider ID.
        # Preserve the allocation and prevent any second paid dispatch.
        db.execute("""UPDATE kie_media_tasks SET state='submission_unknown',
            error_code='provider_receipt_unconfirmed',updated_at=?
            WHERE id=? AND project_id=? AND state='submitting'""",
            (utcnow(),job_id,project_id))
        db.commit()
        raise
    return provider_id



def _digest_file(path):
    result=hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024*1024), b""):
            result.update(block)
    return result.hexdigest()


def _validate_media_bytes(path, *, is_video):
    """Verify bytes before they cross the project publication boundary."""
    if path.stat().st_size > (500 if is_video else 60)*1024*1024:
        raise ValueError("downloaded Kie media exceeds maximum size")
    if not is_video:
        from PIL import Image, UnidentifiedImageError
        expected="PNG" if path.suffix.lower()==".png" else "JPEG"
        try:
            with Image.open(path) as image:
                if image.format!=expected:
                    raise ValueError("downloaded photo MIME does not match output")
                width,height=image.size
                if (width<=0 or height<=0 or width>16000 or height>16000
                        or width*height>40_000_000):
                    raise ValueError("downloaded photo pixel budget exceeded")
                # Verify actual decoded pixels, not only the image header.
                image.load()
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise ValueError("downloaded photo cannot be fully decoded") from exc
    else:
        import subprocess
        try:
            probe=subprocess.run(
                ["ffprobe","-v","error","-show_entries",
                 "stream=codec_type:format=duration","-of","json",str(path)],
                timeout=15, capture_output=True, check=True)
            metadata=json.loads(probe.stdout)
        except (OSError,subprocess.SubprocessError,ValueError) as exc:
            raise ValueError("downloaded video failed ffprobe verification") from exc
        if not any(item.get("codec_type")=="video"
                   for item in metadata.get("streams",[]) if isinstance(item,dict)):
            raise ValueError("downloaded media has no video stream")
        duration=float(metadata.get("format",{}).get("duration",0))
        if not math.isfinite(duration) or duration<=0:
            raise ValueError("downloaded video duration invalid")


def complete(db, project_id, job_id, query_task, fetch_verified_file,
             workspace):
    """Query task via injected documented adapter; publish verified owned bytes.

    fetch_verified_file(task_result, destination) MUST place validated bytes in
    destination (the caller must verify content type/size before returning).
    """
    if db.in_transaction:
        raise RuntimeError("foreign SQLite transaction in progress")
    row=_owned_row(db,project_id,job_id)
    model, _, _, state, provider_id, output_path, result_digest=row
    if state=="completed":
        if not output_path:
            raise ValueError("completed Kie task has no artifact receipt")
        from project_file_boundary import require_managed_file
        dest=Path(output_path)
        require_managed_file(Path(workspace(project_id)),dest,("processed",))
        if not dest.is_file():
            raise ValueError("completed Kie artifact is missing")
        _validate_media_bytes(dest,is_video=model.startswith("kling-"))
        if not result_digest or _digest_file(dest)!=result_digest:
            raise ValueError("completed Kie media artifact fingerprint mismatch")
        return str(dest)
    if state!="submitted" or not provider_id or not TASK_ID.fullmatch(provider_id):
        raise RuntimeError("task has no confirmed remote ID")
    from media_ingest_core import ingest, replay
    # Crash-safe reconciliation: a prior run may have durably published the
    # result and crashed before marking the remote task completed.
    existing=db.execute("""SELECT kind,path FROM project_files WHERE id=?
        AND project_id=?""",(job_id,project_id)).fetchone()
    if existing is not None:
        expected_kind="video" if model.startswith("kling-") else "result"
        if existing[0]!=expected_kind:
            raise ValueError("existing media receipt has wrong content kind")
        replay(db,workspace,project_id=project_id)
        resolved=Path(existing[1])
        root=Path(workspace(project_id))
        from project_file_boundary import require_managed_file
        require_managed_file(root,resolved,("processed",))
        if not resolved.is_file():
            raise ValueError("existing media receipt has unsafe location")
        _validate_media_bytes(resolved,is_video=model.startswith("kling-"))
        # Do not adopt modified-but-valid media as a new trusted receipt.
        # The ingest outbox recorded the source digest before publication.
        original=db.execute("""SELECT source_sha256,source_size,kind,state
            FROM media_ingest_jobs WHERE id=? AND project_id=?""",
            (job_id,project_id)).fetchone()
        if (original is None or original[2]!=expected_kind or
                original[3]!="done" or resolved.stat().st_size!=original[1]):
            raise ValueError("published media receipt is not verified")
        digest=_digest_file(resolved)
        if digest!=original[0]:
            raise ValueError("published media content differs from ingest receipt")
        db.execute("""UPDATE kie_media_tasks SET state='completed',
            output_path=?,result_sha256=?,updated_at=? WHERE id=? AND project_id=?
            AND state='submitted'""",
            (str(resolved),digest,utcnow(),job_id,project_id))
        db.commit()
        return str(resolved)
    status=query_task(provider_id)
    if not isinstance(status,dict) or status.get("code")!=200:
        raise ValueError("Kie task status not confirmed")
    data=status.get("data")
    if not isinstance(data,dict) or data.get("taskId")!=provider_id:
        raise ValueError("provider status task identity mismatch")
    if data.get("model")!=model:
        raise ValueError("provider status model identity mismatch")
    status_name=data.get("state")
    if status_name=="fail":
        db.execute("""UPDATE kie_media_tasks SET state='failed',
            error_code='provider_generation_failed',updated_at=?
            WHERE id=? AND project_id=? AND state='submitted'""",
            (utcnow(),job_id,project_id))
        db.commit()
        raise RuntimeError("Kie task failed")
    if status_name!="success":
        return None
    result_json=data.get("resultJson")
    if not isinstance(result_json,str) or len(result_json)>160_000:
        raise ValueError("missing completed task result")
    result=json.loads(result_json)
    urls=result.get("resultUrls") if isinstance(result,dict) else None
    if not isinstance(urls,list) or len(urls)!=1 or not isinstance(urls[0],str):
        raise ValueError("Kie task returned no single verified result URL")
    from kie_media_contracts import _url
    _url(urls[0])
    from project_file_boundary import require_managed_folder
    root=Path(workspace(project_id))
    spool=require_managed_folder(root,".kie_downloads")
    spool.mkdir(parents=True,exist_ok=True)
    fmt=json.loads(row[1])["input"].get("output_format","png")
    ext=".mp4" if model.startswith("kling-") else (
        ".jpg" if fmt in ("jpg","jpeg") else ".png")
    # Each attempt owns its staging file. A killed downloader or another
    # polling worker cannot block recovery or have its file unlinked by us.
    staged=spool/(job_id+"-"+uuid4().hex+ext)
    try:
        fetch_verified_file(urls[0],staged)
        if not staged.is_file() or staged.is_symlink() or staged.stat().st_size==0:
            raise ValueError("downloaded Kie result missing or invalid")
        _validate_media_bytes(staged, is_video=model.startswith("kling-"))
        kind="video" if model.startswith("kling-") else "result"
        try:
            receipt=ingest(db,workspace,staged,project_id=project_id,
                           kind=kind,original_name="kie-"+job_id+ext,
                           item_id=job_id)
        except (sqlite3.IntegrityError, FileExistsError):
            # Another polling process may have committed this exact identity
            # after our initial lookup. Reconcile through the authenticated
            # ingest receipt; never accept an arbitrary conflicting file.
            if not db.execute("SELECT 1 FROM project_files WHERE id=? AND project_id=?",
                              (job_id,project_id)).fetchone():
                raise
            return complete(db,project_id,job_id,query_task,fetch_verified_file,workspace)
        if not receipt.published:
            raise RuntimeError("media result committed but publication pending")
        db.execute("""UPDATE kie_media_tasks SET state='completed',
            output_path=?,result_sha256=?,updated_at=?
            WHERE id=? AND project_id=? AND state='submitted'""",
            (str(receipt.final_path),_digest_file(receipt.final_path),
             utcnow(),job_id,project_id))
        db.commit()
        return str(receipt.final_path)
    finally:
        staged.unlink(missing_ok=True)


def recover_pending(db, project_id):
    """Return confirmed tasks to poll; NEVER resubmit uncertain paid intents."""
    if db.in_transaction:
        raise RuntimeError("foreign SQLite transaction in progress")
    _project_exists(db,project_id)
    install(db)
    db.commit()
    db.execute("""UPDATE kie_media_tasks SET state='submission_unknown',
        error_code='interrupted_paid_submission',updated_at=?
        WHERE project_id=? AND state='submitting'""",(utcnow(),project_id))
    db.commit()
    return db.execute("""SELECT id,provider_task_id FROM kie_media_tasks
        WHERE project_id=? AND state='submitted' ORDER BY created_at,id""",
        (project_id,)).fetchall()


def list_project_tasks(db, project_id, *, limit=100):
    """Project-scoped status listing for Qt; never expose keys or signed URLs."""
    if db.in_transaction:
        raise RuntimeError("cannot list media jobs in a foreign transaction")
    if type(limit) is not int or not 1<=limit<=500:
        raise ValueError("invalid project task query limit")
    _project_exists(db,project_id)
    install(db)
    db.commit()
    rows=db.execute("""SELECT id,model,state,provider_task_id,output_path,
        error_code,created_at,updated_at
        FROM kie_media_tasks WHERE project_id=? ORDER BY created_at DESC,id DESC
        LIMIT ?""",(project_id,limit)).fetchall()
    return [{"id":row[0],"model":row[1],"state":row[2],
             "provider_task_id":row[3],"output_path":row[4],
             "error_code":row[5],"created_at":row[6],"updated_at":row[7]}
            for row in rows]



def preview_prepared_task(db, project_id, job_id):
    """Return a safe user-facing review of an unpaid draft, never upload URLs.

    The original canonical provider payload stays private in SQLite. This
    method exposes only validated human-readable options, without carrying
    reference URLs or API credentials into Qt status/confirmation dialogs.
    """
    if db.in_transaction:
        raise RuntimeError("cannot preview media task in a foreign transaction")
    row=_owned_row(db,project_id,job_id)
    if row[3]!="prepared":
        raise RuntimeError("only an unpaid media draft may be previewed")
    payload=json.loads(row[1])
    options=payload.get("input")
    if not isinstance(options,dict) or payload.get("model")!=row[0]:
        raise ValueError("invalid persisted media draft")
    prompt=options.get("prompt")
    if not isinstance(prompt,str) or not prompt.strip():
        raise ValueError("invalid persisted media prompt")
    allowed=("aspect_ratio","resolution","output_format","duration")
    return {"model":row[0],"prompt":prompt,
            "options":{key:options[key] for key in allowed
                       if key in options and type(options[key]) in (str,int)},
            "reference_count":sum(len(options.get(field,[]))
                                  for field in ("image_input","image_urls","video_urls")
                                  if isinstance(options.get(field,[]),list))}

def abandon_prepared(db, project_id, job_id):
    """Discard an unpaid draft only; submitted/unknown tasks cannot be erased."""
    if db.in_transaction:
        raise RuntimeError("cannot discard media task in a foreign transaction")
    _owned_row(db,project_id,job_id)
    try:
        db.execute("BEGIN IMMEDIATE")
        deleted=db.execute("""DELETE FROM kie_media_tasks WHERE id=? AND
            project_id=? AND state='prepared'""",(job_id,project_id)).rowcount
        if deleted!=1:
            raise RuntimeError("paid or uncertain provider task cannot be discarded")
        db.commit()
    except BaseException:
        db.rollback()
        raise
