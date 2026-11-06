import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import independent_acceptance_editor as reviewer


class ReviewEvidenceTransactionTests(unittest.TestCase):
    def test_prompt_schema_and_host_parser_share_atomic_field_evidence_catalog(self):
        strategy = "# Strategy\nNo business facts."
        calendar = (
            "| Week/day | Topic | Caption | Visual | CTA | KPI |\n"
            "|---|---|---|---|---|---|\n"
            "| 1/Mon | Process | Ask about the class. | Show a staged scene. | Send a question. | Replies |"
        )
        catalog, _ = reviewer._editor_evidence_index(strategy, calendar)
        captured = {}

        def fake_request(body, **kwargs):
            captured["body"] = body
            return {"message": {"content": json.dumps({"pass": True, "issues": []})}}

        with patch.object(reviewer, "request_chat", side_effect=fake_request):
            result = reviewer._call_editor_once("Review the supplied content", strategy, calendar)

        schema = captured["body"]["format"]
        allowed = schema["properties"]["issues"]["items"]["properties"]["evidence_id"]["enum"]
        prompt = captured["body"]["messages"][1]["content"]
        self.assertEqual(set(allowed), set(catalog))
        self.assertIn('"field": "Caption"', prompt)
        self.assertIn('"field": "Visual"', prompt)
        self.assertIn('"row": "1/Mon"', prompt)
        self.assertIn('[calendar:0003]', prompt)
        self.assertIn('[calendar:0003] Ask about the class.', prompt)
        self.assertNotIn('| 1/Mon | Process |', prompt)
        self.assertTrue(result["pass"])

    def test_large_evidence_catalog_uses_bounded_schema_and_host_parser_still_checks_ids(self):
        strategy = "# Strategy\n" + "\n".join(
            f"- Distinct planning statement number {index} with enough detail." for index in range(60))
        captured = {}

        def fake_request(body, **kwargs):
            captured["body"] = body
            return {"message": {"content": json.dumps({"pass": True, "issues": []})}}

        with patch.object(reviewer, "request_chat", side_effect=fake_request):
            result = reviewer._call_editor_once("Check the document", strategy, "Calendar stub")

        schema = captured["body"]["format"]["properties"]["issues"]["items"]["properties"]["evidence_id"]
        self.assertGreater(len(reviewer._editor_evidence_index(strategy, "Calendar stub")[0]), 48)
        self.assertEqual(schema["type"], "string")
        self.assertLessEqual(schema["maxLength"], 48)
        self.assertNotIn("enum", schema)
        self.assertTrue(result["pass"])

        catalog = reviewer._editor_evidence_index(strategy, "Calendar stub")[0]
        invalid = json.dumps({"pass": False, "issues": [{"document": "strategy",
            "type": "quality", "evidence_id": "strategy:9999", "reason": "Specific quality issue."}]})
        with self.assertRaisesRegex(ValueError, "недостоверный идентификатор"):
            reviewer.parse_verdict(invalid, strategy=strategy, calendar="Calendar stub", require_claim=True,
                host_evidence_only=True, table_cells=True, _evidence_catalog=catalog)

    def test_host_catalog_rejects_ids_outside_the_shared_transaction(self):
        strategy = "# Strategy\nNo business facts."
        calendar = "| Topic | Caption |\n|---|---|\n| A topic | A caption |"
        catalog = reviewer._evidence_chunks(strategy, calendar, table_cells=False)
        invalid = json.dumps({
            "pass": False,
            "issues": [{"document": "calendar", "type": "quality",
                        "evidence_id": "calendar:9999", "reason": "Specific quality issue."}],
        })
        with self.assertRaisesRegex(ValueError, "недостоверный идентификатор"):
            reviewer.parse_verdict(invalid, strategy=strategy, calendar=calendar,
                                   require_claim=True, host_evidence_only=True,
                                   table_cells=False, _evidence_catalog=catalog)


if __name__ == "__main__":
    unittest.main()
