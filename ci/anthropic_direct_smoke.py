#!/usr/bin/env python3
"""One cheap direct Anthropic API verification, never using repository source."""
from __future__ import annotations

import json
import os
from pathlib import Path

from independent_review import request_review

OUTPUT = Path(__file__).resolve().parents[1] / "ci_evidence" / "anthropic_direct_status.json"


def main():
    outcome = {"provider": "anthropic-direct", "status": "BLOCKED",
               "scope": "synthetic API connectivity; NOT an independent code audit"}
    try:
        model = os.environ.get("ANTHROPIC_MODEL", "").strip()
        if not os.environ.get("ANTHROPIC_API_KEY") or not model:
            outcome["reason"] = "ANTHROPIC_API_KEY or ANTHROPIC_MODEL not configured"
        else:
            result = request_review(
                "anthropic",
                "Synthetic connection test, no code supplied. Return JSON ONLY: "
                '{"verdict":"NEEDS_INFORMATION","critical_findings":[],'
                '"confidence":"low","notes":"No code provided"}'
            )
            if result["verdict"] == "NEEDS_INFORMATION" and not result["critical_findings"]:
                outcome["status"] = "PASS"
                outcome["reason"] = None
            else:
                outcome["reason"] = "synthetic verdict not as requested"
    except (RuntimeError, OSError, ValueError) as exc:
        msg = str(exc)
        if msg.startswith("provider HTTP status ") and msg[21:].isdigit():
            outcome["reason"] = msg
        else:
            outcome["reason"] = "unexpected provider response or transport failure"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
    print("Direct Anthropic API:", outcome["status"],
          "(" + str(outcome.get("reason")) + ")")
    return 0 if outcome["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
