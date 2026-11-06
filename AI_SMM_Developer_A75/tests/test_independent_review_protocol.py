"""Audit transport invariants: a review may never silently omit diff bytes.

These are pure contract tests; they never send source material to a provider
and do not assert that an external model actually audited the application.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "isolated_review_protocol", ROOT / "ci" / "independent_review.py"
)
REVIEW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REVIEW)


class IndependentReviewProtocolTests(unittest.TestCase):
    def test_all_diff_bytes_are_covered_without_truncation(self):
        data = (
            "diff --git a/a.py b/a.py\n" + "a=1\n" * 150 +
            "diff --git a/b.py b/b.py\n" + "b=2\n" * 150
        )
        parts = REVIEW.split_review_diff(data, max_chars=1100, max_parts=16)
        self.assertGreater(len(parts), 1)
        self.assertEqual("".join(parts), data)
        self.assertTrue(all(part for part in parts))

    def test_segment_limit_is_hard_failure(self):
        data = "diff --git a/a.py b/a.py\n" + "x\n" * 3000
        with self.assertRaisesRegex(ValueError, "too many reviewer segments"):
            REVIEW.split_review_diff(data, max_chars=1000, max_parts=2)

    def test_empty_and_binary_data_are_rejected(self):
        for data in ("", "   ", "diff --git a/x b/x\x00"):
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    REVIEW.split_review_diff(data)

    def test_approval_cannot_contain_critical_findings(self):
        with self.assertRaisesRegex(ValueError, "contradicts"):
            REVIEW.parse_verdict('{"verdict":"APPROVE",'
                '"critical_findings":[{"file":"foo.py","reason":"data loss"}],'
                '"confidence":"high"}')

    def test_review_budget_preserves_large_multi_file_diff(self):
        # Tests the same 20k/24-segment bounds used by paid audit workflow.
        files = [
            f"diff --git a/file_{index}.py b/file_{index}.py\n" +
            ("+line=1\n" * 250)
            for index in range(75)
        ]
        original = "".join(files)
        chunks = REVIEW.split_review_diff(
            original, max_chars=20_000, max_parts=24
        )
        self.assertTrue(all(len(chunk) <= 20_000 for chunk in chunks))
        self.assertEqual("".join(chunks), original)

    def test_unicode_fixtures_have_explicit_cross_platform_encoding(self):
        # Windows CP1252 must never affect JSON fixture loading at import time.
        for filename in (
            "test_r96_live_requirement_evidence.py",
            "test_r97_ai_content_authority.py",
        ):
            with self.subTest(filename=filename):
                source = Path(__file__).with_name(filename).read_text(encoding="utf-8")
                self.assertIn(".read_text(encoding='utf-8')", source)

    def test_missing_confidence_cannot_pass_as_approval(self):
        with self.assertRaisesRegex(ValueError, "confidence"):
            REVIEW.parse_verdict(
                '{"verdict":"APPROVE","critical_findings":[],"notes":"ok"}'
            )

    def test_critical_finding_requires_explanation(self):
        with self.assertRaisesRegex(ValueError, "malformed finding"):
            REVIEW.parse_verdict(
                '{"verdict":"REJECT","critical_findings":'
                '[{"file":"example.py","reason":"   "}],'
                '"confidence":"high","notes":"finding cannot be empty"}'
            )

    def test_unknown_review_status_is_rejected(self):
        with self.assertRaises(ValueError):
            REVIEW.parse_verdict(
                '{"verdict":"PARTIAL","critical_findings":[],"confidence":"high"}'
            )


    def test_independent_fallback_never_overrides_claude_objections(self):
        # A provider outage is not an independent approval; substantive findings
        # are never erased by a subsequent Gemini verdict.
        import hashlib
        import json
        import sys
        with mock.patch.dict(sys.modules, {"independent_review": REVIEW}):
            spec = importlib.util.spec_from_file_location(
                "isolated_review_gate", ROOT / "ci" / "review_provider_gate.py"
            )
            gate = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(gate)
        diff = "diff --git a/example.py b/example.py\\n+value=1\\n"
        digest = hashlib.sha256(diff.encode("utf-8")).hexdigest()
        segment_sha = hashlib.sha256(diff.encode("utf-8")).hexdigest()
        rejected = {
            "provider": "kie", "status": "BLOCKED", "diff_sha256": digest,
            "reason": "provider HTTP status 503",
            "reviews": [{"index": 1, "sha256": segment_sha,
                         "verdict": {"verdict": "REJECT", "confidence": "high",
                                     "critical_findings": [
                                         {"file": "example.py", "reason": "state corruption"}
                                     ]}}],
        }
        self.assertFalse(gate.permitted_backup(rejected, digest))
        rejected["reviews"][0]["verdict"] = {
            "verdict": "NEEDS_INFORMATION", "confidence": "low",
            "critical_findings": [],
        }
        self.assertFalse(gate.permitted_backup(rejected, digest))
        rejected["reviews"][0]["verdict"] = {
            "verdict": "APPROVE", "confidence": "high", "critical_findings": [],
        }
        self.assertTrue(gate.permitted_backup(rejected, digest))
        rejected["reason"] = "provider HTTP status 401"
        self.assertFalse(gate.permitted_backup(rejected, digest))

    def test_fallback_cannot_claim_complete_on_incomplete_coverage(self):
        import hashlib
        import json
        import sys
        with unittest.mock.patch.dict(sys.modules, {"independent_review": REVIEW}):
            spec = importlib.util.spec_from_file_location(
                "isolated_review_gate", ROOT / "ci" / "review_provider_gate.py"
            )
            gate = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(gate)
        diff = "diff --git a/example.py b/example.py\\n+value=1\\n"
        digest = hashlib.sha256(diff.encode("utf-8")).hexdigest()
        fake = {
            "provider": "compatible", "status": "PASS", "diff_sha256": digest,
            "review_contract_sha256": "a" * 64,
            "segments_expected": 1, "segments_reviewed": 0,
            "reviews": [],
        }
        self.assertFalse(gate.verified_complete(fake, "compatible", [diff], digest))
        fake["segments_reviewed"] = 1
        fake["reviews"] = [{
            "index": 1, "sha256": "incorrect",
            "verdict": {"verdict": "APPROVE", "confidence": "high",
                        "critical_findings": []}
        }]
        self.assertFalse(gate.verified_complete(fake, "compatible", [diff], digest))


if __name__ == "__main__":
    unittest.main()
