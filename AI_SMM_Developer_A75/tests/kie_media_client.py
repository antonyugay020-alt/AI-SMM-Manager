"""Kie.ai createTask transport from confirmed OpenAPI (no speculative routes).

Native upload/status contracts: docs.kie.ai/file-upload-api/upload-file-stream
and docs.kie.ai/market/common/get-task-detail (verified 2026-11-07).
No automatic paid retries, redirects or provider response-body logging.
"""
from __future__ import annotations
import json
import os
import mimetypes
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlencode
import urllib.error
import urllib.request
from kie_media_contracts import CREATE_TASK_PATH, extract_task_id, TASK_ID, _url
from media_upload_transfer import checkpoint

API_ORIGIN = "https://api.kie.ai"
SUPPORTED_MODELS = frozenset((
    "nano-banana-pro", "google/nano-banana-edit",
    "kling-3.0-omni/transformation",
    "kling-3.0-omni/reference-to-video"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Kie authenticated redirect refused")


class KieMediaClient:
    def __init__(self, *, api_key=None, enabled=False, opener=None):
        self._key = api_key if api_key is not None else os.environ.get("KIE_AI_API_KEY", "")
        self.enabled = enabled
        self._opener = opener if opener is not None else urllib.request.build_opener(_NoRedirect()).open

    def _credential(self):
        if (not isinstance(self._key, str) or not self._key.strip()
                or "\r" in self._key or "\n" in self._key):
            raise RuntimeError("Kie API credential unavailable")
        return "Bearer "+self._key

    def _read_json(self, request, *, timeout=50):
        try:
            with self._opener(request, timeout=timeout) as response:
                if getattr(response,"status",200)!=200:
                    raise RuntimeError("Kie media non-success HTTP status")
                body=response.read(120_001)
            if len(body)>120_000:
                raise RuntimeError("Kie media response exceeds limit")
            result=json.loads(body)
            if not isinstance(result,dict) or type(result.get("code")) is not int or result["code"]!=200:
                raise RuntimeError("Kie media response not successful")
            return result
        except urllib.error.HTTPError as exc:
            raise RuntimeError("Kie media HTTP "+str(exc.code)) from None
        except (OSError, ValueError):
            raise RuntimeError("Kie media transport or response unavailable") from None

    def query_task(self, task_id):
        """One read-only status query; retry/backoff belongs to the scheduler."""
        if not isinstance(task_id,str) or not TASK_ID.fullmatch(task_id):
            raise ValueError("invalid Kie task ID")
        req=urllib.request.Request(
            API_ORIGIN+"/api/v1/jobs/recordInfo?"+urlencode({"taskId":task_id}),
            headers={"Authorization":self._credential()},method="GET")
        result=self._read_json(req)
        data=result.get("data")
        if (not isinstance(data,dict) or data.get("taskId")!=task_id
                or data.get("model") not in SUPPORTED_MODELS
                or data.get("state") not in
                    {"waiting","queuing","generating","success","fail"}):
            raise ValueError("Kie task status identity or state invalid")
        return result

    def upload_file(self, filename, *, explicit_upload_consent=False, check=None):
        """Stream a validated project source with distinct upload consent.

        Gateway checks ownership and byte validity before invoking this adapter.
        The stream is bounded, never buffered wholesale, and never retried.
        """
        if explicit_upload_consent is not True:
            raise RuntimeError("external source upload requires explicit user consent")
        checkpoint(check)
        authorization=self._credential()
        path=Path(filename)
        from project_file_boundary import reject_path_link
        reject_path_link(path)
        if not path.is_file() or not 0<path.stat().st_size<=500*1024*1024:
            raise ValueError("Kie upload source size invalid")
        if path.suffix.lower() not in {".png",".jpg",".jpeg",".webp",".mp4",".mov"}:
            raise ValueError("Kie upload source format invalid")
        boundary="kie-"+uuid4().hex
        name=uuid4().hex+path.suffix.lower()
        mime=mimetypes.guess_type(name)[0] or "application/octet-stream"
        prefix=(f'--{boundary}\r\nContent-Disposition: form-data; name="uploadPath"\r\n\r\n'
                f'media/user-uploads\r\n--{boundary}\r\n'
                f'Content-Disposition: form-data; name="fileName"\r\n\r\n{name}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                f'Content-Type: {mime}\r\n\r\n').encode("ascii")
        suffix=f"\r\n--{boundary}--\r\n".encode("ascii")
        with path.open("rb") as source:
            size=os.fstat(source.fileno()).st_size
            def chunks():
                checkpoint(check)
                yield prefix
                remaining=size
                while remaining:
                    checkpoint(check)
                    block=source.read(min(1024*1024,remaining))
                    checkpoint(check)
                    if not block:
                        raise RuntimeError("Kie upload source changed")
                    remaining-=len(block)
                    yield block
                if source.read(1):
                    raise RuntimeError("Kie upload source changed")
                checkpoint(check)
                yield suffix
            req=urllib.request.Request(
                "https://kieai.redpandaai.co/api/file-stream-upload",data=chunks(),
                headers={"Authorization":authorization,
                    "Content-Type":"multipart/form-data; boundary="+boundary,
                    "Content-Length":str(len(prefix)+size+len(suffix))},method="POST")
            result=self._read_json(req,timeout=90)
            checkpoint(check)
        data=result.get("data")
        if result.get("success") is not True or not isinstance(data,dict):
            raise ValueError("Kie upload not confirmed")
        if type(data.get("fileSize")) is not int or data["fileSize"]!=size:
            raise ValueError("Kie upload byte count mismatch")
        checkpoint(check)
        return _url(data.get("downloadUrl"))

    def create_task(self, payload, *, payment_reservation=None,
                    operation=None, cost_gate=None):
        """A direct transport call cannot bypass the durable paid budget fence.

        The orchestration layer supplies the reservation and exact job-scoped
        operation; a caller that only toggles enabled=True is never authorized.
        """
        from durable_cost_gate import DurableCostGate
        if (not isinstance(cost_gate, DurableCostGate)
                or not isinstance(operation, str)
                or not operation.startswith("media:")
                or len(operation) <= len("media:")):
            raise RuntimeError("durable job-scoped payment authorization required")
        cost_gate.assert_reservation(payment_reservation, "kie.ai", operation)

        if self.enabled is not True:
            raise RuntimeError("Kie paid media tasks disabled by default")
        authorization=self._credential()
        if not isinstance(payload, dict) or payload.get("model") not in SUPPORTED_MODELS:
            raise ValueError("Kie model is not allowlisted")
        if not isinstance(payload.get("input"), dict):
            raise ValueError("Kie task input missing")
        request = urllib.request.Request(
            API_ORIGIN+CREATE_TASK_PATH,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": authorization,
                     "Content-Type": "application/json"},
            method="POST")
        # Final check immediately before the potentially billable network call.
        cost_gate.claim_dispatch(payment_reservation, "kie.ai", operation)
        result=self._read_json(request)
        extract_task_id(result)
        return result
