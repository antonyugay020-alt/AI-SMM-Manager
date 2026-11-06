"""Kie.ai createTask transport from confirmed OpenAPI (no speculative routes).

File upload and Get Task Details use injected adapters until their own precise
schemas are supplied. No automatic paid retries or response-body logging.
"""
from __future__ import annotations
import json
import os
import urllib.error
import urllib.request
from kie_media_contracts import CREATE_TASK_PATH, extract_task_id

API_ORIGIN = "https://api.kie.ai"
SUPPORTED_MODELS = frozenset((
    "nano-banana-pro", "google/nano-banana-edit",
    "kling-3.0-omni/transformation",
    "kling-3.0-omni/reference-to-video"))


class KieMediaClient:
    def __init__(self, *, api_key=None, enabled=False, opener=None):
        self._key = api_key if api_key is not None else os.environ.get("KIE_AI_API_KEY", "")
        self.enabled = enabled
        self._opener = opener if opener is not None else urllib.request.urlopen

    def create_task(self, payload):
        if not self.enabled:
            raise RuntimeError("Kie paid media tasks disabled by default")
        if not isinstance(self._key, str) or not self._key.strip():
            raise RuntimeError("Kie API credential unavailable")
        if not isinstance(payload, dict) or payload.get("model") not in SUPPORTED_MODELS:
            raise ValueError("Kie model is not allowlisted")
        if not isinstance(payload.get("input"), dict):
            raise ValueError("Kie task input missing")
        request = urllib.request.Request(
            API_ORIGIN+CREATE_TASK_PATH,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer "+self._key,
                     "Content-Type": "application/json"},
            method="POST")
        try:
            with self._opener(request, timeout=50) as response:
                body = response.read(120_000)
            result = json.loads(body)
            extract_task_id(result)
            return result
        except urllib.error.HTTPError as exc:
            # Never expose provider response bodies or URLs with secrets.
            raise RuntimeError("Kie media HTTP "+str(exc.code)) from None
        except urllib.error.URLError:
            raise RuntimeError("Kie media transport unavailable") from None
