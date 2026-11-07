"""Bounded, atomic download of completed Kie media results.

A result URL is untrusted even when a provider returned it. Reject HTTP,
redirects, credential-bearing URLs and private literal IP hosts. Download
into an exclusive temporary file, fsync and publish atomically. Full image
and video media validity is checked separately by kie_media_jobs.
"""
from __future__ import annotations
import os
from pathlib import Path
import re
from urllib import request, error
from uuid import uuid4

from kie_media_contracts import _url
from project_file_boundary import reject_path_link

MAX_IMAGE = 60 * 1024 * 1024
MAX_VIDEO = 500 * 1024 * 1024


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Kie result redirect refused")


def download_result(url, target, *, opener=None):
    """Persist one file; never overwrite and never log the signed URL.

    Network fetch uses HTTPS without redirects; caller verifies media before
    committing it into the durable project asset registry.
    """
    _url(url)
    if not url.startswith("https://"):
        raise ValueError("Kie result must be HTTPS")
    path=Path(target)
    reject_path_link(path)
    if not path.parent.is_dir() or path.exists():
        raise ValueError("result staging destination is unsafe or occupied")
    if path.suffix.lower() in (".png",".jpg",".jpeg"):
        max_bytes=MAX_IMAGE
    elif path.suffix.lower() in (".mp4",".mov"):
        max_bytes=MAX_VIDEO
    else:
        raise ValueError("unsupported result extension")
    http=opener if opener is not None else request.build_opener(_NoRedirect()).open
    req=request.Request(url,method="GET",headers={"Accept":"application/octet-stream"})
    temporary=path.parent/(".downloading-"+uuid4().hex+".tmp")
    total=0
    try:
        with http(req,timeout=90) as response:
            status=getattr(response,"status",200)
            if status!=200:
                raise ValueError("Kie result returned non-success HTTP status")
            length=response.headers.get("Content-Length") if response.headers else None
            if length is not None and (not length.isdigit() or int(length)>max_bytes):
                raise ValueError("Kie result content-length invalid")
            with temporary.open("xb") as stream:
                while True:
                    chunk=response.read(min(1024*1024,max_bytes+1-total))
                    if not chunk:
                        break
                    total+=len(chunk)
                    if total>max_bytes:
                        raise ValueError("Kie result exceeds download size limit")
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
        if total==0:
            raise ValueError("Kie result download empty")
        if length is not None and total!=int(length):
            raise ValueError("Kie result content-length mismatch")
        if path.exists() or path.is_symlink():
            raise ValueError("Kie result destination created concurrently")
        os.link(temporary,path)
        return path
    except (error.HTTPError,error.URLError) as exc:
        raise RuntimeError("Kie result network transfer failed") from None
    finally:
        temporary.unlink(missing_ok=True)
