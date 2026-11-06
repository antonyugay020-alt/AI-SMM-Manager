import json
import unittest
from unittest.mock import patch
import central_dialogue_transport as c


def payload(utterance):
    return {'model': 'openai/gpt-5', 'messages': [{'role': 'user', 'content': json.dumps({'utterance': utterance})}]}


class HybridRoutingTests(unittest.TestCase):
    def test_greeting_routes_local(self):
        with patch.dict('os.environ', {'AI_SMM_DIALOGUE_MODE': 'hybrid'}):
            with patch('ollama_resilience.request_chat', return_value={'message': {'content': '{}'}}) as local:
                with patch('text_providers.call_text') as cloud:
                    c.request_central_dialogue(payload('Привет!'), stage='dialogue')
        local.assert_called_once()
        cloud.assert_not_called()

    def test_project_command_routes_gpt(self):
        with patch.dict('os.environ', {'AI_SMM_DIALOGUE_MODE': 'hybrid'}):
            with patch('text_providers.call_text', return_value={'message': {'content': '{}'}}) as cloud:
                c.request_central_dialogue(payload('Создай стратегию для проекта'), stage='dialogue')
        cloud.assert_called_once()

    def test_ambiguous_turn_routes_gpt(self):
        with patch.dict('os.environ', {'AI_SMM_DIALOGUE_MODE': 'hybrid'}):
            with patch('text_providers.call_text', return_value={'message': {'content': '{}'}}) as cloud:
                c.request_central_dialogue(payload('продолжай'), stage='dialogue')
        cloud.assert_called_once()

    def test_local_only_never_uses_cloud(self):
        with patch.dict('os.environ', {'AI_SMM_DIALOGUE_MODE': 'local'}):
            with patch('ollama_resilience.request_chat', return_value={'message': {'content': '{}'}}) as local:
                with patch('text_providers.call_text') as cloud:
                    c.request_central_dialogue(payload('Создай стратегию'), stage='dialogue')
        local.assert_called_once()
        cloud.assert_not_called()

    def test_invalid_model_configuration_fails_closed(self):
        with patch.dict('os.environ', {'AI_SMM_DIALOGUE_MODE': 'local', 'AI_SMM_LOCAL_DIALOGUE_MODEL': 'openai/gpt-5'}):
            with self.assertRaises(ValueError):
                c.request_central_dialogue(payload('Привет'), stage='dialogue')
