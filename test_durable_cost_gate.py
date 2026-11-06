import tempfile
import unittest
from pathlib import Path
from cost_authorization import PaymentDenied
from durable_cost_gate import DurableCostGate


class DurableCostGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "cost.sqlite"
        self.gate = DurableCostGate(self.path)

    def test_restart_preserves_reservations_and_cap(self):
        self.gate.set_monthly_cap("3")
        token = self.gate.approve("vendor", "video", "2")
        reservation = self.gate.reserve("vendor", "video", "2", token=token)
        restarted = DurableCostGate(self.path)
        self.assertEqual(restarted.usage()["available_usd"], 1)
        with self.assertRaises(PaymentDenied):
            restarted.reserve("vendor", "video", "2", token=token)
        restarted.settle(reservation, "1.50")
        self.assertEqual(DurableCostGate(self.path).usage()["available_usd"], 1.5)

    def test_lowering_cap_never_erases_spending(self):
        token = self.gate.approve("vendor", "image", "8")
        self.gate.reserve("vendor", "image", "8", token=token)
        self.gate.set_monthly_cap("5")
        self.assertEqual(self.gate.usage()["available_usd"], 0)
        with self.assertRaises(PaymentDenied):
            self.gate.reserve("vendor", "image", "1", token=self.gate.approve("vendor", "image", "1"))

    def test_no_approval_and_overspend_blocked(self):
        with self.assertRaises(PaymentDenied):
            self.gate.reserve("vendor", "image", "1")
        token = self.gate.approve("vendor", "image", "1")
        with self.assertRaises(PaymentDenied):
            self.gate.reserve("vendor", "video", "1", token=token)
        with self.assertRaises(PaymentDenied):
            self.gate.reserve("vendor", "image", "2", token=token)

    def test_settlement_cannot_exceed_reservation(self):
        token = self.gate.approve("vendor", "image", "1")
        reservation = self.gate.reserve("vendor", "image", "1", token=token)
        with self.assertRaises(PaymentDenied):
            self.gate.settle(reservation, "2")
        self.assertEqual(self.gate.usage()["committed_usd"], 1)


if __name__ == "__main__":
    unittest.main()
