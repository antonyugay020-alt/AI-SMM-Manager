import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from durable_cost_gate import DurableCostGate
from local_first_dispatcher import LocalFirstDispatcher, NoProviderAvailable


class Provider:
    def __init__(self, name, tier, output=None, fail=False):
        self.name, self.tier, self.output, self.fail = name, tier, output, fail
        self.calls = 0

    def supports(self, task):
        return True

    def execute(self, task):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider unavailable")
        return self.output

    def estimate_usd(self, task):
        return "2"

    def actual_usd(self, result):
        return "1"


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gate = DurableCostGate(Path(self.tmp.name) / "ledger.sqlite")
        self.task = SimpleNamespace(operation="image")

    def test_local_wins_even_if_paid_listed_first(self):
        paid, local = Provider("paid", "paid", "paid"), Provider("local", "local", "local")
        result = LocalFirstDispatcher([paid, local], self.gate).run(self.task)
        self.assertEqual(result.output, "local")
        self.assertEqual(paid.calls, 0)

    def test_free_fallback_does_not_call_paid(self):
        local = Provider("local", "local", fail=True)
        free = Provider("free", "free", output="free result")
        paid = Provider("paid", "paid", output="paid result")
        result = LocalFirstDispatcher([paid, local, free], self.gate).run(self.task)
        self.assertEqual(result.tier, "free")
        self.assertEqual(paid.calls, 0)

    def test_no_paid_call_without_matching_approval(self):
        paid = Provider("paid", "paid", output="paid result")
        dispatcher = LocalFirstDispatcher([paid], self.gate)
        with self.assertRaises(NoProviderAvailable):
            dispatcher.run(self.task)
        self.assertEqual(paid.calls, 0)
        token = self.gate.approve("paid", "image", "2")
        result = dispatcher.run(self.task, approved_provider="paid", approval_token=token)
        self.assertEqual(result.tier, "paid")
        self.assertEqual(self.gate.usage()["committed_usd"], 1)

    def test_ambiguous_failure_keeps_reservation(self):
        paid = Provider("paid", "paid", fail=True)
        token = self.gate.approve("paid", "image", "2")
        with self.assertRaises(RuntimeError):
            LocalFirstDispatcher([paid], self.gate).run(
                self.task, approved_provider="paid", approval_token=token)
        self.assertEqual(self.gate.usage()["committed_usd"], 2)


if __name__ == "__main__":
    unittest.main()
