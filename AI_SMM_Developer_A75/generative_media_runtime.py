"""Parent-owned offline generation process and native video composition."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from generative_models import verify_bundle


class OfflineGenerationError(RuntimeError):
    def __init__(self,code,diagnostic=None):
        self.diagnostic=diagnostic or {}
        self.public_code=code
        super().__init__(code)


def _stage_deadline(stage,video):
    """Maximum seconds without a new subprocess stage, never a quality PASS.

    Native video inference on 8GB GPUs requires a longer budget than image
    generation; loading, lock acquisition, and interpreter startup are bounded
    separately. This does not change the total job deadline.
    """
    return {
        'runner_started':180,
        'acquiring_device_lock':240,
        'waiting_for_local_device':240,
        'device_lock_acquired':300,
        'verifying_model_bundle':300,
        # The same CUDA Python already passed a 90-second import preflight.
        # Bound a stalled native import separately from GPU inference.
        'importing_torch':180,
        'loading_pipeline':1200,
        'pipeline_loaded':300,
        'inference_started':3600 if video else 1500,
        'inference_complete':300,
        'receipt_written':180,
        'runner_completed':180,
    }.get(stage,180)


def _last_child_stage(log_path):
    """Read a bounded UTF-8 tail; no parsing of model output as instructions."""
    if not log_path.exists():return None
    with log_path.open('rb') as stream:
        size=stream.seek(0,2)
        stream.seek(max(0,size-8192))
        raw=stream.read()
    for line in reversed(raw.splitlines()):
        try:
            item=json.loads(line)
            if isinstance(item,dict) and isinstance(item.get('stage'),str):
                return item['stage'][:80]
        except (ValueError,UnicodeDecodeError):
            continue
    return None


def render_generation(request,sources,target,check,*,tool,probe):
    verify_bundle(request['generation'],check)
    width=request.get('width',512);height=request.get('height',512)
    video=request['task']=='video_generative';audio=False
    duration=request.get('frames',16)/request.get('fps',8)
    base=['ffmpeg','-nostdin','-v','error','-y']
    if video:
        original,audio=probe(sources[0]);start=request.get('start',0)
        if start+duration>original+.02:raise ValueError('Requested generation interval exceeds input video')
        folder=target.parent/'input_frames';folder.mkdir()
        tool(base+['-ss',str(start),'-i',str(sources[0]),'-vf',f'fps={request.get("fps",8)},scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1',
                   '-frames:v',str(request.get('frames',16)),str(folder/'%04d.png')],check)
        if len(list(folder.glob('*.png')))!=request.get('frames',16):raise ValueError('Video extraction incomplete')
    payload=target.parent/'generation_request.json'
    payload.write_text(json.dumps({'request':request,'inputs':[str(p) for p in sources],'target':str(target)},ensure_ascii=False),encoding='utf-8')
    log_path=target.parent/'generation_process.log'
    env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1','DO_NOT_TRACK':'1'}
    with log_path.open('wb') as log:
        process=subprocess.Popen([request['generation'].get('python',sys.executable),str(Path(__file__).with_name('generative_runner.py')),'--request',str(payload)],
            stdin=subprocess.PIPE,stdout=log,stderr=log,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+7200
        stage=None
        last_stage_at=time.monotonic()
        last_check_at=0
        try:
            from durable_json import atomic_write_json
            atomic_write_json(target.parent/'generation_process.json',{'pid':process.pid,'parent_pid':os.getpid(),'started_at':time.time()})
            while process.poll() is None:
                check()
                current=time.monotonic()
                if current-last_check_at>=1:
                    observed=_last_child_stage(log_path)
                    if observed and observed!=stage:
                        stage=observed
                        last_stage_at=current
                    last_check_at=current
                elapsed=current-last_stage_at
                stage_limit=_stage_deadline(stage,video)
                if elapsed>stage_limit:
                    raise OfflineGenerationError(
                        'LOCAL_MODEL_STARTUP_TIMEOUT' if stage is None else 'LOCAL_MODEL_STAGE_TIMEOUT',
                        {'pid':process.pid,'last_stage':stage or 'no_runner_output',
                         'idle_seconds':round(elapsed,1),'stage_limit_s':stage_limit})
                if current>deadline:raise TimeoutError('Offline generative deadline exceeded')
                time.sleep(.1)
            check()
            if process.returncode:
                # Keep a bounded safe diagnostic; never return raw provider/model paths to chat.
                code='LOCAL_MODEL_EXECUTION_FAILED'
                with log_path.open('rb') as handle:
                    handle.seek(max(0,log_path.stat().st_size-32768));tail=handle.read().decode('utf-8','replace')
                for line in tail.splitlines():
                    try:
                        item=json.loads(line);detail=item.get('detail')
                    except (ValueError,AttributeError):continue
                    if detail in ('CUDA_NOT_AVAILABLE','CUDA_MEMORY_OR_CAPABILITY_INSUFFICIENT','SYSTEM_RAM_INSUFFICIENT','LOCAL_DEVICE_LOCK_TIMEOUT'):code=detail
                    if item.get('error_code')=='OutOfMemoryError':code='CUDA_OUT_OF_MEMORY'
                raise OfflineGenerationError(code,{'returncode':process.returncode,'last_stage':_last_child_stage(log_path) or stage or 'no_runner_output','log_tail':tail[-8192:]})
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            if process.stdin is not None:process.stdin.close()
    receipt_path=target.parent/'generation_receipt.json'
    if not receipt_path.is_file() or receipt_path.stat().st_size>8192:raise ValueError('Generative execution receipt missing')
    receipt=json.loads(receipt_path.read_text(encoding='utf-8'))
    for key in ('engine','manifest_sha256','device'):
        if receipt.get(key)!=request['generation'][key]:raise ValueError('Generative execution identity mismatch')
    if receipt.get('seed')!=request.get('seed',0):raise ValueError('Generative seed receipt mismatch')
    if video:
        frames=target.parent/'output_frames'
        if receipt.get('frames')!=request.get('frames',16) or len(list(frames.glob('*.png')))!=request.get('frames',16):raise ValueError('Generative video frame receipt mismatch')
        command=base+['-framerate',str(request.get('fps',8)),'-start_number','0','-i',str(frames/'%04d.png')]
        if audio:command+=['-ss',str(request.get('start',0)),'-i',str(sources[0])]
        command+=['-map','0:v:0']
        if audio:command+=['-map','1:a:0','-c:a','aac']
        command+=['-t',str(duration),'-c:v','libx264','-pix_fmt','yuv420p','-movflags','+faststart',str(target)]
        tool(command,check)
    return receipt
