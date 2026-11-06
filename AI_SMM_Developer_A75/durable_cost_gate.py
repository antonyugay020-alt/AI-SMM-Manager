"""Durable fail-closed payment authorization for the shared AI provider dispatcher.

All paid adapters MUST reserve before network I/O and settle after a confirmed
provider charge. Never release a reservation after an ambiguous network failure:
the charge may have succeeded. Reconcile it manually/provider-side instead.
"""
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
from cost_authorization import PaymentDenied, money

SCALE = Decimal("1000000")


def units(value):
    amount = money(value) * SCALE
    if amount != amount.to_integral_value():
        raise PaymentDenied("Price precision exceeds micro-USD")
    return int(amount)


class DurableCostGate:
    def __init__(self, database_path):
        self.database_path = str(database_path)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cost_settings (
                    id INTEGER PRIMARY KEY CHECK(id=1), cap INTEGER NOT NULL CHECK(cap>=0)
                );
                INSERT OR IGNORE INTO cost_settings(id,cap) VALUES(1,20000000);
                CREATE TABLE IF NOT EXISTS cost_grants (
                    token TEXT PRIMARY KEY, provider TEXT NOT NULL,
                    operation TEXT NOT NULL, period TEXT NOT NULL,
                    maximum INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS cost_reservations (
                    id TEXT PRIMARY KEY, period TEXT NOT NULL,
                    amount INTEGER NOT NULL, actual INTEGER,
                    state TEXT NOT NULL CHECK(state IN ('reserved','settled','released'))
                );
            """)

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        db.execute("PRAGMA busy_timeout=30000")
        return db

    @staticmethod
    def period():
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def set_monthly_cap(self, amount_usd):
        cap = units(amount_usd)
        with self._connect() as db:
            db.execute("UPDATE cost_settings SET cap=? WHERE id=1", (cap,))

    def approve(self, provider, operation, max_usd):
        if not provider or not operation:
            raise PaymentDenied("Missing provider or operation")
        token = uuid4().hex
        with self._connect() as db:
            db.execute(
                "INSERT INTO cost_grants(token,provider,operation,period,maximum) VALUES(?,?,?,?,?)",
                (token, provider, operation, self.period(), units(max_usd)))
        return token

    def reserve(self, provider, operation, estimate_usd, *, token=None):
        estimate = units(estimate_usd)
        period = self.period()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                grant = db.execute(
                    "SELECT maximum FROM cost_grants WHERE token=? AND provider=? AND operation=? AND period=? AND used=0",
                    (token, provider, operation, period)).fetchone()
                if grant is None or estimate > grant[0]:
                    raise PaymentDenied("Explicit matching approval required")
                spent = db.execute(
                    "SELECT COALESCE(SUM(CASE WHEN state='settled' THEN actual ELSE amount END),0) "
                    "FROM cost_reservations WHERE period=? AND state!='released'", (period,)).fetchone()[0]
                cap = db.execute("SELECT cap FROM cost_settings WHERE id=1").fetchone()[0]
                if spent + estimate > cap:
                    raise PaymentDenied("Monthly limit exceeded")
                reservation = uuid4().hex
                db.execute("INSERT INTO cost_reservations(id,period,amount,state) VALUES(?,?,?,'reserved')",
                           (reservation, period, estimate))
                db.execute("UPDATE cost_grants SET used=1 WHERE token=?", (token,))
                db.commit()
                return reservation
            except BaseException:
                db.rollback()
                raise

    def settle(self, reservation, actual_usd):
        actual = units(actual_usd)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT amount,state FROM cost_reservations WHERE id=?", (reservation,)).fetchone()
                if row is None or row[1] != "reserved" or actual > row[0]:
                    raise PaymentDenied("Invalid settlement; reconcile provider charge")
                db.execute("UPDATE cost_reservations SET state='settled',actual=? WHERE id=?",
                           (actual, reservation))
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def usage(self, period=None):
        period = period or self.period()
        with self._connect() as db:
            cap = db.execute("SELECT cap FROM cost_settings WHERE id=1").fetchone()[0]
            used = db.execute(
                "SELECT COALESCE(SUM(CASE WHEN state='settled' THEN actual ELSE amount END),0) "
                "FROM cost_reservations WHERE period=? AND state!='released'", (period,)).fetchone()[0]
        return {"cap_usd": Decimal(cap)/SCALE, "committed_usd": Decimal(used)/SCALE,
                "available_usd": max(Decimal(0), Decimal(cap-used)/SCALE)}
