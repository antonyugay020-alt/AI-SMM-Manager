"""Durable unpaid source intents and independently confirmed maximum quotes.

Preparing sources can upload only after explicit consent. It never dispatches
paid generation. Stable intent/job IDs let restart adopt an already prepared
job instead of losing it or recreating a billable task.
"""
import hashlib
import json
from pathlib import Path
from uuid import uuid4
from datetime import datetime,timezone
from cost_authorization import PaymentDenied
from kie_media_jobs import utcnow, _project_exists, _owned_row, _digest_file
from kie_media_runtime import KieMediaGateway, verified_image, verified_video
from media_task_control import LocalTaskStopped
from media_upload_transfer import copy_snapshot_with_check, upload_with_check

MODES={'image_pro':'nano-banana-pro','image_edit':'google/nano-banana-edit',
       'video_reference':'kling-3.0-omni/reference-to-video',
       'video_edit':'kling-3.0-omni/transformation'}


def install(db):
    db.execute('''CREATE TABLE IF NOT EXISTS media_generation_intents (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, model TEXT NOT NULL,
        request_json TEXT NOT NULL, request_sha256 TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('draft','preparing','prepared','interrupted','failed')),
        stop_requested INTEGER NOT NULL DEFAULT 0, claim TEXT,
        error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS media_generation_quotes (
        job_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
        maximum_usd TEXT NOT NULL, evidence TEXT NOT NULL, expires_at TEXT NOT NULL)''')


def create_intent(db,workspace,project_id,request,*,job_id=None,cancel_check=None):
    if db.in_transaction:raise RuntimeError('Foreign transaction in media preparation')
    _project_exists(db,project_id)
    value=json.loads(json.dumps(request,allow_nan=False))
    mode=value.get('mode')
    if mode not in MODES:raise ValueError('Unsupported generation mode')
    if set(value)-{'mode','prompt','sources','aspect_ratio','resolution','duration'}:
        raise ValueError('Unknown media request options')
    paths=value.get('sources',[])
    if not isinstance(paths,list) or len(paths)>10:raise ValueError('Invalid media source list')
    gate=KieMediaGateway(db,workspace,upload_file=None,create_task=None,query_task=None)
    sources=[]
    video=None
    images=[]
    # Validate and hash ALL sources before a single source may leave the project.
    for name in paths:
        if cancel_check is not None:cancel_check()
        path=gate._authorized(project_id,name)
        if path.suffix.lower() in ('.mp4','.mov'):
            if not mode.startswith('video') or video is not None:raise ValueError('Only one video source supported')
            verified_video(path,check=cancel_check)
            video=str(path)
        else:
            verified_image(path,limit_mb=50 if mode.startswith('video') else (30 if mode=='image_pro' else 10),
                           kling=mode.startswith('video'))
            images.append(str(path))
        sources.append({'path':str(path),'sha256':_digest_file(path)})
    if mode=='image_edit' and not images:raise ValueError('Image editing requires a source')
    if mode=='video_edit' and video is None:raise ValueError('Video editing requires a video')
    if mode=='video_reference' and not (images or video):raise ValueError('Video generation requires source media')
    # Exercise the same full contract as the final uploaded payload, without I/O.
    from kie_media_contracts import nano_banana_pro,nano_banana_edit,kling_omni_reference_to_video,kling_omni_transformation
    urls=['https://preflight.invalid/source' for _ in images]
    prompt=value.get('prompt')
    ratio=value.get('aspect_ratio','1:1' if mode.startswith('image') else '16:9')
    resolution=value.get('resolution','1K' if mode.startswith('image') else '720p')
    metadata=verified_video(video,check=cancel_check)[1] if video else None
    if mode=='image_pro':nano_banana_pro(prompt,urls,ratio,resolution)
    elif mode=='image_edit':nano_banana_edit(prompt,urls,ratio)
    elif mode=='video_reference':
        kling_omni_reference_to_video(prompt,urls,video_url='https://preflight.invalid/video' if video else None,
            metadata=metadata,aspect_ratio=ratio,resolution=resolution,duration=value.get('duration',5))
    else:kling_omni_transformation(prompt,'https://preflight.invalid/video',metadata,urls,resolution,ratio)
    value['sources']=sources
    text=json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))
    ident=job_id or uuid4().hex
    if len(ident)!=32 or any(char not in '0123456789abcdef' for char in ident):raise ValueError('Invalid intent ID')
    if cancel_check is not None:cancel_check()
    try:
        db.execute('BEGIN IMMEDIATE');install(db)
        if cancel_check is not None:cancel_check()
        db.execute('INSERT INTO media_generation_intents VALUES(?,?,?,?,?,\'draft\',0,NULL,NULL,?,?)',
                   (ident,project_id,MODES[mode],text,hashlib.sha256(text.encode()).hexdigest(),utcnow(),utcnow()))
        db.commit()
    except BaseException:db.rollback();raise
    return ident


def intent(db,owner,ident):
    _project_exists(db,owner)
    row=db.execute('SELECT request_json,request_sha256,state,stop_requested,model FROM media_generation_intents WHERE id=? AND project_id=?',
                   (ident,owner)).fetchone()
    if row is None:raise ValueError('Media intent does not belong to project')
    if hashlib.sha256(row[0].encode()).hexdigest()!=row[1]:raise ValueError('Media intent fingerprint mismatch')
    return row


def check_intent(db,owner,ident):
    if intent(db,owner,ident)[3]:raise LocalTaskStopped('Media preparation stopped locally')


def prepare_intent(db,workspace,owner,ident,gateway,*,explicit_upload_consent=False,cancel_check=None):
    if explicit_upload_consent is not True:raise PermissionError('Source upload consent required')
    if db.in_transaction:raise RuntimeError('Foreign transaction owns preparation')
    def ensure_active():
        if cancel_check is not None:cancel_check()
        check_intent(db,owner,ident)
    ensure_active()
    row=intent(db,owner,ident)
    ensure_active()
    # If a prior process created the payload and died before updating its intent,
    # the stable task identity is authoritative. Never upload/create it again.
    table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='kie_media_tasks'").fetchone()
    if table and db.execute('SELECT 1 FROM kie_media_tasks WHERE id=? AND project_id=?',(ident,owner)).fetchone():
        existing=_owned_row(db,owner,ident)
        saved=json.loads(existing[1])['input']
        original=json.loads(row[0])
        if existing[0]!=row[4] or saved.get('prompt')!=original.get('prompt'):
            raise ValueError('Prepared task differs from intent')
        from media_intent_binding import require_bound
        require_bound(db,owner,ident,row[1],existing[2])
        db.execute("UPDATE media_generation_intents SET state='prepared',updated_at=? WHERE id=? AND project_id=?",(utcnow(),ident,owner))
        db.commit()
        return ident
    claim=uuid4().hex
    try:
        db.execute('BEGIN IMMEDIATE')
        count=db.execute("UPDATE media_generation_intents SET state='preparing',claim=?,error_code=NULL,updated_at=? WHERE id=? AND project_id=? AND state IN ('draft','interrupted','failed') AND stop_requested=0",
                         (claim,utcnow(),ident,owner)).rowcount
        if count!=1:raise RuntimeError('Media preparation already claimed')
        db.commit()
    except BaseException:db.rollback();raise
    try:
        request=json.loads(row[0])
        images=[];video=None
        for source in request['sources']:
            path=gateway._authorized(owner,source['path'])
            if _digest_file(path)!=source['sha256']:raise ValueError('Media source changed since confirmation')
            if path.suffix.lower() in ('.mp4','.mov'):video=str(path)
            else:images.append(str(path))
        original_upload=gateway.upload_file
        def upload(path, *, check=None):
            def upload_checkpoint():
                ensure_active()
                if check is not None:check()
            from tempfile import TemporaryDirectory
            from project_file_boundary import require_managed_folder
            ensure_active()
            expected=next(item['sha256'] for item in request['sources'] if item['path']==str(path))
            folder=require_managed_folder(Path(workspace(owner)),'.kie_upload_snapshots')
            folder.mkdir(parents=True,exist_ok=True)
            with TemporaryDirectory(dir=folder) as temporary:
                snapshot=Path(temporary)/('source'+Path(path).suffix.lower())
                copy_snapshot_with_check(path,snapshot,check=upload_checkpoint)
                if _digest_file(snapshot)!=expected:raise ValueError('Source changed before upload snapshot')
                ensure_active()
                result=upload_with_check(original_upload,snapshot,check=upload_checkpoint)
            ensure_active()
            return result
        gateway.upload_file=upload
        try:
            options={key:request[key] for key in ('prompt','aspect_ratio','resolution') if key in request}
            if request['mode'].startswith('image'):
                job=gateway.prepare_image(owner,sources=images,pro=request['mode']=='image_pro',
                    explicit_upload_consent=True,job_id=ident,intent_sha256=row[1],
                    cancel_check=ensure_active,**options)
            else:
                job=gateway.prepare_video(owner,source_video=video,source_images=images,
                    reference_mode=request['mode']=='video_reference',duration=request.get('duration',5),
                    explicit_upload_consent=True,job_id=ident,
                    cancel_check=ensure_active,
                    intent_sha256=row[1],**options)
        finally:gateway.upload_file=original_upload
        ensure_active()
        changed=db.execute("UPDATE media_generation_intents SET state='prepared',updated_at=? WHERE id=? AND claim=?",(utcnow(),ident,claim)).rowcount
        if changed!=1:raise RuntimeError('Media preparation claim lost')
        db.commit()
        ensure_active()
        return job
    except BaseException as exc:
        db.execute("UPDATE media_generation_intents SET state='failed',error_code=?,updated_at=? WHERE id=? AND claim=?",
                   (type(exc).__name__,utcnow(),ident,claim));db.commit()
        raise


def confirm_quote(db,owner,job,maximum_usd,evidence,*,confirmed=False,expires_at):
    if confirmed is not True:raise PaymentDenied('User confirmation of verified maximum price required')
    if db.in_transaction:raise RuntimeError('Foreign transaction owns quote')
    from durable_cost_gate import units
    from cost_authorization import money
    if units(maximum_usd)<=0:raise PaymentDenied('Positive verified maximum price required')
    if not isinstance(evidence,str) or not evidence.strip() or len(evidence)>500:
        raise PaymentDenied('Verified pricing reference required')
    expiry=datetime.fromisoformat(expires_at)
    now=datetime.now(timezone.utc)
    if expiry.tzinfo is None or not now<expiry or (expiry-now).total_seconds()>600:
        raise PaymentDenied('Quote must expire within ten minutes')
    row=_owned_row(db,owner,job)
    if row[3]!='prepared':raise RuntimeError('Quote applies only to an unpaid draft')
    digest=row[2]
    try:
        db.execute('BEGIN IMMEDIATE');install(db)
        db.execute('INSERT OR REPLACE INTO media_generation_quotes VALUES(?,?,?,?,?,?)',
            (job,owner,digest,str(money(maximum_usd)),evidence.strip(),expires_at));db.commit()
    except BaseException:db.rollback();raise
    return str(money(maximum_usd))


def quote_for_job(db,owner,job):
    row=_owned_row(db,owner,job)
    quote=db.execute('SELECT payload_sha256,maximum_usd,expires_at FROM media_generation_quotes WHERE job_id=? AND project_id=?',
                     (job,owner)).fetchone()
    if not quote or quote[0]!=row[2] or datetime.fromisoformat(quote[2])<=datetime.now(timezone.utc):
        raise PaymentDenied('No valid request-bound verified maximum quote')
    return quote[1]


def recover_intents(db,owner):
    """Explicit startup-only recovery; do not invoke while a worker is alive."""
    if db.in_transaction:raise RuntimeError('Foreign transaction owns recovery')
    _project_exists(db,owner)
    install(db)
    db.execute("UPDATE media_generation_intents SET state='interrupted',error_code='interrupted_preparation',updated_at=? WHERE project_id=? AND state='preparing'",(utcnow(),owner))
    db.commit()
