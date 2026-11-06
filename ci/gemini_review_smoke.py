#!/usr/bin/env python3
"""One short real Gemini API connectivity/JSON-contract probe. Not a code audit."""
from __future__ import annotations
import os
from independent_review import request_review

def main():
    if not all(os.environ.get(key) for key in
               ("ALT_REVIEW_API_KEY", "ALT_REVIEW_API_URL", "ALT_REVIEW_MODEL")):
        print("Gemini direct API probe: BLOCKED (missing secret or model configuration)")
        return 2
    try:
        verdict = request_review("compatible",
            "Synthetic transport smoke, NOT a repository review. "
            "No actual diff was provided; return NEEDS_INFORMATION, "
            "critical_findings empty, confidence low, and brief notes. "
            "Return only valid JSON in the required verdict schema.")
        if verdict["verdict"] != "NEEDS_INFORMATION" or verdict["critical_findings"]:
            raise ValueError("synthetic smoke did not fail closed")
    except Exception as exc:
        # Do not echo the provider response (may contain user code or credentials).
        print("Gemini direct API probe: BLOCKED (" +
              (str(exc) if isinstance(exc, (RuntimeError, ValueError)) else type(exc).__name__)
              + ")")
        return 2
    print("Gemini direct API probe: PASS (transport and JSON verdict; NOT an independent audit)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
