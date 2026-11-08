"""Provider/Qt-free technical contract for every supported local media task."""
import math

TASKS=frozenset(('image_resize','video_cut','video_format','video_montage','video_from_images'))


def validate_local_request(request):
    if not isinstance(request,dict) or set(request)-{'task','sources','width','height','start','end','seconds_per_image','expected_sha256'}:
        raise ValueError('Invalid local media request')
    task=request.get('task');sources=request.get('sources')
    if task not in TASKS or not isinstance(sources,list) or not 1<=len(sources)<=30:
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
    return tuple(task for task in sorted(TASKS) if task in declared and minimum[task])
