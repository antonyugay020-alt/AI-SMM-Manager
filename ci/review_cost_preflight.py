#!/usr/bin/env python3
"""Conservative per-dispatch ceiling for a manually authorized Anthropic review.

This is a *per-run* bound, not a monthly ledger or a verified provider invoice.
It intentionally overestimates input tokens using UTF-8 byte length and adds
32 KiB of system/wrapper allowance per segment. Two possibly billable HTTP
attempts and 4096 output tokens per segment are included. Missing pricing
or any unreviewable diff blocks all network calls.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re

from independent_review import review_chunks

MAX_OUTPUT_TOKENS = 4096
MAX_HTTP_ATTEMPTS = 2
PER_SEGMENT_INPUT_OVERHEAD_BYTES = 32768
MILLION = Decimal(1000000)


def positive_decimal(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("missing or invalid verified price/limit") from None
    if not number.is_finite() or number <= 0:
        raise ValueError("verified price/limit must be finite and positive")
    return number


def estimate(diff, input_rate, output_rate):
    chunks = review_chunks("anthropic", diff)
    input_ceiling = sum(len(chunk.encode("utf-8")) for chunk in chunks)
    input_ceiling += len(chunks) * PER_SEGMENT_INPUT_OVERHEAD_BYTES
    output_ceiling = len(chunks) * MAX_OUTPUT_TOKENS
    amount = MAX_HTTP_ATTEMPTS * (
        Decimal(input_ceiling) * input_rate / MILLION
        + Decimal(output_ceiling) * output_rate / MILLION
    )
    return amount, len(chunks), input_ceiling, output_ceiling


def run_preflight(diff_path, output_path, *, limit, input_rate, output_rate, model):
    result = {"status": "BLOCKED", "reason": "unverified review price or input",
              "diff_sha256": None, "estimated_max_usd": None}
    try:
        maximum = positive_decimal(limit)
        price_in = positive_decimal(input_rate)
        price_out = positive_decimal(output_rate)
        if not isinstance(model, str) or not re.fullmatch(r"claude-[a-z0-9-]{3,64}", model):
            raise ValueError("invalid pinned reviewer model")
        diff = diff_path.read_text(encoding="utf-8")
        result["diff_sha256"] = hashlib.sha256(diff.encode("utf-8")).hexdigest()
        amount, count, input_tokens, output_tokens = estimate(diff, price_in, price_out)
        result.update(model=model, authorized_max_usd=str(maximum),
                      estimated_max_usd=str(amount), segments=count,
                      input_token_ceiling=input_tokens,
                      output_token_ceiling=output_tokens,
                      max_http_attempts=MAX_HTTP_ATTEMPTS)
        if amount > maximum:
            raise ValueError("conservative review ceiling exceeds explicit per-run limit")
        result.update(status="PASS", reason=None)
    except (OSError, UnicodeError, ValueError) as exc:
        result["reason"] = str(exc)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.with_suffix(output_path.suffix + ".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(output_path)
    print("Reviewer cost preflight:", result["status"], result["reason"] or "priced")
    return 0 if result["status"] == "PASS" else 2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--diff", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", required=True)
    parser.add_argument("--input-rate", required=True)
    parser.add_argument("--output-rate", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    return run_preflight(args.diff, args.output, limit=args.limit,
                         input_rate=args.input_rate, output_rate=args.output_rate,
                         model=args.model)


if __name__ == "__main__":
    raise SystemExit(main())
