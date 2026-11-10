"""Bounded cooperative HTTP response body reader for text/media transports.

Stdlib-only. The caller owns network connection and supplies the monotonic
absolute deadline established before the request. A socket read may still
block until its socket timeout; this reader bounds memory, the cumulative
read loop and cancellation checkpoints, not kernel-level socket operations.
"""
from __future__ import annotations
import io
import math
import time

MAX_HTTP_BODY = 32 * 1024 * 1024


def read_bounded_body(response, *, max_bytes, deadline, check=None):
    """Read no more than max_bytes, reject overflow, honor deadline/check."""
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_HTTP_BODY:
        raise ValueError('Invalid HTTP body byte budget')
    if (type(deadline) not in (int, float) or not math.isfinite(deadline)):
        raise ValueError('Invalid HTTP response deadline')
    if check is not None and not callable(check):
        raise TypeError('HTTP cancellation check must be callable')
    reader = getattr(response, 'read1', None)
    if (not callable(reader) or (isinstance(response, io.BytesIO)
            and type(response).read1 is io.BytesIO.read1
            and type(response).read is not io.BytesIO.read)):
        reader = response.read
    body = bytearray()
    while True:
        if check is not None:
            check()
        if time.monotonic() >= deadline:
            raise TimeoutError('HTTP response body deadline exceeded')
        chunk = reader(min(64 * 1024, max_bytes + 1 - len(body)))
        if check is not None:
            check()
        if time.monotonic() >= deadline:
            raise TimeoutError('HTTP response body deadline exceeded')
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise ValueError('HTTP response returned non-byte content')
        if len(chunk) > max_bytes - len(body):
            raise ValueError('HTTP response body exceeds byte budget')
        if not chunk:
            return bytes(body)
        body.extend(chunk)
