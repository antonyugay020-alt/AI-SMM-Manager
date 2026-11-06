#!/usr/bin/env python3
"""Bounded Gemini direct connection check, preserving sanitized evidence."""
from __future__ import annotations

import json
import os
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
