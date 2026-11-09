"""Offline Diffusers subprocess. No implicit model download or remote code.

APIs pinned to diffusers 0.36.0. Parent owns cancellation; EOF watchdog prevents
an orphan CUDA workload when the GUI process dies. No GPU acceptance is implied.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time
from PIL import Image,ImageOps
from generative_contract import validate_generation
from generative_models import verify_bundle
from project_file_boundary import reject_path_link


def _stage(name):
    # Captured by the owning local worker, never sent to an external service.
    print(json.dumps({'stage':name,'monotonic_s':round(time.monotonic(),3)}),flush=True)


def device_lock(path=None,*,wait=True,max_wait_seconds=180):
    from local_compute_lease import device_lease
    return device_lease(path,wait=wait,max_wait_seconds=max_wait_seconds,
        on_wait=lambda:_stage('waiting_for_local_device'))


def device_policy(torch,device):
    if device=='cpu':return torch.float32
    if not torch.cuda.is_available():raise RuntimeError('CUDA_NOT_AVAILABLE')
    major,minor=torch.cuda.get_device_capability(0)
    free,total=torch.cuda.mem_get_info(0)
    if major<7 or total<6*2**30 or free<3*2**30:raise RuntimeError('CUDA_MEMORY_OR_CAPABILITY_INSUFFICIENT')
    # RTX 2080 Super (7.5): native FP16; never BF16 or BF16 emulation.
    return torch.float16


def load_pipeline(request,torch):
    from diffusers import (StableDiffusionInstructPix2PixPipeline,StableDiffusionImg2ImgPipeline,
        StableDiffusionInpaintPipeline,AnimateDiffVideoToVideoPipeline,MotionAdapter)
    profile=request['generation'];root=Path(profile['bundle']);device=profile['device']
    dtype=device_policy(torch,device)
    if device=='cuda':
        import psutil
        minimum=10 if request['task']=='video_generative' else 6
        if psutil.virtual_memory().available<minimum*2**30:raise RuntimeError('SYSTEM_RAM_INSUFFICIENT')
    common={'torch_dtype':dtype,'local_files_only':True,'use_safetensors':True}
    task=request['task']
    if task=='image_instruction':pipe=StableDiffusionInstructPix2PixPipeline.from_pretrained(root/'model',**common)
    elif task=='image_reference':pipe=StableDiffusionImg2ImgPipeline.from_pretrained(root/'model',**common)
    elif task=='image_inpaint':pipe=StableDiffusionInpaintPipeline.from_pretrained(root/'model',**common)
    else:
        motion=MotionAdapter.from_pretrained(root/'motion',**common)
        pipe=AnimateDiffVideoToVideoPipeline.from_pretrained(root/'model',motion_adapter=motion,**common)
        pipe.unet.enable_forward_chunking(chunk_size=1,dim=1)
    if task=='image_reference' or (task=='video_generative' and len(request['sources'])==2):
        pipe.load_ip_adapter(str(root/'ip_adapter'),subfolder='models',
            weight_name='ip-adapter_sd15.safetensors',local_files_only=True)
        pipe.set_ip_adapter_scale(.6)
    pipe.vae.enable_slicing()
    pipe.vae.enable_tiling()
    if device=='cuda':pipe.enable_model_cpu_offload(gpu_id=0)
    else:pipe.to('cpu')
    return pipe,dtype


def image(path,width,height,*,mask=False):
    with Image.open(path) as value:
        value=ImageOps.exif_transpose(value).convert('L' if mask else 'RGB')
        return ImageOps.pad(value,(width,height),method=Image.Resampling.NEAREST if mask else Image.Resampling.LANCZOS,color=0 if mask else 'white')


def initialize_python_torch():
    """Phase 1: initialize native CPU libraries before obtaining the GPU lease.

    Preflight and production both run the same import path. Importing PyTorch
    under a GPU mutex previously hung on native Windows despite a successful
    isolated CUDA probe. The following phase may acquire GPU resources, but
    this import phase must never access a model, CUDA context or user data.
    """
    _stage('importing_torch')
    import faulthandler
    faulthandler.dump_traceback_later(45,repeat=True,file=sys.stderr)
    try:
        import torch
    finally:
        faulthandler.cancel_dump_traceback_later()
    _stage('torch_imported')
    return torch


def run(request,inputs,target,*,pipeline_loader=load_pipeline,preloaded_torch=None):
    validate_generation(request);profile=request['generation']
    _stage('verifying_model_bundle')
    verify_bundle(profile)
    torch=preloaded_torch if preloaded_torch is not None else initialize_python_torch()
    width=request.get('width',512);height=request.get('height',512)
    _stage('loading_pipeline')
    pipe,dtype=pipeline_loader(request,torch)
    _stage('pipeline_loaded')
    seed=request.get('seed',0)
    kwargs={'prompt':request['prompt'],'num_inference_steps':request.get('steps',20),
            'guidance_scale':request.get('guidance',7.5),'generator':torch.Generator(device='cpu').manual_seed(seed)}
    task=request['task'];first=image(inputs[0],width,height) if task!='video_generative' else None
    if task=='image_instruction':kwargs.update(image=first,image_guidance_scale=1.5)
    elif task=='image_inpaint':
        with Image.open(inputs[0]) as source,Image.open(inputs[1]) as mask:
            if ImageOps.exif_transpose(source).size!=ImageOps.exif_transpose(mask).size:
                raise ValueError('Mask geometry must match oriented source')
        kwargs.update(image=first,mask_image=image(inputs[1],width,height,mask=True),strength=request.get('strength',.65),width=width,height=height)
    elif task=='image_reference':kwargs.update(image=first,ip_adapter_image=image(inputs[1],width,height),strength=request.get('strength',.65))
    else:
        frames=sorted((target.parent/'input_frames').glob('*.png'))
        if len(frames)!=request.get('frames',16):raise ValueError('Prepared input frame count mismatch')
        kwargs.update(video=[image(path,width,height) for path in frames],width=width,height=height,strength=request.get('strength',.65),decode_chunk_size=1)
        if len(inputs)==2:kwargs['ip_adapter_image']=image(inputs[1],width,height)
    _stage('inference_started')
    with torch.inference_mode():result=pipe(**kwargs)
    _stage('inference_complete')
    if task=='video_generative':
        outputs=result.frames[0]
        if len(outputs)!=request.get('frames',16):raise ValueError('Generated frame count mismatch')
        folder=target.parent/'output_frames';folder.mkdir()
        for index,frame in enumerate(outputs):
            if frame.size!=(width,height):raise ValueError('Generated frame geometry mismatch')
            frame.save(folder/(str(index).zfill(4)+'.png'),'PNG')
    else:
        if getattr(result,'nsfw_content_detected',None) and any(result.nsfw_content_detected):raise RuntimeError('Model rejected generated image')
        if len(result.images)!=1 or result.images[0].size!=(width,height):raise ValueError('Generated image geometry mismatch')
        output=result.images[0]
        if task=='image_inpaint':output=Image.composite(output,first,image(inputs[1],width,height,mask=True))
        output.save(target,'PNG')
    from importlib.metadata import version
    runtime_versions={name:version(name) for name in ('torch','diffusers','transformers','accelerate','safetensors')} if pipeline_loader is load_pipeline else {'fixture':True}
    receipt={'runtime_versions':runtime_versions,'engine':profile['engine'],'manifest_sha256':profile['manifest_sha256'],
        'device':profile['device'],'dtype':str(dtype),'torch_version':torch.__version__,'diffusers_version':__import__('diffusers').__version__ if pipeline_loader is load_pipeline else 'fixture','peak_cuda_bytes':torch.cuda.max_memory_allocated(0) if profile['device']=='cuda' else None,'peak_cuda_reserved_bytes':torch.cuda.max_memory_reserved(0) if profile['device']=='cuda' else None,'seed':seed,'frames':request.get('frames') if task=='video_generative' else None}
    (target.parent/'generation_receipt.json').write_text(json.dumps(receipt),encoding='utf-8')
    _stage('receipt_written')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True)
    args=parser.parse_args()
    _stage('runner_started')
    # stdin is a parent-owned pipe, never a console. Parent death closes it.
    def parent_watch():
        while os.read(sys.stdin.fileno(),1024):pass
        os._exit(75)
    threading.Thread(target=parent_watch,daemon=True).start()
    data=json.loads(Path(args.request).read_text(encoding='utf-8'))
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    os.environ['HF_HUB_DISABLE_TELEMETRY']='1';os.environ['DO_NOT_TRACK']='1'
    # Check offline bundle integrity before loading expensive libraries.
    validate_generation(data['request'])
    _stage('verifying_model_bundle')
    verify_bundle(data['request']['generation'])
    torch=initialize_python_torch()
    _stage('acquiring_device_lock')
    with device_lock():
        _stage('device_lock_acquired')
        run(data['request'],[Path(p) for p in data['inputs']],Path(data['target']),
            preloaded_torch=torch)
    _stage('runner_completed')
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        import traceback
        print(json.dumps({'error_code':type(exc).__name__,'detail':str(exc)[:400],
            'traceback':traceback.format_exc()[-8192:]}),file=sys.stderr,flush=True)
        raise SystemExit(1)
