"""Provider/Qt-free technical contract for every supported local media task."""
import math
from generative_contract import GEN_TASKS,validate_generation

TASKS=frozenset(('image_resize','video_cut','video_format','video_montage','video_from_images'))


def validate_local_request(request):
    if isinstance(request,dict) and isinstance(request.get('task'),str) and request.get('task') in GEN_TASKS:
        validate_generation(request)
        expected=request.get('expected_sha256')
        if expected is not None and (not isinstance(expected,list) or len(expected)!=len(request['sources']) or any(value is not None and (not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value)) for value in expected)):
            raise ValueError('Invalid generative source receipts')
        return
    if not isinstance(request,dict) or set(request)-{'task','sources','width','height','start','end','seconds_per_image','expected_sha256'}:
        raise ValueError('Invalid local media request')
    task=request.get('task');sources=request.get('sources')
    if not isinstance(task,str) or task not in TASKS or not isinstance(sources,list) or not 1<=len(sources)<=30:
        raise ValueError('Invalid local media task or sources')
    if any(not isinstance(path,str) or not path for path in sources):raise ValueError('Local source path required')
    expected=request.get('expected_sha256')
    if expected is not None and (not isinstance(expected,list) or len(expected)!=len(sources)
            or any(value is not None and (not isinstance(value,str) or len(value)!=64 or any(char not in '0123456789abcdef' for char in value)) for value in expected)):
        raise ValueError('Invalid host-issued source receipt hashes')
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




def available_local_tasks(catalog,capabilities):
    """Capability and resource preconditions only; never inspect an utterance."""
    declared=capabilities.get('local_media_tasks',[]) if isinstance(capabilities,dict) else []
    if not isinstance(declared,(list,tuple)):return ()
    images=sum(item.get('kind')=='image' for item in catalog.values())
    videos=sum(item.get('kind')=='video' for item in catalog.values())
    minimum={'image_resize':images>=1,'video_from_images':images>=1,
             'video_cut':videos>=1,'video_format':videos>=1,'video_montage':videos>=2}
    local=tuple(task for task in sorted(TASKS) if task in declared and minimum[task])
    from generative_contract import ENGINES
    installed=capabilities.get('generative_profiles',{})
    if not isinstance(installed,dict):return local
    resources={'image_instruction':images>=1,'image_reference':images>=2,'image_inpaint':images>=2,'video_generative':videos>=1}
    return local+tuple(task for task,engine in ENGINES.items() if resources[task] and any(isinstance(p,dict) and p.get('engine')==engine for p in installed.values()))


def local_media_schema(catalog,capabilities):
    """Expose task-specific constraints to native decoding as well as the host."""
    variants=[{'type':'null'}]
    for task in available_local_tasks(catalog,capabilities):
        if task in GEN_TASKS:continue
        kind='image' if task in ('image_resize','video_from_images') else 'video'
        ids=[ident for ident,item in catalog.items() if item['kind']==kind]
        single=task in ('image_resize','video_cut','video_format')
        properties={'task':{'type':'string','enum':[task]},
            'source_ids':{'type':'array','items':{'type':'string','enum':ids},
                'minItems':2 if task=='video_montage' else 1,'maxItems':1 if single else 30,'uniqueItems':True},
            'width':{'type':['integer','null'],'minimum':64,'maximum':4096},
            'height':{'type':['integer','null'],'minimum':64,'maximum':4096},
            'start':{'type':'number','minimum':0} if task=='video_cut' else {'type':'null'},
            'end':{'type':'number','exclusiveMinimum':0} if task=='video_cut' else {'type':'null'},
            'seconds_per_image':{'type':['number','null'],'minimum':.25,'maximum':30} if task=='video_from_images' else {'type':'null'}}
        variants.append({'type':'object','additionalProperties':False,'required':list(properties),'properties':properties})
    from generative_contract import ENGINES
    for task in available_local_tasks(catalog,capabilities):
        if task not in GEN_TASKS:continue
        video=task=='video_generative'
        ids=[ident for ident,item in catalog.items() if video or item['kind']=='image']
        properties={'task':{'type':'string','enum':[task]},
            'source_ids':{'type':'array','items':{'type':'string','enum':ids},'minItems':2 if task in ('image_reference','image_inpaint') else 1,'maxItems':1 if task=='image_instruction' else 2,'uniqueItems':True},
            'profile':{'type':'string','enum':[name for name,p in capabilities['generative_profiles'].items() if p['engine']==ENGINES[task]]},
            'prompt':{'type':'string','minLength':1,'maxLength':6000},
            'width':{'type':'integer','minimum':64,'maximum':384 if video else 512,'multipleOf':8},
            'height':{'type':'integer','minimum':64,'maximum':384 if video else 512,'multipleOf':8},
            'steps':{'type':'integer','minimum':2,'maximum':50},'seed':{'type':'integer','minimum':0,'maximum':2147483647},
            'strength':{'type':'number','minimum':.05,'maximum':1},'guidance':{'type':'number','minimum':1,'maximum':15}}
        if video:properties.update(frames={'type':'integer','minimum':4,'maximum':16},fps={'type':'integer','minimum':1,'maximum':12},start={'type':'number','minimum':0})
        variants.append({'type':'object','additionalProperties':False,'required':list(properties),'properties':properties})
    return {'anyOf':variants}
