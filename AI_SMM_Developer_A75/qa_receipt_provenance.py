"""Bind every local media QA verdict to one request and immutable media bytes.

No model, Qt, network or GPU dependencies. A completed child from an older
run cannot certify a different output or changed source/reference/mask.
"""
import hashlib
import json
from pathlib import Path
import secrets


def output_sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def _sources(case):
    if case is None:
        return []
    if not isinstance(case,dict):
        raise ValueError('LOCAL_QUALITY_REVIEW_SOURCE_BINDING_INVALID')
    sources=case.get('sources',[])
    if (not isinstance(sources,list) or len(sources)>2
            or any(not isinstance(p,str) or not p for p in sources)):
        raise ValueError('LOCAL_QUALITY_REVIEW_SOURCE_BINDING_INVALID')
    return sources


def case_sha256(case):
    """Canonical, strict binding of the entire immutable visual QA instruction.

    Source and output bytes are hashed separately. This additionally binds
    prompt, edit kind, geometry, timing, references and every other case field.
    A child may never certify a different instruction using the same pixels.
    """
    if case is not None and not isinstance(case,dict):
        raise ValueError('LOCAL_QUALITY_REVIEW_CASE_BINDING_INVALID')
    try:
        encoded=json.dumps(case,ensure_ascii=False,sort_keys=True,
                           separators=(',',':'),allow_nan=False).encode('utf-8')
    except (TypeError,ValueError,OverflowError) as exc:
        raise ValueError('LOCAL_QUALITY_REVIEW_CASE_BINDING_INVALID') from exc
    if len(encoded)>262144:
        raise ValueError('LOCAL_QUALITY_REVIEW_CASE_BINDING_INVALID')
    return hashlib.sha256(encoded).hexdigest()


def make_request(output,case=None):
    return {'request_id':secrets.token_hex(16),
            'output_sha256':output_sha256(output),
            'source_sha256':[output_sha256(path) for path in _sources(case)],
            'case_sha256':case_sha256(case)}


def validate_output_binding(request,output,case=None):
    sources=_sources(case)
    if (not isinstance(request,dict)
            or not isinstance(request.get('request_id'),str)
            or len(request['request_id'])!=32
            or any(c not in '0123456789abcdef' for c in request['request_id'])
            or not isinstance(request.get('output_sha256'),str)
            or len(request['output_sha256'])!=64
            or any(c not in '0123456789abcdef' for c in request['output_sha256'])
            or output_sha256(output)!=request['output_sha256']):
        raise ValueError('LOCAL_QUALITY_REVIEW_OUTPUT_BINDING_INVALID')
    hashes=request.get('source_sha256')
    if (not isinstance(hashes,list) or len(hashes)!=len(sources)
            or any(not isinstance(sha,str) or len(sha)!=64
                   or any(c not in '0123456789abcdef' for c in sha) for sha in hashes)):
        raise ValueError('LOCAL_QUALITY_REVIEW_SOURCE_BINDING_INVALID')
    for path,sha in zip(sources,hashes):
        try:
            if output_sha256(path)!=sha:
                raise ValueError('LOCAL_QUALITY_REVIEW_SOURCE_BINDING_INVALID')
        except OSError as exc:
            raise ValueError('LOCAL_QUALITY_REVIEW_SOURCE_BINDING_INVALID') from exc
    if (not isinstance(request.get('case_sha256'),str)
            or request['case_sha256']!=case_sha256(case)):
        raise ValueError('LOCAL_QUALITY_REVIEW_CASE_BINDING_INVALID')


def validate_provenance(report,request,output,case=None):
    validate_output_binding(request,output,case)
    if (not isinstance(report,dict)
            or report.get('request_id')!=request['request_id']
            or report.get('output_sha256')!=request['output_sha256']
            or report.get('source_sha256')!=request['source_sha256']
            or report.get('case_sha256')!=request['case_sha256']):
        raise ValueError('LOCAL_QUALITY_REVIEW_PROVENANCE_INVALID')
    return report


def validate_media_metrics(case,metrics):
    """Independently reject impossible/insufficient objective QA evidence.

    The native child computes metrics; the parent must not treat a valid
    semantic verdict as proof that the measurements themselves are sound.
    """
    import math

    def valid_number(value,low,high):
        return (type(value) in (int,float) and math.isfinite(value)
                and low<=value<=high)

    if not isinstance(case,dict) or not isinstance(metrics,dict):
        raise ValueError('LOCAL_QUALITY_REVIEW_METRICS_INVALID')
    width,height=metrics.get('width'),metrics.get('height')
    if (type(width) is not int or type(height) is not int
            or not 1<=width<=8192 or not 1<=height<=8192
            or ('width' in case and width!=case['width'])
            or ('height' in case and height!=case['height'])):
        raise ValueError('LOCAL_QUALITY_REVIEW_METRICS_INVALID')
    if case.get('task')=='video_generative':
        frames=metrics.get('sampled_frames')
        if (not valid_number(metrics.get('duration'),.01,300)
                or not isinstance(frames,list) or len(frames)!=3
                or any(not isinstance(frame,dict)
                       or not valid_number(frame.get('contrast'),.008,1)
                       or not valid_number(frame.get('luma'),0,1) for frame in frames)
                or not valid_number(metrics.get('sampled_luma_jump'),0,.8)
                or type(metrics.get('audio')) is not bool
                or type(metrics.get('source_audio')) is not bool):
            raise ValueError('LOCAL_QUALITY_REVIEW_METRICS_INVALID')
        fps=case.get('fps',8);count=case.get('frames',16)
        if (type(fps) is not int or type(count) is not int or fps<=0
                or abs(metrics['duration']-count/fps)>.16):
            raise ValueError('LOCAL_QUALITY_REVIEW_METRICS_INVALID')
        if metrics['source_audio']:
            comparison=metrics.get('audio_comparison')
            if (not metrics['audio'] or not isinstance(comparison,dict)
                    or not valid_number(comparison.get('best_correlation'),.9,1)):
                raise ValueError('LOCAL_QUALITY_REVIEW_METRICS_INVALID')
        elif 'audio_comparison' in metrics:
            raise ValueError('LOCAL_QUALITY_REVIEW_METRICS_INVALID')
    else:
        if (not valid_number(metrics.get('contrast'),.008,1)
                or not valid_number(metrics.get('mean_absolute_change'),.001,1)
                or (case.get('task')=='image_inpaint'
                    and metrics.get('protected_pixels_exact') is not True)):
            raise ValueError('LOCAL_QUALITY_REVIEW_METRICS_INVALID')
    return metrics
