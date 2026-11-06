"""Offline contract tests for direct Anthropic, with fake secrets only."""
import io
import json
import os
import unittest
from unittest.mock import patch

from independent_review import request_review

VERDICT = {"verdict": "NEEDS_INFORMATION", "critical_findings": [],
           "confidence": "low", "notes": "No code supplied"}


def fake_response(stop="end_turn", text=None):
    return io.BytesIO(json.dumps({
        "stop_reason": stop,
        "content": [{"type": "text", "text": json.dumps(VERDICT) if text is None else text}],
    }).encode("utf-8"))


class AnthropicDirectTransportTests(unittest.TestCase):
    def test_native_headers_and_system_separation(self):
        seen = []
        def send(request, timeout=None):
            seen.append((request, timeout))
            return fake_response()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-placeholder",
                                    "ANTHROPIC_MODEL": "claude-sonnet-4-5"}):
            with patch("independent_review.urllib.request.urlopen", side_effect=send):
                self.assertEqual(request_review("anthropic", "tiny diff"), VERDICT)
        req, timeout = seen[0]
        self.assertEqual(req.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(req.get_header("X-api-key"), "test-placeholder")
        self.assertEqual(req.get_header("Anthropic-version"), "2023-06-01")
        payload = json.loads(req.data)
        self.assertEqual(payload["model"], "claude-sonnet-4-5")
        self.assertGreaterEqual(payload["max_tokens"], 4096)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "tiny diff"}])
        self.assertNotIn("test-placeholder", json.dumps(payload))
        self.assertEqual(timeout, 180)

    def test_missing_key_fails_before_network(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "",
                                    "ANTHROPIC_MODEL": "claude-sonnet-4-5"}):
            with patch("independent_review.urllib.request.urlopen") as network:
                with self.assertRaisesRegex(RuntimeError, "missing"):
                    request_review("anthropic", "diff")
                network.assert_not_called()

    def test_truncated_or_invalid_json_cannot_approve(self):
        for stop, text in [("max_tokens", json.dumps(VERDICT)),
                           ("tool_use", json.dumps(VERDICT)),
                           ("end_turn", "not json")]:
            with self.subTest(stop=stop, text=text):
                with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-placeholder",
                                            "ANTHROPIC_MODEL": "claude-sonnet-4-5"}):
                    with patch("independent_review.urllib.request.urlopen",
                               return_value=fake_response(stop, text)):
                        with self.assertRaises(ValueError):
                            request_review("anthropic", "diff")


if __name__ == "__main__":
    unittest.main()
