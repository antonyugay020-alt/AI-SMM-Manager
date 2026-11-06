"""No-network contract tests for JSON-only Google Gemini backup reviewer."""
import io
import json
import os
import unittest
from unittest.mock import patch

from independent_review import request_review

APPROVE = {
    "verdict": "APPROVE", "confidence": "high",
    "critical_findings": [], "notes": "Reviewed evidence",
}
SETTINGS = {
    "ALT_REVIEW_API_KEY": "placeholder-test-only",
    "ALT_REVIEW_MODEL": "gemini-3.1-pro-preview",
    "ALT_REVIEW_API_URL": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
}


def response(content, finish_reason="stop", **message_fields):
    body = {"choices": [{
        "finish_reason": finish_reason,
        "message": {"role": "assistant", "content": content, **message_fields},
    }]}
    return io.BytesIO(json.dumps(body).encode("utf-8"))


class GeminiReviewTransportTests(unittest.TestCase):
    def test_json_mode_and_model_are_sent(self):
        seen = []
        def send(req, timeout):
            seen.append(req)
            return response(json.dumps(APPROVE))
        with patch.dict(os.environ, SETTINGS):
            with patch("independent_review.urllib.request.urlopen", side_effect=send):
                self.assertEqual(request_review("compatible", "diff data"), APPROVE)
        body = json.loads(seen[0].data)
        self.assertEqual(body["model"], "gemini-3.1-pro-preview")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertGreaterEqual(body["max_tokens"], 4096)
        self.assertNotIn("placeholder-test-only", json.dumps(body))
        self.assertEqual(seen[0].get_header("Authorization"), "Bearer placeholder-test-only")

    def test_empty_or_truncated_thinking_cannot_be_approved(self):
        cases = [(None, "stop"), ("", "stop"), (json.dumps(APPROVE), "length")]
        for content, reason in cases:
            with self.subTest(content=content, reason=reason):
                with patch.dict(os.environ, SETTINGS):
                    with patch("independent_review.urllib.request.urlopen",
                               return_value=response(content, reason)):
                        with self.assertRaises(ValueError):
                            request_review("compatible", "diff data")

    def test_refusal_and_tool_call_cannot_be_approved(self):
        for field in [{"refusal": "cannot evaluate"},
                      {"tool_calls": [{"id": "mock"}]}]:
            with self.subTest(field=field):
                with patch.dict(os.environ, SETTINGS):
                    with patch("independent_review.urllib.request.urlopen",
                               return_value=response(json.dumps(APPROVE), **field)):
                        with self.assertRaises(ValueError):
                            request_review("compatible", "diff data")

    def test_markdown_or_ungrounded_approval_remains_blocked(self):
        for payload in ["```json\n"+json.dumps(APPROVE)+"\n```",
                        "Sure, approved", '{"verdict":"APPROVE"}']:
            with self.subTest(payload=payload):
                with patch.dict(os.environ, SETTINGS):
                    with patch("independent_review.urllib.request.urlopen",
                               return_value=response(payload)):
                        with self.assertRaises(ValueError):
                            request_review("compatible", "diff data")


if __name__ == "__main__":
    unittest.main()
