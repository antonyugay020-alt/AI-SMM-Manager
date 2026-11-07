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


    def test_concurrent_schema_migration_preserves_legacy_grants_and_budget(self):
        from concurrent.futures import ThreadPoolExecutor
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE cost_audit")
            db.execute("DROP TABLE cost_reservations")
            db.execute("DROP TABLE cost_grants")
            db.execute("DROP TABLE cost_settings")
            db.execute("CREATE TABLE cost_settings(id INTEGER PRIMARY KEY, cap INTEGER NOT NULL)")
            db.execute("INSERT INTO cost_settings VALUES(1, 17500000)")
            db.execute("""CREATE TABLE cost_grants(
                token TEXT PRIMARY KEY, provider TEXT NOT NULL, operation TEXT NOT NULL,
                period TEXT NOT NULL, maximum INTEGER NOT NULL,
                used INTEGER NOT NULL DEFAULT 0)""")
            db.execute("INSERT INTO cost_grants VALUES('legacy','provider','image','2026-11',1000000,0)")
        with ThreadPoolExecutor(max_workers=8) as pool:
            instances = list(pool.map(lambda _: DurableCostGate(self.path), range(16)))
        self.assertEqual(len(instances), 16)
        self.assertEqual(self.gate.usage()["cap_usd"], 17.5)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute(
                "SELECT expires_at FROM cost_grants WHERE token='legacy'").fetchone(), (0,))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM cost_grants").fetchone()[0], 1)

    def test_schema_upgrade_failure_rolls_back_entire_migration(self):
        from unittest.mock import patch
        class FailGrantMigration(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if "ALTER TABLE cost_grants ADD COLUMN expires_at" in sql:
                    raise sqlite3.OperationalError("injected migration failure")
                return super().execute(sql, parameters)
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE cost_audit")
            db.execute("DROP TABLE cost_reservations")
            db.execute("DROP TABLE cost_grants")
            db.execute("DROP TABLE cost_settings")
            db.execute("CREATE TABLE cost_settings(id INTEGER PRIMARY KEY,cap INTEGER NOT NULL)")
            db.execute("INSERT INTO cost_settings VALUES(1, 9000000)")
            db.execute("""CREATE TABLE cost_grants(
                token TEXT PRIMARY KEY, provider TEXT NOT NULL, operation TEXT NOT NULL,
                period TEXT NOT NULL, maximum INTEGER NOT NULL,
                used INTEGER NOT NULL DEFAULT 0)""")
            db.execute("INSERT INTO cost_grants VALUES('legacy','p','image','2026-11',1000000,0)")
        def failing_connection(gate):
            return sqlite3.connect(gate.database_path, timeout=30, isolation_level=None,
                                   factory=FailGrantMigration)
        with patch.object(DurableCostGate, "_connect", failing_connection):
            with self.assertRaisesRegex(sqlite3.OperationalError, "injected migration"):
                DurableCostGate(self.path)
        with sqlite3.connect(self.path) as db:
            self.assertNotIn("hold", [r[1] for r in db.execute("PRAGMA table_info(cost_settings)")])
            self.assertNotIn("expires_at", [r[1] for r in db.execute("PRAGMA table_info(cost_grants)")])
            self.assertIsNone(db.execute(
                "SELECT name FROM sqlite_master WHERE name='cost_reservations'").fetchone())
            self.assertEqual(db.execute("SELECT cap FROM cost_settings").fetchone()[0], 9000000)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM cost_grants").fetchone()[0], 1)
        self.assertEqual(DurableCostGate(self.path).usage()["cap_usd"], 9)

    def test_exact_accounting_when_multiple_provider_overcharges_exceed_sqlite_sum(self):
        from decimal import Decimal
        from durable_cost_gate import MAX_SQLITE_INT, SCALE
        reserve_units = MAX_SQLITE_INT // 4
        reserve_usd = str(Decimal(reserve_units) / SCALE)
        overcharge_usd = str(Decimal(MAX_SQLITE_INT) / SCALE)
        self.gate.set_monthly_cap(overcharge_usd)
        first = self.reserve(reserve_usd)
        second = self.reserve(reserve_usd)
        self.gate.reconcile(first, overcharge_usd,
                            evidence="provider-invoice-1", confirmed_by_user=True)
        self.gate.reconcile(second, overcharge_usd,
                            evidence="provider-invoice-2", confirmed_by_user=True)
        snapshot = DurableCostGate(self.path).usage()
        self.assertEqual(snapshot["spent_usd"], Decimal(2 * MAX_SQLITE_INT) / SCALE)
        self.assertTrue(snapshot["paid_calls_frozen"])
        self.assertEqual(snapshot["available_usd"], 0)
        with self.assertRaises(PaymentDenied):
            self.gate.reserve("provider", "image", "1",
                              token=self.gate.approve("provider", "image", "1"))

    def test_concurrent_paid_reservations_never_exceed_cap(self):
        from concurrent.futures import ThreadPoolExecutor
        self.gate.set_monthly_cap("3")
        tokens = [self.gate.approve("provider", "image", "2") for _ in range(12)]
        def reserve(token):
            try:
                DurableCostGate(self.path).reserve("provider", "image", "2", token=token)
                return True
            except PaymentDenied:
                return False
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(reserve, tokens))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.gate.usage()["committed_usd"], 2)



if __name__ == "__main__":
    unittest.main()
