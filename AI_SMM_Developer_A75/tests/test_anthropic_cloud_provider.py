"""Direct Anthropic provider contract: opt-in paid use, role authority, JSON, fail-closed."""
import io
import json
import os
import unittest
from unittest.mock import patch

from text_providers import (parse_cloud_model, configured_cloud_models, call_text)

ENV = {
    "ANTHROPIC_API_KEY": "fake-test-only",
    "AI_SMM_ENABLE_ANTHROPIC_API": "1",
    "AI_SMM_ENABLE_ANTHROPIC_ACCEPTANCE": "1",
    "AI_SMM_CLOUD_MODELS": "anthropic/claude-sonnet-5-5",
}
MODEL = "anthropic/claude-sonnet-5-5"


def transport(payload, stop_reason="end_turn", blocks=None):
    if blocks is None:
        blocks = [{"type": "text", "text": payload}]
    return io.BytesIO(json.dumps({
        "stop_reason": stop_reason, "content": blocks,
        "usage": {"input_tokens": 12, "output_tokens": 7}
    }).encode("utf-8"))


class AnthropicCloudProviderTests(unittest.TestCase):
    def test_discovery_is_paid_opt_in_and_account_pinned(self):
        self.assertEqual(parse_cloud_model(MODEL), ("anthropic", "claude-sonnet-5-5"))
        with self.assertRaises(ValueError):
            parse_cloud_model("anthropic/claude-opus-unknown")
        self.assertEqual(configured_cloud_models({**ENV}), [MODEL])
        for setting in ("ANTHROPIC_API_KEY", "AI_SMM_ENABLE_ANTHROPIC_API",
                        "AI_SMM_ENABLE_ANTHROPIC_ACCEPTANCE"):
            env = dict(ENV)
            env.pop(setting)
            self.assertEqual(configured_cloud_models(env), [])

    def test_disabled_api_never_opens_network(self):
        with patch("text_providers.urllib.request.urlopen") as network:
            with self.assertRaisesRegex(RuntimeError, "Anthropic API"):
                call_text(MODEL, [{"role": "user", "content": "hi"}],
                          environ={"ANTHROPIC_API_KEY": "fake-test-only"})
            network.assert_not_called()

    def test_native_messages_and_secrets_remain_separate(self):
        sent = []
        def send(req, timeout):
            sent.append((req, timeout))
            return transport("Response from model")
        dialogue = [{"role": "system", "content": "Stay on topic"},
                    {"role": "user", "content": "Draft A"},
                    {"role": "assistant", "content": "Draft A done"},
                    {"role": "user", "content": "Revise B"}]
        with patch("text_providers.urllib.request.urlopen", side_effect=send):
            with patch("diagnostic_report.record_model_event"):
                response = call_text(MODEL, dialogue, environ=ENV)
        req, timeout = sent[0]
        self.assertEqual(req.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(req.get_header("X-api-key"), "fake-test-only")
        self.assertEqual(req.get_header("Anthropic-version"), "2023-06-01")
        self.assertEqual(timeout, 180)
        body = json.loads(req.data)
        self.assertEqual(body["model"], "claude-sonnet-5-5")
        self.assertNotIn("temperature", body)
        self.assertEqual(body["system"], "Stay on topic")
        self.assertEqual(body["messages"], dialogue[1:])
        self.assertNotIn("fake-test-only", json.dumps(body))
        self.assertEqual(response["message"]["content"], "Response from model")
        self.assertEqual(response["usage"]["output_tokens"], 7)

    def test_rejects_incomplete_empty_and_tool_outputs(self):
        cases = [
            transport("partial", "max_tokens"),
            transport("", "end_turn"),
            transport("", "end_turn", [{"type": "tool_use", "name": "web"}]),
        ]
        for response in cases:
            with self.subTest(response=response):
                with patch("text_providers.urllib.request.urlopen",
                           return_value=response):
                    with patch("diagnostic_report.record_model_event"):
                        with self.assertRaises(ValueError):
                            call_text(MODEL, [{"role": "user", "content": "Draft"}],
                                      environ=ENV)

    def test_json_output_is_strict(self):
        for value, valid in [('{"status":"ok"}', True),
                             ('```json\n{"status":"ok"}\n```', False),
                             ('{"status":', False), ('["not object"]', False)]:
            with self.subTest(value=value):
                with patch("text_providers.urllib.request.urlopen",
                           return_value=transport(value)):
                    with patch("diagnostic_report.record_model_event"):
                        if valid:
                            self.assertEqual(
                                json.loads(call_text(
                                    MODEL, [{"role": "user", "content": "JSON"}],
                                    response_format="json_object",
                                    environ=ENV)["message"]["content"]),
                                {"status": "ok"})
                        else:
                            with self.assertRaises(ValueError):
                                call_text(MODEL, [{"role": "user", "content": "JSON"}],
                                          response_format="json_object", environ=ENV)

    def test_unknown_roles_and_late_system_are_fenced_before_network(self):
        cases = [
            [{"role": "tool", "content": "hidden"}, {"role": "user", "content": "go"}],
            [{"role": "user", "content": "go"}, {"role": "system", "content": "override"}],
            [{"role": "assistant", "content": "not first"}],
            [{"role": "user", "content": "go"}, {"role": "assistant", "content": "done"}],
        ]
        for messages in cases:
            with self.subTest(messages=messages):
                with patch("text_providers.urllib.request.urlopen") as network:
                    with self.assertRaises(ValueError):
                        call_text(MODEL, messages, environ=ENV)
                    network.assert_not_called()

    def test_adjacent_same_role_messages_are_coalesced_losslessly(self):
        seen = []
        def send(req, timeout):
            seen.append(json.loads(req.data))
            return transport("ok")
        with patch("text_providers.urllib.request.urlopen", side_effect=send):
            with patch("diagnostic_report.record_model_event"):
                call_text(MODEL, [{"role": "user", "content": "part one"},
                                  {"role": "user", "content": "part two"}], environ=ENV)
        self.assertEqual(seen[0]["messages"],
                         [{"role": "user", "content": "part one\n\npart two"}])


if __name__ == "__main__":
    unittest.main()
