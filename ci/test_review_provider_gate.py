"""No-network safeguards for optional independent model failover."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

DIR = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("review_provider_gate", DIR / "review_provider_gate.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)

APPROVE = {"verdict": "APPROVE", "critical_findings": [],
           "confidence": "high", "notes": "test"}
REJECT = {"verdict": "REJECT", "critical_findings": [
          {"file": "a.py", "reason": "critical defect"}],
          "confidence": "high", "notes": "test"}


def evidence(provider, diff, verdict=APPROVE, status="PASS", reason=None):
    parts = gate.split_review_diff(diff, max_chars=20_000, max_parts=24)
    return {"provider": provider, "status": status, "reason": reason,
            "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
            "review_contract_sha256": "1" * 64,
            "segments_expected": len(parts), "segments_reviewed": len(parts),
            "reviews": [
                {"index": i, "sha256": hashlib.sha256(part.encode("utf-8")).hexdigest(),
                 "verdict": verdict}
                for i, part in enumerate(parts, 1)]}


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.diff = "diff --git a/a.py b/a.py\n+new_value=1\n"
        self.diff_path = self.root / "a.diff"
        self.primary = self.root / "primary.json"
        self.backup = self.root / "backup.json"
        self.output = self.root / "gate.json"
        self.diff_path.write_text(self.diff, encoding="utf-8")
        self.env = {"ALT_REVIEW_API_KEY": "dummy", "ALT_REVIEW_API_URL": "https://example.invalid/chat",
                    "ALT_REVIEW_MODEL": "independent-model"}

    def check(self, initial, runner=None, env=None):
        self.primary.write_text(json.dumps(initial), encoding="utf-8")
        return gate.run_gate(self.diff_path, self.primary, self.backup, self.output,
                             env=self.env if env is None else env, runner=runner)

    def test_complete_primary_does_not_run_paid_backup(self):
        runner = Mock()
        self.assertEqual(self.check(evidence("kie", self.diff), runner), 0)
        runner.assert_not_called()
        self.assertEqual(json.loads(self.output.read_text())["reviewer"], "kie")

    def test_only_transient_gateway_failure_triggers_backup(self):
        first = evidence("kie", self.diff, status="BLOCKED", reason="provider HTTP status 502")
        def secondary(command, **kwargs):
            self.assertEqual(command[command.index("--provider")+1], "compatible")
            self.assertEqual(kwargs["timeout"], 600)
            self.backup.write_text(json.dumps(evidence("compatible", self.diff)), encoding="utf-8")
        runner = Mock(side_effect=secondary)
        self.assertEqual(self.check(first, runner), 0)
        runner.assert_called_once()
        self.assertEqual(json.loads(self.output.read_text())["reviewer"], "compatible")

    def test_rejection_cannot_be_bypassed(self):
        first = evidence("kie", self.diff, REJECT, status="BLOCKED", reason="provider HTTP status 503")
        runner = Mock()
        self.assertEqual(self.check(first, runner), 2)
        runner.assert_not_called()

    def test_primary_non_transient_or_missing_backup_configuration_blocks(self):
        for reason in ("provider HTTP status 401", "reviewer returned NEEDS_INFORMATION",
                       "provider HTTP status 502 injected", None):
            with self.subTest(reason=reason):
                first = evidence("kie", self.diff, status="BLOCKED", reason=reason)
                runner = Mock()
                self.assertEqual(self.check(first, runner), 2)
                runner.assert_not_called()
        first = evidence("kie", self.diff, status="BLOCKED", reason="provider HTTP status 500")
        runner = Mock()
        self.assertEqual(self.check(first, runner, env={}), 2)
        runner.assert_not_called()

    def test_stale_or_partial_backup_never_approves(self):
        first = evidence("kie", self.diff, status="BLOCKED", reason="provider HTTP status 502")
        for mode in ("old_diff", "unreviewed", "missing_segment"):
            with self.subTest(mode=mode):
                def secondary(command, **kwargs):
                    rec = evidence("compatible", self.diff)
                    if mode == "old_diff": rec["diff_sha256"] = "x" * 64
                    elif mode == "unreviewed": rec["reviews"][0]["verdict"] = REJECT
                    else: rec["reviews"].clear()
                    self.backup.write_text(json.dumps(rec), encoding="utf-8")
                self.assertEqual(self.check(first, secondary), 2)
                self.assertEqual(json.loads(self.output.read_text())["status"], "BLOCKED")

    def test_malformed_cached_primary_must_not_certify(self):
        runner = Mock()
        fake = evidence("kie", self.diff)
        fake["reviews"][0]["sha256"] = "wrong"
        self.assertEqual(self.check(fake, runner), 2)
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
