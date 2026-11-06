#!/usr/bin/env python3
"""Fail-closed selection between Kie Claude and an EXPLICITLY configured independent backup.

Do not switch reviewers to override a REJECT or NEEDS_INFORMATION. Only the
provider's transient HTTP 500/502/503/504 can trigger backup. Each model must
review the ENTIRE diff on its own, never a mixture of model approvals.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from independent_review import parse_verdict, review_chunks, split_review_diff

TRANSIENT = re.compile(r"^provider HTTP status (?:500|502|503|504)$")


def verified_complete(report, provider, chunks, diff_sha):
    if not isinstance(report, dict) or report.get("provider") != provider:
        return False
    if report.get("status") != "PASS" or report.get("diff_sha256") != diff_sha:
        return False
    if not isinstance(report.get("review_contract_sha256"), str) or len(report["review_contract_sha256"]) != 64:
        return False
    reviews = report.get("reviews")
    if (type(report.get("segments_expected")) is not int
            or type(report.get("segments_reviewed")) is not int
            or report["segments_expected"] != len(chunks)
            or report["segments_reviewed"] != len(chunks)
            or not isinstance(reviews, list) or len(reviews) != len(chunks)):
        return False
    for index, (segment, record) in enumerate(zip(chunks, reviews), 1):
        if not isinstance(record, dict) or type(record.get("index")) is not int or record["index"] != index:
            return False
        if record.get("sha256") != hashlib.sha256(segment.encode("utf-8")).hexdigest():
            return False
        try:
            decision = parse_verdict(json.dumps(record.get("verdict")))
        except (ValueError, TypeError, json.JSONDecodeError):
            return False
        if decision["verdict"] != "APPROVE":
            return False
    return True


def permitted_backup(primary, diff_sha):
    if not isinstance(primary, dict):
        return False
    if primary.get("provider") not in ("kie", "anthropic") or primary.get("diff_sha256") != diff_sha:
        return False
    if primary.get("status") != "BLOCKED" or not TRANSIENT.fullmatch(str(primary.get("reason"))):
        return False
    # A substantive objection from any completed chunk blocks substitution.
    reviews = primary.get("reviews")
    if not isinstance(reviews, list):
        return False
    for record in reviews:
        if not isinstance(record, dict):
            return False
        try:
            v = parse_verdict(json.dumps(record.get("verdict")))
        except (ValueError, TypeError, json.JSONDecodeError):
            return False
        if v["verdict"] != "APPROVE":
            return False
    return True


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run_gate(diff_path, primary_path, backup_path, output_path, env=None, runner=None):
    env = os.environ if env is None else env
    runner = subprocess.run if runner is None else runner
    diff = diff_path.read_text(encoding="utf-8")
    primary_provider = env.get("PRIMARY_REVIEW_PROVIDER", "kie")
    chunks = review_chunks(primary_provider, diff)
    backup_chunks = review_chunks("compatible", diff)
    digest = hashlib.sha256(diff.encode("utf-8")).hexdigest()
    primary = load(primary_path)
    result = {"status": "BLOCKED", "diff_sha256": digest,
              "reason": "primary independent review incomplete",
              "reviewer": None, "segments_expected": len(chunks)}
    if primary_provider not in ("kie", "anthropic"):
        result["reason"] = "invalid primary reviewer configuration"
    elif verified_complete(primary, primary_provider, chunks, digest):
        result.update(status="PASS", reason=None, reviewer=primary_provider)
    elif permitted_backup(primary, digest) and primary.get("provider") == primary_provider:
        if all(env.get(name) for name in
               ("ALT_REVIEW_API_KEY", "ALT_REVIEW_API_URL", "ALT_REVIEW_MODEL")):
            command = [sys.executable, str(Path(__file__).with_name("independent_review.py")),
                       "--provider", "compatible", "--diff", str(diff_path),
                       "--output", str(backup_path)]
            try:
                runner(command, env=env, check=False, timeout=600)
                alternate = load(backup_path)
                if verified_complete(alternate, "compatible", backup_chunks, digest):
                    result.update(status="PASS", reason=None, reviewer="compatible")
                else:
                    result["reason"] = "backup independent review incomplete"
            except (OSError, subprocess.TimeoutExpired):
                result["reason"] = "backup transport failed"
        else:
            result["reason"] = "backup provider not configured"
    elif isinstance(primary, dict):
        result["reason"] = "primary review blocked; fallback not permitted"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(f"Independent review gate: {result['status']} ({result['reason'] or result['reviewer']})")
    return 0 if result["status"] == "PASS" else 2


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--diff", type=Path, required=True)
    p.add_argument("--primary", type=Path, required=True)
    p.add_argument("--backup", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    return run_gate(a.diff, a.primary, a.backup, a.output)


if __name__ == "__main__":
    raise SystemExit(main())
