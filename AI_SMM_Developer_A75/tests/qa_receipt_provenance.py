"""Bind every local media QA verdict to one request and immutable output bytes.

This module has no model, Qt, network or GPU dependencies. A completed child
from a previous run cannot certify the current image/video, even when its
model tag and vision digest happen to match.
"""
import hashlib
from pathlib import Path
import secrets


def output_sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def make_request(output):
    return {'request_id':secrets.token_hex(16),'output_sha256':output_sha256(output)}


def validate_output_binding(request,output):
    if (not isinstance(request,dict)
            or not isinstance(request.get('request_id'),str)
            or len(request['request_id'])!=32
            or any(c not in '0123456789abcdef' for c in request['request_id'])
            or not isinstance(request.get('output_sha256'),str)
            or len(request['output_sha256'])!=64
            or any(c not in '0123456789abcdef' for c in request['output_sha256'])
            or output_sha256(output)!=request['output_sha256']):
        raise ValueError('LOCAL_QUALITY_REVIEW_OUTPUT_BINDING_INVALID')


def validate_provenance(report,request,output):
    validate_output_binding(request,output)
    if (not isinstance(report,dict)
            or report.get('request_id')!=request['request_id']
            or report.get('output_sha256')!=request['output_sha256']):
        raise ValueError('LOCAL_QUALITY_REVIEW_PROVENANCE_INVALID')
    return report
