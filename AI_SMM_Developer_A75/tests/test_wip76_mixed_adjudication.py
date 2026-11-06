import unittest
from semantic_editor import audit_post_with_independent_model


class MixedIndependentAdjudicationTests(unittest.TestCase):
    def test_confirmed_new_findings_remain_blocking_when_other_findings_dismissed(self):
        post = {'caption': 'draft caption', 'hook': 'draft hook'}
        first = {'field': 'caption', 'quote': 'draft caption', 'reason': 'first concern'}
        confirmed = {'field': 'hook', 'quote': 'draft hook', 'reason': 'confirmed concern'}
        dismissed = {'field': 'caption', 'quote': 'draft caption', 'reason': 'dismissed concern'}
        calls = []

        def audit(ask, reviewer, request, facts, slot, candidate, previous_posts=()):
            calls.append(('audit', reviewer))
            if reviewer == 'mistral:7b':
                return None, [first]
            if reviewer == 'qwen3:8b':
                return None, [confirmed, dismissed]
            self.fail('Third model is not a full reviewer')

        def adjudicate(ask, reviewer, request, content, findings, **kwargs):
            calls.append(('adjudicate', reviewer))
            if reviewer == 'qwen3:8b':
                return [], [first]
            if reviewer == 'llama3.1:8b':
                return [confirmed], [dismissed]
            self.fail('Unexpected adjudicator')

        result, issues = audit_post_with_independent_model(
            lambda *args: None, 'gemma3:4b',
            ['mistral:7b', 'qwen3:8b', 'llama3.1:8b'], 'request', {},
            {'week': 1}, post, audit_function=audit,
            adjudicate_function=adjudicate)
        self.assertIsNone(result)
        self.assertEqual(issues, [confirmed])
        self.assertEqual(calls, [('audit', 'mistral:7b'),
                                 ('adjudicate', 'qwen3:8b'),
                                 ('audit', 'qwen3:8b'),
                                 ('adjudicate', 'llama3.1:8b')])


if __name__ == '__main__':
    unittest.main()
