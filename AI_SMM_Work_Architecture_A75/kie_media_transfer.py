"""One cancellation boundary for provider result adapters.

The bundled downloader accepts `check` during every bounded network read.
Legacy injected adapters may accept only (url, target); keep those compatible,
but always check the caller's durable authority before and after their call.
Never retry a transfer or swallow an exception raised by the adapter itself.
"""
from __future__ import annotations
import inspect


def fetch_result(fetch, url, target, check):
    if not callable(fetch):
        raise TypeError('Kie result fetch adapter must be callable')
    if check is not None and not callable(check):
        raise TypeError('Kie result cancellation check must be callable')
    if check is not None:
        check()
    try:
        params=inspect.signature(fetch).parameters.values()
        supports_check=any(p.name=='check' and p.kind in (
            inspect.Parameter.KEYWORD_ONLY,inspect.Parameter.POSITIONAL_OR_KEYWORD)
            or p.kind==inspect.Parameter.VAR_KEYWORD for p in params)
    except (TypeError,ValueError):
        supports_check=False
    if supports_check:
        result=fetch(url,target,check=check)
    else:
        result=fetch(url,target)
    if check is not None:
        check()
    return result
