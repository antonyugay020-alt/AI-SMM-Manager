"""Offline regression: no implicit paid PR reviews or unpriced model calls."""
import json
from pathlib import Path
import tempfile
import unittest

from review_cost_preflight import estimate, positive_decimal, run_preflight

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "independent-review.yml"
DIFF = "diff --git a/app.py b/app.py\n+print('hello')\n"


class ReviewerSpendGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.diff = root / "change.diff"
        self.diff.write_text(DIFF, encoding="utf-8")
        self.receipt = root / "cost.json"

    def check(self, limit="1", input_rate="3", output_rate="15",
              model="claude-sonnet-5-5"):
        return run_preflight(self.diff, self.receipt, limit=limit,
                             input_rate=input_rate, output_rate=output_rate,
                             model=model)

    def test_explicit_priced_ceiling_passes_and_records_hash(self):
        self.assertEqual(self.check(), 0)
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["segments"], 1)
        self.assertEqual(receipt["max_http_attempts"], 2)
        self.assertEqual(receipt["output_token_ceiling"], 4096)
        self.assertEqual(len(receipt["diff_sha256"]), 64)

    def test_missing_invalid_or_unbounded_price_blocks(self):
        for value in ("", "0", "-1", "NaN", "Infinity", "not-a-price"):
            with self.subTest(value=value):
                self.assertEqual(self.check(input_rate=value), 2)
                self.assertEqual(json.loads(self.receipt.read_text())["status"], "BLOCKED")
        self.assertEqual(self.check(limit=""), 2)
        self.assertEqual(self.check(output_rate="0"), 2)

    def test_over_budget_blocks_before_any_paid_request(self):
        self.assertEqual(self.check(limit="0.01"), 2)
        self.assertIn("exceeds", json.loads(self.receipt.read_text())["reason"])

    def test_unicode_byte_ceiling_and_two_attempts(self):
        amount, count, input_ceiling, output_ceiling = estimate(
            DIFF + "+中文😊\n", positive_decimal("3"), positive_decimal("15"))
        self.assertEqual(count, 1)
        self.assertGreaterEqual(input_ceiling, len((DIFF + "+中文😊\n").encode("utf-8")))
        self.assertEqual(output_ceiling, 4096)
        self.assertGreater(amount, 0)

    def test_empty_diff_or_missing_input_fails_closed(self):
        self.diff.write_text("", encoding="utf-8")
        self.assertEqual(self.check(), 2)
        self.diff.unlink()
        self.assertEqual(self.check(), 2)

    def test_model_must_be_pinned_and_valid(self):
        self.assertEqual(self.check(model=""), 2)
        self.assertEqual(self.check(model="unverified"), 2)

    def test_review_workflow_is_manual_and_requires_owner_approval(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("pull_request_target:", workflow)
        self.assertNotIn("\n  pull_request:\n", workflow)
        self.assertIn("inputs.authorize_paid_review == true", workflow)
        self.assertIn("vars.AI_SMM_PAID_REVIEW_ENABLED == 'true'", workflow)
        self.assertIn("environment: ai-smm-paid-review", workflow)
        self.assertIn("inputs.expected_head_sha", workflow)
        # A manual authorization is one dispatch only, from trusted main.
        # GitHub's rerun button must not repeat a potentially paid call.
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertIn("github.run_attempt == 1", workflow)
        # A review can take minutes: a PR may move after diff export.
        # A green reviewer job must re-check the pinned identity at the end.
        self.assertIn("Confirm reviewed PR is still the authorized head", workflow)
        self.assertIn("PR changed during independent review", workflow)
        self.assertIn("PR closed during independent review", workflow)
        self.assertIn("PR base changed during independent review", workflow)
        self.assertIn("PR head repository changed", workflow)
        self.assertIn('"$actual" == "$EXPECTED_HEAD_SHA"', workflow)
        self.assertIn("review_cost_preflight.py", workflow)
        self.assertNotIn("github.event.pull_request.number", workflow)
        self.assertIn("anthropic-review-${{ inputs.pr_number }}-", workflow)
        self.assertNotIn("ALT_REVIEW_API_KEY:", workflow)


if __name__ == "__main__":
    unittest.main()
