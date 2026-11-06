"""No-network tests for Anthropic account-model selection."""
import io
import json
import unittest
from unittest.mock import patch

from anthropic_direct_smoke import choose_model, models_for_key


class AnthropicInventoryTests(unittest.TestCase):
    def test_select_listed_model(self):
        models = ["claude-sonnet-5-5", "claude-sonnet-4-5-20250929"]
        self.assertEqual(choose_model(models, "claude-sonnet-5-5"), "claude-sonnet-5-5")

    def test_fall_back_only_to_verified_sonnet(self):
        models = ["claude-haiku-4-5", "claude-sonnet-5-5"]
        self.assertEqual(choose_model(models, "claude-sonnet-4-5"), "claude-sonnet-5-5")
        self.assertIsNone(choose_model(["claude-haiku-4-5"], "not-listed"))

    def test_inventory_filters_unrecognized_ids(self):
        payload = {"data": [{"id": "claude-sonnet-5-5"},
                            {"id": "private-or-unsafe-model"},
                            {"id": "claude-opus-5-5"}]}
        reqs = []
        def fake_urlopen(req, timeout):
            reqs.append(req)
            return io.BytesIO(json.dumps(payload).encode())
        with patch("anthropic_direct_smoke.urllib.request.urlopen",
                   side_effect=fake_urlopen):
            self.assertEqual(models_for_key("dummy-test-key"),
                             ["claude-sonnet-5-5", "claude-opus-5-5"])
        self.assertEqual(reqs[0].get_header("X-api-key"), "dummy-test-key")
        self.assertNotIn("dummy-test-key", reqs[0].full_url)


if __name__ == "__main__":
    unittest.main()
