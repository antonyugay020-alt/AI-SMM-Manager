#!/usr/bin/env python3
"""Bounded Gemini direct connection check, preserving sanitized evidence."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import urllib.error
import urllib.request

from independent_review import request_review

EVIDENCE = Path(__file__).resolve().parents[1] / "ci_evidence" / "gemini_probe.json"


def model_inventory():
    """Enumerate eligible model IDs without generating tokens or logging secrets."""
    request = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models?pageSize=100",
        headers={"x-goog-api-key": os.environ["ALT_REVIEW_API_KEY"]},
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        data = json.loads(response.read(300_000).decode("utf-8"))
    models = data.get("models", []) if isinstance(data, dict) else []
    # Report only known Gemini naming family; never echo provider diagnostics.
    return sorted(
        entry["name"].removeprefix("models/")
        for entry in models
        if isinstance(entry, dict)
        and isinstance(entry.get("name"), str)
        and entry["name"].startswith("models/gemini-")
        and "generateContent" in entry.get("supportedGenerationMethods", [])
        and len(entry["name"]) < 100
    )



def sanitized_google_message(value):
    """Render Google error text only after dropping secrets and opaque identifiers.

    This is diagnostic evidence, not a raw HTTP response. Never persist
    Authorization headers, credentials, URLs with queries, or token-like data.
    """
    if not isinstance(value, str):
        return None
    value = value[:2000]
    for name in ("ALT_REVIEW_API_KEY", "KIE_AI_API_KEY", "OPENAI_API_KEY"):
        secret = os.environ.get(name, "")
        if secret:
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"(?i)\\bBearer\\s+[^\\s,;]+", "Bearer [REDACTED]", value)
    value = re.sub(r"(?i)\\bAIza[A-Za-z0-9_\\-]{16,}", "[REDACTED]", value)
    value = re.sub(r"(?i)\\b(?:api[_ -]?key|access[_ -]?token|authorization)"
                   r"\\s*[:=]\\s*[^\\s,;]+", "[REDACTED]", value)
    value = re.sub(r"https?://[^\\s<>]+", "[URL REDACTED]", value)
    value = re.sub(r"[A-Za-z0-9_+\\-]{36,}", "[OPAQUE REDACTED]", value)
    value = re.sub(r"[\\x00-\\x1f\\x7f]+", " ", value).strip()
    return value[:500] if value else None


def safe_provider_error(error):
    """Record structured Google status only; never echo messages or credentials."""
    result = {"http_status": int(error.code)}
    try:
        data = json.loads(error.read(8192).decode("utf-8"))
        obj = data.get("error", {}) if isinstance(data, dict) else {}
        if isinstance(obj, dict):
            code = obj.get("code")
            if type(code) is int and 100 <= code <= 599:
                result["google_code"] = code
            status = obj.get("status")
            allowed_statuses = {
                "NOT_FOUND", "PERMISSION_DENIED", "FAILED_PRECONDITION",
                "RESOURCE_EXHAUSTED", "UNAUTHENTICATED", "INVALID_ARGUMENT",
                "UNAVAILABLE", "INTERNAL", "UNKNOWN",
            }
            if isinstance(status, str) and status in allowed_statuses:
                result["google_status"] = status
            explanation = sanitized_google_message(obj.get("message"))
            if explanation:
                result["google_message_redacted"] = explanation
            kinds = set()
            reasons = set()
            for entry in obj.get("details", [])[:8] if isinstance(obj.get("details"), list) else []:
                if not isinstance(entry, dict):
                    continue
                kind = str(entry.get("@type", "")).rsplit("/", 1)[-1]
                if kind in {"ErrorInfo", "QuotaFailure", "PreconditionFailure"}:
                    kinds.add(kind)
                reason = entry.get("reason")
                if isinstance(reason, str) and reason in {
                    "API_KEY_INVALID", "API_KEY_SERVICE_BLOCKED",
                    "SERVICE_DISABLED", "BILLING_DISABLED", "CONSUMER_INVALID",
                    "RATE_LIMIT_EXCEEDED", "QUOTA_EXCEEDED", "LOCATION_NOT_SUPPORTED",
                }:
                    reasons.add(reason)
            if kinds:
                result["detail_kinds"] = sorted(kinds)
            if reasons:
                result["error_reasons"] = sorted(reasons)
    except (OSError, ValueError, UnicodeError):
        result["structured_error"] = "unavailable"
    return result


def main():
    evidence = {"provider": "gemini-direct", "status": "BLOCKED",
                "reason": "not run", "scope": "one synthetic API probe; NOT a code review"}
    try:
        if not all(os.environ.get(name) for name in
                   ("ALT_REVIEW_API_KEY", "ALT_REVIEW_API_URL", "ALT_REVIEW_MODEL")):
            evidence["reason"] = "missing key, URL, or model setting"
        else:
            verdict = request_review(
                "compatible",
                "Synthetic connection test only. No repository or source code supplied. "
                "Return ONLY JSON matching the specified verdict schema, "
                "with verdict NEEDS_INFORMATION, critical_findings [], "
                "confidence low, notes explaining missing code."
            )
            if verdict["verdict"] == "NEEDS_INFORMATION" and verdict["critical_findings"] == []:
                evidence.update(status="PASS", reason=None)
            else:
                evidence["reason"] = "synthetic verdict failed closed"
    except (RuntimeError, ValueError, urllib.error.URLError) as exc:
        # Do not print response bodies, URLs, credentials or untrusted model text.
        message = str(exc)
        if message.startswith("provider HTTP status ") and message[len("provider HTTP status "):].isdigit():
            evidence["reason"] = message
            if message == "provider HTTP status 404":
                try:
                    candidates = model_inventory()
                    requested = os.environ.get("ALT_REVIEW_MODEL", "")
                    evidence["requested_model_listed"] = requested in candidates
                    evidence["available_pro_models"] = [
                        model for model in candidates if "pro" in model
                    ][:20]
                    evidence["model_inventory_status"] = "PASS"
                    configured = os.environ.get("ALT_REVIEW_API_URL", "").strip()
                    canonical = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
                    evidence["compatible_endpoint_exact"] = configured == canonical
                    # A native API fallback diagnostic differentiates a malformed
                    # OpenAI-compatible endpoint from unavailable credentials.
                    if configured == canonical and requested in candidates:
                        native = urllib.request.Request(
                            "https://generativelanguage.googleapis.com/v1beta/models/"
                            + requested + ":generateContent",
                            data=json.dumps({"contents": [{"parts": [{
                                "text": "Reply with only the word OK."
                            }]}], "generationConfig": {"maxOutputTokens": 32}}
                            ).encode("utf-8"),
                            headers={"x-goog-api-key": os.environ["ALT_REVIEW_API_KEY"],
                                     "Content-Type": "application/json"},
                            method="POST",
                        )
                        try:
                            with urllib.request.urlopen(native, timeout=40) as response:
                                response.read(50_000)
                            evidence["native_endpoint_status"] = "PASS"
                        except urllib.error.HTTPError as native_error:
                            evidence["native_endpoint_status"] = "HTTP " + str(native_error.code)
                            evidence["native_error"] = safe_provider_error(native_error)
                        except (OSError, ValueError):
                            evidence["native_endpoint_status"] = "BLOCKED"
                    # One low-token Flash probe isolates Pro tier restrictions.
                    flash = "gemini-2.5-flash"
                    if evidence.get("native_endpoint_status") == "HTTP 404" and flash in candidates:
                        probe = urllib.request.Request(
                            "https://generativelanguage.googleapis.com/v1beta/models/"
                            + flash + ":generateContent",
                            data=json.dumps({"contents": [{"parts": [{
                                "text": "Reply OK."
                            }]}], "generationConfig": {"maxOutputTokens": 16}}
                            ).encode("utf-8"),
                            headers={"x-goog-api-key": os.environ["ALT_REVIEW_API_KEY"],
                                     "Content-Type": "application/json"},
                            method="POST",
                        )
                        try:
                            with urllib.request.urlopen(probe, timeout=40) as response:
                                response.read(20_000)
                            evidence["flash_endpoint_status"] = "PASS"
                        except urllib.error.HTTPError as flash_error:
                            evidence["flash_endpoint_status"] = "HTTP " + str(flash_error.code)
                            evidence["flash_error"] = safe_provider_error(flash_error)
                        except (OSError, ValueError):
                            evidence["flash_endpoint_status"] = "BLOCKED"
                except urllib.error.HTTPError as inventory_error:
                    evidence["model_inventory_status"] = "HTTP " + str(inventory_error.code)
                except (OSError, ValueError, KeyError):
                    evidence["model_inventory_status"] = "BLOCKED"
        else:
            evidence["reason"] = "provider replied, but transport or verdict was invalid"
    except Exception:
        evidence["reason"] = "unexpected probe failure"
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print("Gemini direct API probe:", evidence["status"], "(" + str(evidence["reason"]) + ")")
    return 0 if evidence["status"] == "PASS" else 2

if __name__ == "__main__":
    raise SystemExit(main())
