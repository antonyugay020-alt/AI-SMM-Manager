"""Offline SQLite budget, consent expiry and manual charge reconciliation."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from cost_authorization import PaymentDenied
from durable_cost_gate import DurableCostGate


class CostReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "cost.sqlite"
        self.gate = DurableCostGate(self.path)

    def reserve(self, maximum="2"):
        token = self.gate.approve("provider", "image", maximum)
        return self.gate.reserve("provider", "image", maximum, token=token)

    def test_expired_approval_fails_closed(self):
        token = self.gate.approve("provider", "image", "2")
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE cost_grants SET expires_at=0 WHERE token=?", (token,))
        with self.assertRaises(PaymentDenied):
            self.gate.reserve("provider", "image", "2", token=token)

    def test_legacy_grants_are_invalidated_on_migration(self):
        token = self.gate.approve("provider", "image", "2")
        with sqlite3.connect(self.path) as db:
            db.execute("ALTER TABLE cost_grants RENAME TO grants_backup")
            db.execute("""CREATE TABLE cost_grants (
                token TEXT PRIMARY KEY, provider TEXT NOT NULL,
                operation TEXT NOT NULL, period TEXT NOT NULL,
                maximum INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0)""")
            db.execute("""INSERT INTO cost_grants(token,provider,operation,period,maximum,used)
                SELECT token,provider,operation,period,maximum,used FROM grants_backup""")
            db.execute("DROP TABLE grants_backup")
        restarted = DurableCostGate(self.path)
        with self.assertRaises(PaymentDenied):
            restarted.reserve("provider", "image", "2", token=token)

    def test_pending_and_spent_are_separate_and_persist(self):
        first = self.reserve()
        second = self.reserve()
        self.gate.settle(first, "1.25")
        snapshot = DurableCostGate(self.path).usage()
        self.assertEqual(snapshot["spent_usd"], 1.25)
        self.assertEqual(snapshot["pending_usd"], 2)
        self.assertEqual(snapshot["committed_usd"], 3.25)
        self.assertEqual(snapshot["available_usd"], 16.75)
        self.gate.reconcile(second, "0", evidence="invoice-no-charge-2", confirmed_by_user=True)
        self.assertEqual(DurableCostGate(self.path).usage()["pending_usd"], 0)

    def test_reconciliation_requires_user_and_invoice(self):
        reservation = self.reserve()
        with self.assertRaises(PaymentDenied):
            self.gate.reconcile(reservation, "0", evidence="invoice-1")
        with self.assertRaises(PaymentDenied):
            self.gate.reconcile(reservation, "0", evidence="", confirmed_by_user=True)
        self.assertEqual(self.gate.usage()["pending_usd"], 2)

    def test_overcharge_freeze_and_audited_unfreeze(self):
        reservation = self.reserve()
        self.gate.reconcile(reservation, "3", evidence="invoice-overage", confirmed_by_user=True)
        self.assertTrue(DurableCostGate(self.path).usage()["paid_calls_frozen"])
        self.assertEqual(self.gate.usage()["spent_usd"], 3)
        with self.assertRaises(PaymentDenied):
            self.gate.clear_paid_hold(evidence="reviewed-invoice")
        self.gate.clear_paid_hold(evidence="reviewed-invoice", confirmed_by_user=True)
        self.assertFalse(DurableCostGate(self.path).usage()["paid_calls_frozen"])
        with sqlite3.connect(self.path) as db:
            events = db.execute("SELECT event FROM cost_audit ORDER BY rowid").fetchall()
        self.assertEqual(events, [("reconcile",), ("clear_hold",)])

    def test_unfreeze_denied_while_any_reservation_unresolved(self):
        pending = self.reserve()
        first = self.reserve()
        self.gate.reconcile(first, "3", evidence="invoice-1", confirmed_by_user=True)
        with self.assertRaises(PaymentDenied):
            self.gate.clear_paid_hold(evidence="invoice-1", confirmed_by_user=True)
        self.gate.reconcile(pending, "0", evidence="invoice-2", confirmed_by_user=True)
        self.gate.clear_paid_hold(evidence="invoice-1-and-2", confirmed_by_user=True)
        self.assertFalse(self.gate.usage()["paid_calls_frozen"])

    def test_reconcile_replay_does_not_change_balance_or_audit(self):
        reservation = self.reserve()
        self.gate.reconcile(reservation, "1.5", evidence="invoice-1", confirmed_by_user=True)
        with self.assertRaises(PaymentDenied):
            self.gate.reconcile(reservation, "0", evidence="invoice-1", confirmed_by_user=True)
        self.assertEqual(self.gate.usage()["spent_usd"], 1.5)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM cost_audit").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
