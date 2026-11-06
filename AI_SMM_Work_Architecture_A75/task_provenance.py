"""Canonical request provenance shared by planner, editor and persistence.

The original user request is not reconstructed from a model's plan or draft.
The digest is domain-separated and computed over exact UTF-8 bytes.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json


@dataclass(frozen=True)
class TaskProvenance:
    original_request: str
    request_sha256: str

    @classmethod
    def from_request(cls, request):
        original = str(request)
        if not original.strip():
            raise ValueError('Original task request must not be empty')
        digest = hashlib.sha256(b'ai-smm:original-request:v1\x00' + original.encode('utf-8')).hexdigest()
        return cls(original, digest)

    def verify(self, request):
        if self != self.from_request(request):
            raise ValueError('Task request changed after orchestration boundary')

    def as_dict(self):
        return {'schema': 'ai-smm.request-provenance.v1',
                'original_request': self.original_request,
                'request_sha256': self.request_sha256}

    @classmethod
    def restore(cls, obj):
        if not isinstance(obj, dict) or set(obj) != {'schema', 'original_request', 'request_sha256'} or obj['schema'] != 'ai-smm.request-provenance.v1':
            raise ValueError('Invalid request provenance record')
        if not isinstance(obj['original_request'], str) or not isinstance(obj['request_sha256'], str):
            raise ValueError('Invalid request provenance types')
        rebuilt = cls.from_request(obj['original_request'])
        if rebuilt.request_sha256 != obj['request_sha256']:
            raise ValueError('Request provenance checksum mismatch')
        return rebuilt
