#!/usr/bin/env python3
"""Bounded Gemini direct connection check, preserving sanitized evidence."""
from __future__ import annotations

import json
import os
from pathlib import Path
import urllib.error

from independent_review import request_review

EVIDENCE = Path(__file__).resolve().parents[1] / "ci_evidence" / "gemini_probe.json"

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
