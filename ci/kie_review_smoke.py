#!/usr/bin/env python3
"""One tiny, bounded, paid Kie.ai reviewer request before reviewing a large PR.

This verifies transport, authentication and a parsable verdict only. It NEVER
certifies the repository's code or substitutes for full independent review.
"""
from __future__ import annotations
import sys
from independent_review import request_review


def main():
    # A small self-contained diff keeps the probe economical and distinguishes
    # transport/provider errors from large PR-context limitations.
    minimal_diff = (
        "diff --git a/example.py b/example.py\n"
        "--- a/example.py\n"
        "+++ b/example.py\n"
        "@@ -1 +1 @@\n"
        "-ANSWER = 1\n"
        "+ANSWER = 2\n"
    )
    # Exercise a near-production context size before allowing the whole PR.
    # This deliberately cheap synthetic input is NOT evidence of code quality.
    minimal_diff += "".join(f"+VALUE_{i:04d} = {i:05d}\n" for i in range(800))
    try:
        review = request_review("kie", minimal_diff)
    except (ValueError, RuntimeError, OSError) as exc:
        print("Kie Claude transport smoke: BLOCKED (" + str(exc) + ")")
        return 2
    if review["verdict"] not in ("APPROVE", "REJECT", "NEEDS_INFORMATION"):
        print("Kie Claude transport smoke: BLOCKED (invalid verdict)")
        return 2
    print("Kie Claude transport smoke: PASS (valid API response and verdict; NOT a code audit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
