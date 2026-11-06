import json
import unittest

from document_recovery import patch_document
from strategy_integrity import normalize_explicit_cadence, strategy_integrity_issues


class StructuredRepairContractTests(unittest.TestCase):
    def test_repair_receives_exact_review_finding_and_targets_its_line(self):
        source = "# Strategy\n- Value proposition: classes help adults speak confidently.\n- Other: preserve this.\n"
        finding = {
            "document": "strategy",
            "type": "invented_fact",
            "quote": "help adults speak confidently",
            "claim": "help adults speak confidently",
            "evidence_segment": "Value proposition: classes help adults speak confidently.",
            "evidence_id": "strategy:0002",
            "field_role": "Value proposition",
            "reason": "The outcome is not established by the brief.",
            "statement_class": "service_outcome",
            "adjudication": "unsupported",
        }
        captured = {}

        def ask(model, system, user, **kwargs):
            captured.update(json.loads(user))
            return json.dumps({"changes": [{
                "line_id": "L00002",
                "text": "- Value proposition: a content theme about common speaking situations.",
            }]})

        updated = patch_document(ask, "qwen3:8b", "brief", source,
                                 "unsupported outcome", findings=[finding])
        self.assertIn("common speaking situations", updated)
        self.assertIn("help adults speak confidently", source)
        self.assertEqual(captured["structured_findings"], [finding])
        self.assertEqual(captured["priority_line_ids"], ["L00002"])


class CadenceConsistencyTests(unittest.TestCase):
    def test_cadence_without_repeated_publication_noun_is_checked_and_canonicalized(self):
        draft = "Количество публикаций: 2 (по 1 в неделю)\n"
        issues = strategy_integrity_issues(draft, "2 публикации в неделю", per_week=2)
        self.assertTrue(any("1 публикаций в неделю вместо 2" in issue for issue in issues))
        normalized, edits = normalize_explicit_cadence(draft, 2)
        self.assertEqual(len(edits), 1)
        self.assertIn("по 2 публикации в неделю", normalized)
        self.assertEqual(strategy_integrity_issues(normalized, "2 публикации в неделю", per_week=2), [])


if __name__ == "__main__":
    unittest.main()
