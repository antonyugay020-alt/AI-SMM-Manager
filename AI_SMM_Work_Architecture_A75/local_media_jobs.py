"""Durable local Pillow/FFmpeg jobs, fenced attempts and atomic media publication.

No AI capability or cloud fallback is inferred. Inputs must be registered to the
project, are snapshotted before execution, and retain hashes across recovery.
"""
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
import time
from uuid import uuid4
from PIL import Image,ImageOps
from project_file_boundary import require_managed_folder,require_managed_file
from project_media_sources import registered_source
from media_artifact_validation import digest_file,validate_media_bytes
from media_ingest_core import ingest,replay

TASKS=frozenset(('image_resize','video_cut','video_format','video_montage','video_from_images'))


class LocalMediaStopped(Exception):
    pass


def now():return datetime.now(timezone.utc).isoformat()


def install(db):
    db.execute('''CREATE TABLE IF NOT EXISTS local_media_jobs (
        id TEXT PRIMARY KEY,project_id TEXT NOT NULL,request_json TEXT NOT NULL,
        request_sha256 TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN
        ('prepared','running','completed','interrupted','failed','cancelled')),
        stop_requested INTEGER NOT NULL DEFAULT 0,claim TEXT,input_receipts_json TEXT,
        output_path TEXT,result_sha256 TEXT,error_code TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS local_media_events (
        id TEXT PRIMARY KEY,job_id TEXT NOT NULL,project_id TEXT NOT NULL,
        event TEXT NOT NULL,created_at TEXT NOT NULL)''')
    db.execute("""CREATE TABLE IF NOT EXISTS local_media_dialogue_links (
        job_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,dialogue_turn_id TEXT NOT NULL,
        kind TEXT NOT NULL,UNIQUE(project_id,dialogue_turn_id))""")


def _finish_linked(db,workspace,owner,job,output):
    link=db.execute('SELECT dialogue_turn_id,kind FROM local_media_dialogue_links WHERE job_id=? AND project_id=?',(job,owner)).fetchone()
    if link:
        from result_publication_runtime import ResultPublicationRuntime
        ResultPublicationRuntime(db,workspace).publish(owner,link[1],output,active_project_id=owner,
            operation_id='dialogue:'+owner+':'+link[0]+':'+link[1],dialogue_turn_id=link[0])


def _event(db,owner,job,event):
    db.execute('INSERT INTO local_media_events VALUES(?,?,?,?,?)',(uuid4().hex,job,owner,event,now()))


def _owner(db,owner):
    if not isinstance(owner,str) or not owner or not db.execute('SELECT 1 FROM projects WHERE id=?',(owner,)).fetchone():
        raise ValueError('Local media project owner not found')


def _validate(request):
    if not isinstance(request,dict) or set(request)-{'task','sources','width','height','start','end','seconds_per_image'}:
        raise ValueError('Invalid local media request')
    task=request.get('task');sources=request.get('sources')
    if task not in TASKS or not isinstance(sources,list) or not 1<=len(sources)<=30:
        raise ValueError('Invalid local media task or sources')
    if any(not isinstance(path,str) or not path for path in sources):raise ValueError('Local source path required')
    if task in ('image_resize','video_cut','video_format') and len(sources)!=1:raise ValueError('Operation requires one source')
    if task=='video_montage' and len(sources)<2:raise ValueError('Montage requires at least two videos')
    width=request.get('width',720);height=request.get('height',1280)
    if type(width) is not int or type(height) is not int or not 64<=width<=4096 or not 64<=height<=4096:
        raise ValueError('Local output dimensions invalid')
    if task!='image_resize' and (width%2 or height%2):raise ValueError('Video dimensions must be even')
    if task=='video_cut':
        start=request.get('start',0);end=request.get('end')
        if type(start) not in (int,float) or type(end) not in (int,float) or not math.isfinite(start) or not math.isfinite(end) or not 0<=start<end or end-start>300:
            raise ValueError('Invalid local trim interval')
    if task=='video_from_images':
        seconds=request.get('seconds_per_image',3)
        if type(seconds) not in (int,float) or not math.isfinite(seconds) or not .25<=seconds<=30 or seconds*len(sources)>300:
            raise ValueError('Invalid slide duration')


def create_job(db,owner,request,*,dialogue_turn_id=None):
    if db.in_transaction:raise RuntimeError('Foreign transaction owns local task creation')
    _owner(db,owner);_validate(request)
    text=json.dumps(request,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    ident=uuid4().hex
    try:
        db.execute('BEGIN IMMEDIATE');install(db)
        _owner(db,owner)
        if dialogue_turn_id is not None:
            previous=db.execute('SELECT j.id,j.request_sha256 FROM local_media_dialogue_links l JOIN local_media_jobs j ON l.job_id=j.id AND l.project_id=j.project_id WHERE l.project_id=? AND l.dialogue_turn_id=?',(owner,dialogue_turn_id)).fetchone()
            if previous:
                if previous[1]!=hashlib.sha256(text.encode()).hexdigest():raise ValueError('Dialogue media retry changed its request')
                db.commit();return previous[0]
            intake=db.execute('SELECT state FROM ai_task_intake WHERE project_id=? AND dialogue_turn_id=?',(owner,dialogue_turn_id)).fetchone()
            if intake!=('resolving',):raise ValueError('Media job requires an active owned dialogue intake')
        db.execute('''INSERT INTO local_media_jobs
            (id,project_id,request_json,request_sha256,state,created_at,updated_at)
            VALUES(?,?,?,?,'prepared',?,?)''',(ident,owner,text,hashlib.sha256(text.encode()).hexdigest(),now(),now()))
        if dialogue_turn_id is not None:
            db.execute('INSERT INTO local_media_dialogue_links VALUES(?,?,?,?)',(ident,owner,dialogue_turn_id,'image' if request['task']=='image_resize' else 'video'))
        _event(db,owner,ident,'prepared');db.commit()
    except BaseException:db.rollback();raise
    return ident


def _row(db,owner,job):
    _owner(db,owner)
    row=db.execute('''SELECT request_json,request_sha256,state,stop_requested,claim,
        input_receipts_json,output_path,result_sha256 FROM local_media_jobs WHERE id=? AND project_id=?''',(job,owner)).fetchone()
    if row is None:raise ValueError('Local job does not belong to project')
    if hashlib.sha256(row[0].encode()).hexdigest()!=row[1]:raise ValueError('Local media request fingerprint mismatch')
    return row


def _probe(path):
    result=subprocess.run(['ffprobe','-v','error','-show_entries',
        'stream=codec_type,width,height:format=duration','-of','json',str(path)],capture_output=True,check=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    data=json.loads(result.stdout)
    duration=float(data.get('format',{}).get('duration',0))
    video=next((row for row in data.get('streams',[]) if row.get('codec_type')=='video'),None)
    if not video or not math.isfinite(duration) or not 0<duration<=3600:
        raise ValueError('Invalid local video source')
    if not 0<int(video.get('width',0))<=8192 or not 0<int(video.get('height',0))<=8192:
        raise ValueError('Local video dimensions invalid')
    return duration,any(row.get('codec_type')=='audio' for row in data.get('streams',[]))


def _tool(command,check,*,timeout=1200):
    process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    deadline=time.monotonic()+timeout
    try:
        while True:
            check()
            if time.monotonic()>deadline:raise TimeoutError('Local media tool deadline exceeded')
            try:
                _,error=process.communicate(timeout=.1)
                if process.returncode:raise RuntimeError('Local media tool failed')
                return
            except subprocess.TimeoutExpired:continue
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
        if process.stderr is not None:process.stderr.close()


def _render(request,sources,target,check):
    task=request['task'];width=request.get('width',720);height=request.get('height',1280)
    if task=='image_resize':
        with Image.open(sources[0]) as image:
            image.load();image=ImageOps.exif_transpose(image).convert('RGB')
            ImageOps.pad(image,(width,height),method=Image.Resampling.LANCZOS,color='white').save(target,'PNG')
        return
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):raise RuntimeError('Native FFmpeg and ffprobe required')
    base=['ffmpeg','-nostdin','-v','error','-threads','2','-filter_complex_threads','1','-y']
    scale=f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p'
    if task in ('video_cut','video_format'):
        if task=='video_cut' and 'width' not in request and 'height' not in request:
            scale='pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1,format=yuv420p'
        duration,_=_probe(sources[0])
        command=base+['-i',str(sources[0])]
        if task=='video_cut':
            if request['end']>duration+.05:raise ValueError('Trim interval exceeds source video')
            command+=['-ss',str(request.get('start',0)),'-t',str(request['end']-request.get('start',0))]
        elif duration>300:raise ValueError('Local output exceeds five-minute task budget')
        command+=['-map','0:v:0','-map','0:a:0?','-vf',scale,'-c:v','libx264','-preset','veryfast','-c:a','aac','-movflags','+faststart',str(target)]
    else:
        filters=[];labels=[];command=base[:];total=0
        for index,path in enumerate(sources):
            check()
            if task=='video_from_images':
                duration=request.get('seconds_per_image',3)
                command+=['-loop','1','-t',str(duration),'-i',str(path)]
                audio=False
            else:
                duration,audio=_probe(path)
                command+=['-i',str(path)]
            total+=duration
            filters.append(f'[{index}:v]{scale},setpts=PTS-STARTPTS[v{index}]')
            labels.append(f'[v{index}]')
            if task=='video_montage':
                if audio:filters.append(f'[{index}:a]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[a{index}]')
                else:filters.append(f'anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration={duration},asetpts=PTS-STARTPTS[a{index}]')
                labels.append(f'[a{index}]')
        if total>300:raise ValueError('Local output exceeds five-minute task budget')
        audio=task=='video_montage'
        filters.append(''.join(labels)+f'concat=n={len(sources)}:v=1:a={1 if audio else 0}[v]'+('[a]' if audio else ''))
        command+=['-filter_complex',';'.join(filters),'-map','[v]']
        if audio:command+=['-map','[a]','-c:a','aac']
        command+=['-c:v','libx264','-preset','veryfast','-movflags','+faststart',str(target)]
    _tool(command,check)


def _adopt_receipt(db,workspace,owner,job,is_video):
    existing=db.execute('SELECT kind,path FROM project_files WHERE id=? AND project_id=?',(job,owner)).fetchone()
    if existing is None:return None
    expected='video' if is_video else 'result'
    if existing[0]!=expected:raise ValueError('Local output registry kind changed')
    replay(db,workspace,project_id=owner)
    path=Path(existing[1]);require_managed_file(Path(workspace(owner)),path,('processed',))
    canonical=Path(workspace(owner))/'processed'/(job+('.mp4' if is_video else '.png'))
    if path!=canonical:raise ValueError('Local output registry path differs from planned identity')
    receipt=db.execute('SELECT source_sha256,source_size,state FROM media_ingest_jobs WHERE id=? AND project_id=?',(job,owner)).fetchone()
    if not receipt or receipt[2]!='done' or not path.is_file() or path.stat().st_size!=receipt[1] or digest_file(path)!=receipt[0]:
        raise ValueError('Local output receipt is not verified')
    validate_media_bytes(path,is_video=is_video)
    _finish_linked(db,workspace,owner,job,str(path))
    db.execute("UPDATE local_media_jobs SET state='completed',output_path=?,result_sha256=?,error_code=NULL,updated_at=? WHERE id=? AND project_id=?",
               (str(path),receipt[0],now(),job,owner));_event(db,owner,job,'recovered_publication');db.commit()
    return str(path)


def execute_job(db,workspace,owner,job,*,cancel_check=None):
    if db.in_transaction:raise RuntimeError('Foreign transaction owns local execution')
    row=_row(db,owner,job);request=json.loads(row[0]);_validate(request)
    is_video=request['task']!='image_resize'
    # A committed ingest receipt is the publication boundary, even after crash.
    result=_adopt_receipt(db,workspace,owner,job,is_video)
    if result:return result
    if row[2]=='completed':raise ValueError('Completed local task lost its publication receipt')
    if row[3] or row[2]=='cancelled':raise LocalMediaStopped('Local task cancelled')
    claim=uuid4().hex
    try:
        db.execute('BEGIN IMMEDIATE')
        count=db.execute("UPDATE local_media_jobs SET state='running',claim=?,error_code=NULL,updated_at=? WHERE id=? AND project_id=? AND state IN ('prepared','interrupted','failed') AND stop_requested=0",
                         (claim,now(),job,owner)).rowcount
        if count!=1:raise RuntimeError('Local media task already claimed')
        _event(db,owner,job,'running');db.commit()
    except BaseException:db.rollback();raise
    def check():
        current=_row(db,owner,job)
        if current[3] or current[4]!=claim or current[2]!='running':raise LocalMediaStopped('Local attempt lost authority')
        if cancel_check is not None:cancel_check()
    try:
        check();registered=[];receipts=[]
        for name in request['sources']:
            check();path=registered_source(db,workspace,owner,name)
            if not 0<path.stat().st_size<=500*1024*1024:raise ValueError('Local source byte budget exceeded')
            if request['task'] in ('image_resize','video_from_images'):
                with Image.open(path) as image:
                    if image.width*image.height>40_000_000:raise ValueError('Local source pixel budget exceeded')
                    image.load()
            else:_probe(path)
            registered.append(path);receipts.append({'path':str(path),'sha256':digest_file(path)})
        serialized=json.dumps(receipts,sort_keys=True,separators=(',',':'))
        if row[5] and row[5]!=serialized:raise ValueError('Local input changed since first execution')
        db.execute('UPDATE local_media_jobs SET input_receipts_json=? WHERE id=? AND claim=?',(serialized,job,claim));db.commit()
        root=Path(workspace(owner));folder=require_managed_folder(root,'.local_media_work');folder.mkdir(parents=True,exist_ok=True)
        with TemporaryDirectory(dir=folder) as temporary:
            temp=Path(temporary);sources=[]
            for index,path in enumerate(registered):
                check();snapshot=temp/(str(index)+path.suffix.lower())
                shutil.copyfile(path,snapshot)
                if digest_file(snapshot)!=receipts[index]['sha256']:raise ValueError('Local source changed during snapshot')
                sources.append(snapshot)
            target=temp/('output.mp4' if is_video else 'output.png')
            _render(request,sources,target,check);check()
            validate_media_bytes(target,is_video=is_video)
            check()
            receipt=ingest(db,workspace,target,project_id=owner,kind='video' if is_video else 'result',
                           item_id=job,original_name=request['task']+target.suffix,authorize=check)
            if not receipt.published:raise RuntimeError('Local publication pending recovery')
            _finish_linked(db,workspace,owner,job,str(receipt.final_path))
            db.execute("UPDATE local_media_jobs SET state='completed',output_path=?,result_sha256=?,updated_at=? WHERE id=? AND project_id=? AND claim=?",
                       (str(receipt.final_path),digest_file(receipt.final_path),now(),job,owner,claim))
            _event(db,owner,job,'completed');db.commit()
            return str(receipt.final_path)
    except BaseException as exc:
        if db.in_transaction:db.rollback()
        state='cancelled' if isinstance(exc,LocalMediaStopped) else 'failed'
        db.execute("UPDATE local_media_jobs SET state=?,error_code=?,updated_at=? WHERE id=? AND project_id=? AND claim=? AND state='running'",
                   (state,type(exc).__name__,now(),job,owner,claim));_event(db,owner,job,state);db.commit()
        raise


def stop_job(db,owner,job):
    if db.in_transaction:raise RuntimeError('Foreign transaction owns local stop')
    try:
        db.execute('BEGIN IMMEDIATE');row=_row(db,owner,job)
        if row[2]=='completed' or db.execute('SELECT 1 FROM project_files WHERE id=? AND project_id=?',(job,owner)).fetchone():
            db.rollback();return False
        db.execute("UPDATE local_media_jobs SET stop_requested=1,state=CASE WHEN state='running' THEN state ELSE 'cancelled' END,updated_at=? WHERE id=? AND project_id=?",(now(),job,owner))
        link=db.execute('SELECT dialogue_turn_id FROM local_media_dialogue_links WHERE job_id=? AND project_id=?',(job,owner)).fetchone()
        if link:
            from task_intake import update_intake
            update_intake(db,owner,link[0],'failed')
        _event(db,owner,job,'stop_requested');db.commit();return True
    except BaseException:db.rollback();raise


def recover_jobs(db,owner):
    if db.in_transaction:raise RuntimeError('Foreign transaction owns local recovery')
    _owner(db,owner);install(db)
    db.execute("UPDATE local_media_jobs SET state=CASE WHEN stop_requested=1 THEN 'cancelled' ELSE 'interrupted' END,updated_at=? WHERE project_id=? AND state='running'",(now(),owner));db.commit()


def list_jobs(db,owner):
    if db.in_transaction:raise RuntimeError('Foreign transaction owns local listing')
    _owner(db,owner);install(db);db.commit()
    return [{'id':row[0],'model':'local/pillow' if json.loads(row[1])['task']=='image_resize' else 'local/ffmpeg',
             'state':row[2],'local_stop_requested':bool(row[3]),'output_path':row[4],'error_code':row[5],'created_at':row[6]}
            for row in db.execute('SELECT id,request_json,state,stop_requested,output_path,error_code,created_at FROM local_media_jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 200',(owner,))]
