"""Audit transport invariants: a review may never silently omit diff bytes.

These are pure contract tests; they never send source material to a provider
and do not assert that an external model actually audited the application.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


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

    def test_missing_confidence_cannot_pass_as_approval(self):
        with self.assertRaisesRegex(ValueError, "confidence"):
            REVIEW.parse_verdict(
                '{"verdict":"APPROVE","critical_findings":[],"notes":"ok"}'
            )

    def test_unknown_review_status_is_rejected(self):
        with self.assertRaises(ValueError):
            REVIEW.parse_verdict(
                '{"verdict":"PARTIAL","critical_findings":[],"confidence":"high"}'
            )


if __name__ == "__main__":
    unittest.main()
