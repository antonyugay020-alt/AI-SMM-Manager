#!/usr/bin/env python3
"""Single small Kie model acceptance probe. Never audits code or logs secrets."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

OUTPUT = Path(__file__).resolve().parents[1] / "ci_evidence" / "kie_alternate_model.json"

def check():
    model = os.environ.get("KIE_PROBE_MODEL", "").strip()
    key = os.environ.get("KIE_AI_API_KEY", "")
    result = {"provider": "kie", "model": model if re.fullmatch(r"claude-[a-z0-9-]{3,64}", model) else "invalid",
              "status": "BLOCKED", "scope": "synthetic single-request model transport check, NOT a code audit"}
    if not key or result["model"] == "invalid":
        result["reason"] = "credential missing or model identifier invalid"
    else:
        payload = {
            "model": model, "stream": False, "max_tokens": 128,
            "messages": [{"role": "user", "content":
              "Connection probe, not a code audit. Reply with one brief word OK."}]
        }
        req = urllib.request.Request(
            "https://api.kie.ai/claude/v1/messages",
            data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=50) as response:
                data = json.loads(response.read(80_000).decode("utf-8"))
            blocks = data.get("content") if isinstance(data, dict) else None
            if isinstance(blocks, list) and any(
                isinstance(b, dict) and b.get("type") == "text" and
                isinstance(b.get("text"), str) and b["text"].strip() for b in blocks
            ):
                result["status"] = "PASS"
                result["reason"] = None
            else:
                result["reason"] = "invalid or empty Claude response envelope"
        except urllib.error.HTTPError as error:
            result["reason"] = "provider HTTP status " + str(error.code)
        except (OSError, ValueError, UnicodeError):
            result["reason"] = "transport or response decoding failure"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Alternate Kie model transport: " + result["status"] +
          (" (" + result["reason"] + ")" if result.get("reason") else ""))
    return 0 if result["status"] == "PASS" else 2

if __name__ == "__main__":
    raise SystemExit(check())
