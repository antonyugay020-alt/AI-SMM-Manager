import tempfile
import sqlite3
from concurrent.futures import ThreadPoolExecutor
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
        self.assertEqual(self.gate.usage()["committed_usd"], 2)
        self.assertTrue(DurableCostGate(self.path).usage()["paid_calls_frozen"])
        self.gate.set_monthly_cap("100")
        with self.assertRaisesRegex(PaymentDenied, "frozen"):
            self.gate.reserve("vendor", "image", "1", token=self.gate.approve("vendor", "image", "1"))


    def test_reject_zero_quote_and_sqlite_overflow(self):
        token = self.gate.approve("vendor", "image", "2")
        with self.assertRaisesRegex(PaymentDenied, "positive"):
            self.gate.reserve("vendor", "image", "0", token=token)
        with self.assertRaises(PaymentDenied):
            self.gate.set_monthly_cap("1000000000000000000000")
        self.assertEqual(self.gate.usage()["cap_usd"], 20)

    def test_upgrade_old_database_keeps_budget_and_grants(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE cost_settings SET cap=7000000 WHERE id=1")
            db.execute("ALTER TABLE cost_settings RENAME TO cost_settings_new")
            db.execute("CREATE TABLE cost_settings (id INTEGER PRIMARY KEY CHECK(id=1), cap INTEGER NOT NULL CHECK(cap>=0))")
            db.execute("INSERT INTO cost_settings(id,cap) SELECT id,cap FROM cost_settings_new")
            db.execute("DROP TABLE cost_settings_new")
        reopened = DurableCostGate(self.path)
        self.assertEqual(reopened.usage()["cap_usd"], 7)
        self.assertFalse(reopened.usage()["paid_calls_frozen"])

    def test_atomic_multi_connection_reservation(self):
        first = self.gate.approve("vendor", "image", "12")
        second = self.gate.approve("vendor", "image", "12")
        def attempt(token):
            try:
                DurableCostGate(self.path).reserve("vendor", "image", "12", token=token)
                return "accepted"
            except PaymentDenied:
                return "denied"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, (first, second)))
        self.assertEqual(sorted(results), ["accepted", "denied"])
        self.assertEqual(self.gate.usage()["committed_usd"], 12)

    def test_settlement_replay_is_rejected_without_double_charge(self):
        token = self.gate.approve("vendor", "image", "2")
        reservation = self.gate.reserve("vendor", "image", "2", token=token)
        self.gate.settle(reservation, "1.25")
        with self.assertRaises(PaymentDenied):
            self.gate.settle(reservation, "1.25")
        self.assertEqual(self.gate.usage()["committed_usd"], 1.25)


if __name__ == "__main__":
    unittest.main()
