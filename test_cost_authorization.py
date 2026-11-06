import unittest
from decimal import Decimal
from cost_authorization import CostGate, PaymentDenied


class CostGateTests(unittest.TestCase):
    def test_no_silent_paid_fallback(self):
        gate = CostGate()
        with self.assertRaises(PaymentDenied):
            gate.reserve("paid", "image", "0.01")

    def test_one_use_scope_and_month(self):
        gate = CostGate()
        token = gate.approve("provider", "video", "3", month="2026-11")
        with self.assertRaises(PaymentDenied):
            gate.reserve("provider", "image", "1", token=token, month="2026-11")
        reservation = gate.reserve("provider", "video", "2", token=token, month="2026-11")
        with self.assertRaises(PaymentDenied):
            gate.reserve("provider", "video", "1", token=token, month="2026-11")
        gate.settle(reservation, "1.5")
        self.assertEqual(gate._spent["2026-11"], Decimal("1.5"))

    def test_budget_includes_reserved_calls(self):
        gate = CostGate()
        first = gate.approve("p", "image", "12", month="2026-11")
        gate.reserve("p", "image", "12", token=first, month="2026-11")
        second = gate.approve("p", "image", "9", month="2026-11")
        with self.assertRaises(PaymentDenied):
            gate.reserve("p", "image", "9", token=second, month="2026-11")

    def test_invalid_prices_fail_closed(self):
        gate = CostGate()
        for price in ("NaN", "-1", "Infinity", "broken"):
            with self.assertRaises(PaymentDenied):
                gate.approve("p", "image", price)

    def test_wrong_month_denied(self):
        gate = CostGate()
        token = gate.approve("p", "image", "2", month="2026-11")
        with self.assertRaises(PaymentDenied):
            gate.reserve("p", "image", "1", token=token, month="2026-12")


if __name__ == "__main__":
    unittest.main()
