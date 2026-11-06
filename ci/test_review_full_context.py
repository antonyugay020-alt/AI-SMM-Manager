"""Contract tests: direct Claude gets coherent context; Gemini keeps its own budget."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from independent_review import review_chunks
from review_provider_gate import run_gate

APPROVE = {"verdict": "APPROVE", "critical_findings": [],
           "confidence": "high", "notes": "verified"}


class ProviderContextTests(unittest.TestCase):
    def setUp(self):
        self.diff = "diff --git a/combined.py b/combined.py\n" + "+x = 1\n" * 5000

    def test_direct_claude_receives_full_diff(self):
        self.assertEqual(review_chunks("anthropic", self.diff), [self.diff])
        fragments = review_chunks("compatible", self.diff)
        self.assertGreater(len(fragments), 1)
        self.assertEqual("".join(fragments), self.diff)

    def test_overlong_full_context_remains_lossless(self):
        large = self.diff * 13
        pieces = review_chunks("anthropic", large)
        self.assertGreater(len(pieces), 1)
        self.assertEqual("".join(pieces), large)
        self.assertTrue(all(len(p) <= 400_000 for p in pieces))

    def test_gateway_failover_uses_complete_independent_gemini_coverage(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            inp, primary, backup, report = [root / name for name in
                ("diff.txt", "anthropic.json", "gemini.json", "gate.json")]
            inp.write_text(self.diff, encoding="utf-8")
            digest = hashlib.sha256(self.diff.encode()).hexdigest()
            def evidence(provider, chunks, status, reason=None):
                return {"provider": provider, "status": status, "reason": reason,
                        "diff_sha256": digest, "review_contract_sha256": "a"*64,
                        "segments_expected": len(chunks),
                        "segments_reviewed": len(chunks) if status=="PASS" else 0,
                        "reviews": ([{"index": i, "sha256": hashlib.sha256(chunk.encode()).hexdigest(),
                                     "verdict": APPROVE} for i,chunk in enumerate(chunks,1)]
                                    if status=="PASS" else [])}
            primary.write_text(json.dumps(evidence("anthropic",
                 review_chunks("anthropic", self.diff), "BLOCKED",
                 "provider HTTP status 503")), encoding="utf-8")
            def backup_runner(cmd, **kwargs):
                backup.write_text(json.dumps(evidence("compatible",
                    review_chunks("compatible", self.diff), "PASS")), encoding="utf-8")
                return Mock(returncode=0)
            env = {"PRIMARY_REVIEW_PROVIDER": "anthropic",
                   "ALT_REVIEW_API_KEY": "test-only", "ALT_REVIEW_API_URL": "https://example.invalid",
                   "ALT_REVIEW_MODEL": "test-gemini"}
            self.assertEqual(run_gate(inp, primary, backup, report,
                                      env=env, runner=backup_runner), 0)
            self.assertEqual(json.loads(report.read_text())["reviewer"], "compatible")
            partial = evidence("compatible", review_chunks("compatible", self.diff), "PASS")
            partial["reviews"].pop()
            def incomplete(cmd, **kwargs):
                backup.write_text(json.dumps(partial), encoding="utf-8")
                return Mock(returncode=0)
            self.assertEqual(run_gate(inp, primary, backup, report,
                                      env=env, runner=incomplete), 2)


if __name__ == "__main__":
    unittest.main()
