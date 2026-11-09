"""Technical limits for explicitly installed, offline generative engines."""
import math

ENGINES={'image_instruction':'instruct_pix2pix','image_reference':'sd15_ip_adapter',
         'image_inpaint':'sd15_inpaint','video_generative':'animatediff_v2v'}
GEN_TASKS=frozenset(ENGINES)


def output_kind(request):
    return 'image' if request['task'].startswith('image_') else 'video'


def source_kind(request,index):
    return 'video' if request['task']=='video_generative' and index==0 else 'image'


def instruction_image_guidance(strength):
    """A single user-visible edit strength for Pix2Pix and other media engines.

    Diffusers InstructPix2Pix uses image_guidance_scale instead of img2img
    strength. Higher image guidance preserves the input more strongly, hence
    the inverse mapping. Keep in [1.0, 1.95] without changing other engines.
    """
    if type(strength) not in (int,float) or not math.isfinite(strength) or not .05<=strength<=1:
        raise ValueError('Generative limit: strength')
    return round(2.0-float(strength),4)


def validate_generation(request):
    if not isinstance(request,dict) or not isinstance(request.get('task'),str) or request['task'] not in ENGINES:raise ValueError('Known generative task required')
    allowed={'task','sources','expected_sha256','prompt','generation','width','height',
             'steps','seed','strength','guidance','frames','fps','start'}
    if set(request)-allowed:raise ValueError('Unknown generative request fields')
    task=request['task'];sources=request.get('sources')
    minimum=2 if task in ('image_reference','image_inpaint') else 1
    maximum=2 if task=='video_generative' else minimum
    if not isinstance(sources,list) or not minimum<=len(sources)<=maximum or any(not isinstance(p,str) or not p for p in sources):
        raise ValueError('Generative source/reference/mask count invalid')
    if len(set(sources))!=len(sources):raise ValueError('Source, mask and reference must be different files')
    if not isinstance(request.get('prompt'),str) or not 1<=len(request['prompt'].strip())<=6000:raise ValueError('Edit prompt required')
    profile=request.get('generation')
    if not isinstance(profile,dict) or set(profile) not in ({'engine','bundle','manifest_sha256','device'},{'engine','bundle','manifest_sha256','device','python'}) or profile['engine']!=ENGINES[task]:
        raise ValueError('Installed engine profile does not match task')
    if 'python' in profile and (not isinstance(profile['python'],str) or not profile['python']):raise ValueError('Generative Python executable required')
    if profile['device'] not in ('cuda','cpu'):raise ValueError('Unsupported generative device')
    if not isinstance(profile['bundle'],str) or not profile['bundle']:raise ValueError('Local model bundle required')
    sha=profile['manifest_sha256']
    if not isinstance(sha,str) or len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):raise ValueError('Model manifest digest required')
    for key,default,low,high in [('width',512,64,512),('height',512,64,512),('steps',20,2,50),('seed',0,0,2147483647)]:
        value=request.get(key,default)
        if type(value) is not int or not low<=value<=high:raise ValueError('Generative limit: '+key)
        if key in ('width','height') and value%8:raise ValueError('Generative dimensions must be multiples of 8')
    for key,default,low,high in [('strength',.65,.05,1),('guidance',7.5,1,15)]:
        value=request.get(key,default)
        if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:raise ValueError('Generative limit: '+key)
    # InstructPix2Pix has no denoising-strength parameter: the same UI knob
    # maps to inverse image_guidance_scale; denoising-step limit is only for
    # img2img/inpainting/video diffusion.
    if task!='image_instruction' and int(request.get('steps',20)*request.get('strength',.65))<1:
        raise ValueError('Strength must permit at least one denoising step')
    if task=='video_generative':
        if request.get('width',512)>384 or request.get('height',512)>384:raise ValueError('8 GB video profile supports at most 384 pixels per side')
        for key,default,low,high in [('frames',16,4,16),('fps',8,1,12)]:
            value=request.get(key,default)
            if type(value) is not int or not low<=value<=high:raise ValueError('Video generation limit: '+key)
        start=request.get('start',0)
        if type(start) not in (int,float) or not math.isfinite(start) or not 0<=start<=3600:raise ValueError('Video start invalid')
    elif any(key in request for key in ('frames','fps','start')):raise ValueError('Video options supplied to image engine')
