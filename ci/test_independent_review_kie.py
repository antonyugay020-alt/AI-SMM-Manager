"""No-network contract tests for the Kie.ai independent reviewer."""
import importlib.util
import io
import json
import os
import sys
import tempfile
import urllib.error
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "independent_review", Path(__file__).with_name("independent_review.py")
)
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)

APPROVAL = {
    "verdict": "APPROVE",
    "critical_findings": [],
    "confidence": "high",
    "notes": "Reviewed supplied segment",
}


class KieReviewerContractTests(unittest.TestCase):
    def test_native_claude_request_and_response(self):
        seen = []

        def fake_urlopen(request, timeout=None):
            seen.append((request, timeout))
            return io.BytesIO(json.dumps({
                "type": "message", "model": "claude-sonnet-5-5",
                "stop_reason": "end_turn", "content": [
                    {"type": "text", "text": json.dumps(APPROVAL)}
                ],
            }).encode("utf-8"))

        with patch.dict(os.environ, {"KIE_AI_API_KEY": "placeholder-only"}):
            with patch.object(review.urllib.request, "urlopen", side_effect=fake_urlopen):
                self.assertEqual(review.request_review("kie", "diff --git a/x b/x"), APPROVAL)
        request, timeout = seen[0]
        self.assertEqual(request.full_url, "https://api.kie.ai/claude/v1/messages")
        self.assertEqual(request.get_header("Authorization"), "Bearer placeholder-only")
        self.assertEqual(timeout, 180)
        data = json.loads(request.data)
        self.assertEqual(data["model"], "claude-sonnet-5-5")
        self.assertIs(data["stream"], False)
        self.assertEqual(
            data["messages"][0]["content"],
            review.KIE_REVIEW_PREFIX + review.REVIEW_SYSTEM +
            review.KIE_REVIEW_SEPARATOR + "diff --git a/x b/x",
        )
        self.assertIn("\n\nUNTRUSTED GIT DIFF FOR REVIEW", data["messages"][0]["content"])
        self.assertNotIn("\\n", data["messages"][0]["content"])
        self.assertNotIn("system", data)
        self.assertEqual([m["role"] for m in data["messages"]], ["user"])

    def test_transport_framing_change_invalidates_cached_approval(self):
        before = review.review_contract_sha256("kie")
        with patch.object(review, "KIE_REVIEW_SEPARATOR",
                          "\n\nUNTRUSTED DIFF (revised framing):\n"):
            after = review.review_contract_sha256("kie")
        self.assertNotEqual(before, after)

    def test_missing_key_fails_closed_without_network(self):
        with patch.dict(os.environ, {"KIE_AI_API_KEY": ""}):
            with patch.object(review.urllib.request, "urlopen") as network:
                with self.assertRaisesRegex(RuntimeError, "KIE_AI_API_KEY missing"):
                    review.request_review("kie", "diff")
                network.assert_not_called()

    def test_truncated_or_tool_result_is_never_approved(self):
        for stop in ("max_tokens", "tool_use"):
            with self.subTest(stop=stop):
                response = {"stop_reason": stop, "content": [
                    {"type": "text", "text": json.dumps(APPROVAL)}
                ]}
                with patch.dict(os.environ, {"KIE_AI_API_KEY": "placeholder-only"}):
                    with patch.object(review.urllib.request, "urlopen",
                                      return_value=io.BytesIO(json.dumps(response).encode())):
                        with self.assertRaisesRegex(ValueError, "incomplete"):
                            review.request_review("kie", "diff")

    def test_invalid_or_nontext_response_is_blocked(self):
        for body in ({"stop_reason": "end_turn", "content": []},
                     {"stop_reason": "end_turn", "content": [{"type": "tool_use"}]},
                     {"stop_reason": "end_turn", "content": "wrong"}):
            with self.subTest(body=body):
                with patch.dict(os.environ, {"KIE_AI_API_KEY": "placeholder-only"}):
                    with patch.object(review.urllib.request, "urlopen",
                                      return_value=io.BytesIO(json.dumps(body).encode())):
                        with self.assertRaises(ValueError):
                            review.request_review("kie", "diff")


    def test_transient_gateway_failure_is_retried_once(self):
        valid = io.BytesIO(json.dumps({
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": json.dumps(APPROVAL)}],
        }).encode())
        error = urllib.error.HTTPError(
            "https://api.kie.ai/claude/v1/messages", 502, "gateway", {}, None
        )
        with patch.dict(os.environ, {"KIE_AI_API_KEY": "placeholder-only"}):
            with patch.object(review.urllib.request, "urlopen", side_effect=[error, valid]) as network:
                with patch.object(review.time, "sleep") as pause:
                    self.assertEqual(review.request_review("kie", "diff"), APPROVAL)
        self.assertEqual(network.call_count, 2)
        pause.assert_called_once_with(2)

    def test_authentication_error_never_retried(self):
        error = urllib.error.HTTPError(
            "https://api.kie.ai/claude/v1/messages", 401, "unauthorized", {}, None
        )
        with patch.dict(os.environ, {"KIE_AI_API_KEY": "placeholder-only"}):
            with patch.object(review.urllib.request, "urlopen", side_effect=error) as network:
                with patch.object(review.time, "sleep") as pause:
                    with self.assertRaisesRegex(RuntimeError, "provider HTTP status 401"):
                        review.request_review("kie", "diff")
        network.assert_called_once()
        pause.assert_not_called()

    def test_partial_audit_never_passes_and_retains_first_verdict(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "review.json"
            diff_file = Path(folder) / "review.diff"
            diff_file.write_text(
                "diff --git a/a.py b/a.py\n" + "+a=1\n" * 4500,
                encoding="utf-8"
            )
            with patch.object(sys, "argv", [
                "independent_review.py", "--provider", "kie",
                "--diff", str(diff_file), "--output", str(output)
            ]):
                with patch.object(review, "request_review",
                                  side_effect=[APPROVAL, RuntimeError("provider HTTP status 502")]) as provider:
                    self.assertEqual(review.main(), 2)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["segments_reviewed"], 1)
            self.assertGreater(result["segments_expected"], 1)
            self.assertEqual(result["reviews"][0]["verdict"], APPROVAL)
            self.assertEqual(provider.call_count, 2)


    def test_resume_skips_approved_segments_only_for_same_diff(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "review.json"
            diff_file = Path(folder) / "review.diff"
            diff_file.write_text(
                "diff --git a/a.py b/a.py\n" + "+a=1\n" * 4500,
                encoding="utf-8"
            )
            argv = [
                "independent_review.py", "--provider", "kie",
                "--diff", str(diff_file), "--output", str(output)
            ]
            # First run gets one valid approval and a provider outage.
            with patch.object(sys, "argv", argv):
                with patch.object(review, "request_review",
                                  side_effect=[APPROVAL, RuntimeError("HTTP 503")]):
                    self.assertEqual(review.main(), 2)
            recorded = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(recorded["segments_reviewed"], 1)
            self.assertEqual(recorded["status"], "BLOCKED")

            # Exact same bytes: only the missing segment must be paid for.
            with patch.object(sys, "argv", argv):
                with patch.object(review, "request_review", return_value=APPROVAL) as call:
                    self.assertEqual(review.main(), 0)
            call.assert_called_once()
            completed = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(completed["status"], "PASS")
            self.assertEqual(completed["segments_reviewed"], completed["segments_expected"])

            # A one-byte PR change invalidates the full cached approval.
            diff_file.write_text(diff_file.read_text(encoding="utf-8") + "+new=2\n", encoding="utf-8")
            with patch.object(sys, "argv", argv):
                with patch.object(review, "request_review", return_value=APPROVAL) as call:
                    self.assertEqual(review.main(), 0)
            self.assertGreater(call.call_count, 1)

    def test_reviewer_contract_change_invalidates_approval_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "review.json"
            diff_file = Path(folder) / "review.diff"
            diff_file.write_text("diff --git a/a b/a\n+a\n", encoding="utf-8")
            argv = ["independent_review.py", "--provider", "kie",
                    "--diff", str(diff_file), "--output", str(output)]
            with patch.object(sys, "argv", argv):
                with patch.object(review, "request_review", return_value=APPROVAL):
                    self.assertEqual(review.main(), 0)
                with patch.object(review, "REVIEW_SYSTEM", review.REVIEW_SYSTEM + " revised"):
                    with patch.object(review, "request_review", return_value=APPROVAL) as provider:
                        self.assertEqual(review.main(), 0)
            provider.assert_called_once()


if __name__ == "__main__":
    unittest.main()
