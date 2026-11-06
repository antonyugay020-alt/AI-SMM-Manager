#!/usr/bin/env python3
"""Bounded Anthropic inventory and one synthetic request; never logs credentials."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from independent_review import request_review

OUTPUT = Path(__file__).resolve().parents[1] / "ci_evidence" / "anthropic_direct_status.json"
MODEL_ID = re.compile(r"^claude-(?:sonnet|opus|haiku)-[a-z0-9-]{1,60}$")


def models_for_key(key):
    req = urllib.request.Request("https://api.anthropic.com/v1/models?limit=100",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = json.loads(response.read(200_000).decode("utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("invalid Anthropic model inventory")
    return [item["id"] for item in payload["data"]
            if isinstance(item, dict) and isinstance(item.get("id"), str)
            and MODEL_ID.fullmatch(item["id"])][:100]


def choose_model(models, configured):
    """Only use model names actually returned by Anthropic for this key."""
    if configured in models:
        return configured
    sonnet = [m for m in models if m.startswith("claude-sonnet-")]
    if not sonnet:
        return None
    # Prefer the service's ordering; direct API list normally puts newest first.
    return sonnet[0]


def main():
    outcome = {"provider": "anthropic-direct", "status": "BLOCKED",
               "scope": "model inventory + one synthetic API request; NOT a code audit"}
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    configured = os.environ.get("ANTHROPIC_MODEL", "").strip()
    try:
        if not key:
            outcome["reason"] = "ANTHROPIC_API_KEY not configured"
        else:
            models = models_for_key(key)
            outcome["inventory_status"] = "PASS"
            outcome["available_models"] = models
            outcome["configured_model_listed"] = configured in models
            selected = choose_model(models, configured)
            if not selected:
                outcome["reason"] = "no Claude Sonnet model in account inventory"
            else:
                outcome["tested_model"] = selected
                os.environ["ANTHROPIC_MODEL"] = selected
                result = request_review(
                    "anthropic",
                    "Synthetic connectivity test, no code supplied. Respond ONLY with JSON: "
                    '{"verdict":"NEEDS_INFORMATION","critical_findings":[],'
                    '"confidence":"low","notes":"No code provided"}'
                )
                if result["verdict"] == "NEEDS_INFORMATION" and not result["critical_findings"]:
                    outcome.update(status="PASS", reason=None)
                else:
                    outcome["reason"] = "synthetic verdict failed closed"
    except urllib.error.HTTPError as exc:
        outcome["reason"] = "provider HTTP status " + str(exc.code)
    except (RuntimeError, OSError, ValueError, UnicodeError) as exc:
        message = str(exc)
        if message.startswith("provider HTTP status "):
            outcome["reason"] = message
        else:
            outcome["reason"] = "provider transport or response invalid"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(outcome, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Direct Anthropic:", outcome["status"],
          "(" + str(outcome.get("reason")) + ")",
          "model:", outcome.get("tested_model", "unavailable"))
    return 0 if outcome["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
