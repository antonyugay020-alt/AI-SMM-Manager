"""Durable, project-scoped Kie media task orchestration.

The paid submit boundary is intentionally not retried automatically after an
uncertain network result. An HTTP timeout might already have created a task.
File upload and Get Task Details are supplied as adapters until their exact
provider OpenAPI schemas are verified. Never invent endpoints.
"""
from __future__ import annotations

import hashlib
import json
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
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        error_code TEXT, CHECK (state IN (
        'prepared','submitting','submitted','completed','failed',
        'submission_unknown')))""")
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
        provider_task_id,output_path FROM kie_media_tasks
        WHERE id=? AND project_id=?""",(job_id,project_id)).fetchone()
    if row is None:
        raise ValueError("job does not belong to requested project")
    if hashlib.sha256(row[1].encode("utf-8")).hexdigest()!=row[2]:
        raise ValueError("persisted Kie task payload was modified")
    return row


def submit(db, project_id, job_id, create_task, *, enable_paid=False):
    """Attempt one paid provider call; a crash/timeout cannot auto-resubmit."""
    if not enable_paid:
        raise RuntimeError("paid Kie media generation must be explicitly enabled")
    if db.in_transaction:
        raise RuntimeError("foreign SQLite transaction in progress")
    row=_owned_row(db,project_id,job_id)
    if row[3]!="prepared":
        raise RuntimeError("Kie task already submitted or outcome uncertain")
    try:
        db.execute("BEGIN IMMEDIATE")
        updated=db.execute("""UPDATE kie_media_tasks
            SET state='submitting',updated_at=? WHERE id=? AND project_id=?
            AND state='prepared'""",(utcnow(),job_id,project_id)).rowcount
        if updated!=1:raise RuntimeError("task submission fenced by another worker")
        db.commit()
    except BaseException:
        db.rollback()
        raise
    try:
        result=create_task(json.loads(row[1]))
        provider_id=extract_task_id(result)
    except Exception:
        db.execute("""UPDATE kie_media_tasks SET state='submission_unknown',
            error_code='provider_response_unconfirmed',updated_at=?
            WHERE id=? AND project_id=? AND state='submitting'""",
            (utcnow(),job_id,project_id))
        db.commit()
        raise
    db.execute("""UPDATE kie_media_tasks
        SET state='submitted',provider_task_id=?,updated_at=?
        WHERE id=? AND project_id=? AND state='submitting'""",
        (provider_id,utcnow(),job_id,project_id))
    db.commit()
    return provider_id



def _validate_media_bytes(path, *, is_video):
    """Verify bytes before they cross the project publication boundary."""
    if path.stat().st_size > (500 if is_video else 60)*1024*1024:
        raise ValueError("downloaded Kie media exceeds maximum size")
    if not is_video:
        from PIL import Image
        expected="PNG" if path.suffix.lower()==".png" else "JPEG"
        with Image.open(path) as image:
            if image.format!=expected:
                raise ValueError("downloaded photo MIME does not match output")
            image.verify()
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
        if float(metadata.get("format",{}).get("duration",0))<=0:
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
    model, _, _, state, provider_id, output_path=row
    if state=="completed":
        if not output_path:
            raise ValueError("completed Kie task has no artifact receipt")
        from project_file_boundary import require_managed_file
        dest=Path(output_path)
        require_managed_file(Path(workspace(project_id)),dest,("processed",))
        if not dest.is_file():
            raise ValueError("completed Kie artifact is missing")
        _validate_media_bytes(dest,is_video=model.startswith("kling-"))
        return str(dest)
    if state!="submitted" or not provider_id or not TASK_ID.fullmatch(provider_id):
        raise RuntimeError("task has no confirmed remote ID")
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
    from media_ingest_core import ingest, replay
    from project_file_boundary import require_managed_folder
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
        db.execute("""UPDATE kie_media_tasks SET state='completed',
            output_path=?,updated_at=? WHERE id=? AND project_id=?
            AND state='submitted'""",(str(resolved),utcnow(),job_id,project_id))
        db.commit()
        return str(resolved)
    root=Path(workspace(project_id))
    spool=require_managed_folder(root,".kie_downloads")
    spool.mkdir(parents=True,exist_ok=True)
    fmt=json.loads(row[1])["input"].get("output_format","png")
    ext=".mp4" if model.startswith("kling-") else (
        ".jpg" if fmt in ("jpg","jpeg") else ".png")
    staged=spool/(job_id+ext)
    if staged.exists() or staged.is_symlink():
        raise ValueError("stale media staging file requires reconciliation")
    try:
        fetch_verified_file(urls[0],staged)
        if not staged.is_file() or staged.is_symlink() or staged.stat().st_size==0:
            raise ValueError("downloaded Kie result missing or invalid")
        _validate_media_bytes(staged, is_video=model.startswith("kling-"))
        kind="video" if model.startswith("kling-") else "result"
        receipt=ingest(db,workspace,staged,project_id=project_id,
                       kind=kind,original_name="kie-"+job_id+ext,
                       item_id=job_id)
        if not receipt.published:
            raise RuntimeError("media result committed but publication pending")
        db.execute("""UPDATE kie_media_tasks SET state='completed',
            output_path=?,updated_at=?
            WHERE id=? AND project_id=? AND state='submitted'""",
            (str(receipt.final_path),utcnow(),job_id,project_id))
        db.commit()
        return str(receipt.final_path)
    finally:
        staged.unlink(missing_ok=True)


def recover_pending(db, project_id):
    """Return confirmed tasks to poll; NEVER resubmit uncertain paid intents."""
    if db.in_transaction:
        raise RuntimeError("foreign SQLite transaction in progress")
    _project_exists(db,project_id)
    db.execute("""UPDATE kie_media_tasks SET state='submission_unknown',
        error_code='interrupted_paid_submission',updated_at=?
        WHERE project_id=? AND state='submitting'""",(utcnow(),project_id))
    db.commit()
    return db.execute("""SELECT id,provider_task_id FROM kie_media_tasks
        WHERE project_id=? AND state='submitted' ORDER BY created_at,id""",
        (project_id,)).fetchall()
