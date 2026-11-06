"""Offline tests: Gemini diagnostic errors must not leak secrets."""
import io
import json
import os
import urllib.error
import unittest
from unittest.mock import patch

from gemini_review_smoke import safe_provider_error, sanitized_google_message


class GeminiErrorSafetyTests(unittest.TestCase):
    def test_google_model_explanation_is_preserved(self):
        actual = "models/gemini-2.5-pro is not found for API version v1beta, or is not supported for generateContent."
        self.assertEqual(sanitized_google_message(actual), actual)

    def test_keys_bearer_and_url_redacted(self):
        secret = "AIza"+"A"*32
        with patch.dict(os.environ, {"ALT_REVIEW_API_KEY": secret}):
            raw = "Request failed key=" + secret + " Bearer " + secret + " https://example.com/?key=" + secret
            safe = sanitized_google_message(raw)
        self.assertNotIn(secret, safe)
        self.assertNotIn("example.com", safe)
        self.assertIn("[REDACTED]", safe)

    def test_unstructured_and_long_secrets_rejected(self):
        self.assertIsNone(sanitized_google_message({"message": "secret"}))
        opaque = "z" * 44
        self.assertNotIn(opaque, sanitized_google_message("Detail "+opaque))
        self.assertLessEqual(len(sanitized_google_message("x" * 10000)), 500)

    def test_structured_google_error_redacts_message(self):
        secret = "AIza"+"B"*32
        payload = {"error": {"code": 404, "status": "NOT_FOUND",
                             "message": "API key " + secret + ": model gemini-2.5-flash unavailable",
                             "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                                          "reason": "SERVICE_DISABLED"}]}}
        error = urllib.error.HTTPError("https://example.invalid", 404, "Not Found", {},
                                      io.BytesIO(json.dumps(payload).encode()))
        with patch.dict(os.environ, {"ALT_REVIEW_API_KEY": secret}):
            result = safe_provider_error(error)
        self.assertEqual(result["http_status"], 404)
        self.assertEqual(result["google_status"], "NOT_FOUND")
        self.assertNotIn(secret, json.dumps(result))
        self.assertIn("gemini-2.5-flash", result["google_message_redacted"])
        self.assertEqual(result["error_reasons"], ["SERVICE_DISABLED"])

    def test_invalid_body_never_echoed(self):
        error = urllib.error.HTTPError("https://example.invalid", 404, "Not Found", {},
                                      io.BytesIO(b"non-json-secret-contents"))
        result = safe_provider_error(error)
        self.assertEqual(result["structured_error"], "unavailable")
        self.assertNotIn("secret", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
