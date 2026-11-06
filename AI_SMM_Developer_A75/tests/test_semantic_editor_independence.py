import unittest
from unittest.mock import patch

import semantic_editor
from ollama_resilience import _MODEL_CALL_ROLE


class IndependentPublicationReviewerTests(unittest.TestCase):
    def test_post_review_uses_randomly_assigned_model_distinct_from_author(self):
        calls = []

        def audit(ask, model, request, facts, slot, post, previous_posts=(), previous_topics=()):
            calls.append((model, _MODEL_CALL_ROLE.get()))
            return dict(post), []

        with patch.object(semantic_editor, 'audit_post', side_effect=audit):
            reviewed, issues = semantic_editor.audit_post_with_independent_model(
                lambda *args: None, 'gpt-oss:20b-cloud',
                ['gpt-oss:latest', 'mistral:7b', 'qwen3:8b'],
                'request', {}, {'week': 1}, {'caption': 'text'})

        self.assertEqual(issues, [])
        self.assertIsNotNone(reviewed)
        self.assertEqual(calls, [('mistral:7b', 'reviewer')])

    def test_unavailable_primary_fails_over_once_to_distinct_backup(self):
        calls = []

        def audit(ask, model, request, facts, slot, post, previous_posts=(), previous_topics=()):
            calls.append((model, _MODEL_CALL_ROLE.get()))
            if model == 'mistral:7b':
                return None, ['Редактор вернул неструктурированный ответ']
            return dict(post), []

        events = []
        with patch.object(semantic_editor, 'audit_post', side_effect=audit):
            reviewed, issues = semantic_editor.audit_post_with_independent_model(
                lambda *args: None, 'gemma3:4b',
                ['mistral:7b', 'qwen3:8b'], 'request', {}, {'week': 1},
                {'caption': 'text'}, report=lambda stage, **data: events.append((stage, data)))

        self.assertEqual(issues, [])
        self.assertIsNotNone(reviewed)
        self.assertEqual(calls, [('mistral:7b', 'reviewer'), ('qwen3:8b', 'reviewer_backup')])
        self.assertEqual(events[0][0], 'publication_reviewer_failover')

    def test_substantive_rejection_is_not_overruled_by_backup(self):
        calls = []

        def audit(ask, model, request, facts, slot, post, previous_posts=(), previous_topics=()):
            calls.append(model)
            return None, ['Не подтверждён заявленный компанией процесс']

        with patch.object(semantic_editor, 'audit_post', side_effect=audit):
            reviewed, issues = semantic_editor.audit_post_with_independent_model(
                lambda *args: None, 'gemma3:4b', ['mistral:7b', 'qwen3:8b'],
                'request', {}, {'week': 1}, {'caption': 'text'})

        self.assertIsNone(reviewed)
        self.assertEqual(calls, ['mistral:7b'])
        self.assertIn('Не подтверждён', issues[0])


if __name__ == '__main__':
    unittest.main()
