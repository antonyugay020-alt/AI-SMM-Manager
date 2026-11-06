import json
import unittest
from publication_writer import write_week

class UnknownTaskFactsTests(unittest.TestCase):
    def test_explicit_unknowns_reach_post_writer_as_non_authorizing_context(self):
        captured = {}
        def ask(model, system, user):
            captured.update(system=system, user=user)
            return json.dumps({"posts": []})
        facts = {"business":"языковая школа", "task_analysis":{"unknowns":["точная методика занятий"]}}
        write_week(ask, "mock", "Сделай контент-план", facts, "стратегия", [])
        self.assertIn("task_analysis.unknowns", captured["system"])
        self.assertIn("точная методика занятий", captured["user"])
        self.assertEqual(captured["user"].count("точная методика занятий"), 1)
        self.assertIn("не заполняй пробел практическими шагами", captured["system"])

if __name__ == "__main__": unittest.main()
