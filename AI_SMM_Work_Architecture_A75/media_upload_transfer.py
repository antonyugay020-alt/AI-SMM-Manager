"""Cooperative source-upload boundary; never retries a provider operation.

A legacy one-argument adapter is checked before/after the call. Adapters
supporting ``check`` also receive the callback for each streaming chunk.
"""
from __future__ import annotations
import inspect
from pathlib import Path


def checkpoint(check):
    if check is not None:
        if not callable(check):
            raise TypeError('Media upload cancellation check must be callable')
        check()


def upload_with_check(upload, path, *, check=None):
    if not callable(upload):
        raise TypeError('Media upload adapter must be callable')
    checkpoint(check)
    try:
        params=inspect.signature(upload).parameters.values()
        supports_check=any(
            (p.name=='check' and p.kind in (
                inspect.Parameter.KEYWORD_ONLY,inspect.Parameter.POSITIONAL_OR_KEYWORD))
            or p.kind==inspect.Parameter.VAR_KEYWORD for p in params)
    except (TypeError,ValueError):
        supports_check=False
    result=upload(path,check=check) if supports_check else upload(path)
    checkpoint(check)
    return result


def copy_snapshot_with_check(source, destination, *, check=None, chunk_size=1024*1024):
    """Bounded-memory source snapshot; interruption leaves only temp-dir bytes."""
    if type(chunk_size) is not int or not 1<=chunk_size<=1024*1024:
        raise ValueError('Invalid snapshot copy chunk size')
    checkpoint(check)
    with Path(source).open('rb') as reader, Path(destination).open('xb') as writer:
        while True:
            checkpoint(check)
            block=reader.read(chunk_size)
            checkpoint(check)
            if not block:
                break
            writer.write(block)
    checkpoint(check)
