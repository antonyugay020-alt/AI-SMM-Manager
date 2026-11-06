"""Durable fail-closed payment authorization for the shared AI provider dispatcher.

All paid adapters MUST reserve before network I/O and settle after a confirmed
provider charge. Never release a reservation after an ambiguous network failure:
the charge may have succeeded. Reconcile it manually/provider-side instead.
"""
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from time import time

MAX_SQLITE_INT = (1 << 63) - 1
from uuid import uuid4
from cost_authorization import PaymentDenied, money

SCALE = Decimal("1000000")


def units(value):
    amount = money(value) * SCALE
    if amount != amount.to_integral_value():
        raise PaymentDenied("Price precision exceeds micro-USD")
    if amount > MAX_SQLITE_INT:
        raise PaymentDenied("Price exceeds safe SQLite range")
    return int(amount)


class DurableCostGate:
    def __init__(self, database_path):
        self.database_path = str(database_path)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cost_settings (
                    id INTEGER PRIMARY KEY CHECK(id=1), cap INTEGER NOT NULL CHECK(cap>=0),
                    hold INTEGER NOT NULL DEFAULT 0 CHECK(hold IN (0,1))
                );
                INSERT OR IGNORE INTO cost_settings(id,cap) VALUES(1,20000000);
                CREATE TABLE IF NOT EXISTS cost_grants (
                    token TEXT PRIMARY KEY, provider TEXT NOT NULL,
                    operation TEXT NOT NULL, period TEXT NOT NULL,
                    maximum INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0,
                    expires_at INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS cost_reservations (
                    id TEXT PRIMARY KEY, period TEXT NOT NULL,
                    amount INTEGER NOT NULL, actual INTEGER,
                    state TEXT NOT NULL CHECK(state IN ('reserved','settled','released'))
                );
            """)
            # Preserve the budget of pre-hold installations during migration.
            if "hold" not in {row[1] for row in db.execute("PRAGMA table_info(cost_settings)")}:
                db.execute("ALTER TABLE cost_settings ADD COLUMN hold INTEGER NOT NULL DEFAULT 0 CHECK(hold IN (0,1))")
            # Existing unused grants are intentionally invalidated on upgrade.
            if "expires_at" not in {row[1] for row in db.execute("PRAGMA table_info(cost_grants)")}:
                db.execute("ALTER TABLE cost_grants ADD COLUMN expires_at INTEGER NOT NULL DEFAULT 0")
            db.execute("""CREATE TABLE IF NOT EXISTS cost_audit (
                id TEXT PRIMARY KEY, event TEXT NOT NULL,
                reservation_id TEXT, evidence TEXT NOT NULL,
                created_utc TEXT NOT NULL
            )""")

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
                "INSERT INTO cost_grants(token,provider,operation,period,maximum,expires_at) VALUES(?,?,?,?,?,?)",
                (token, provider, operation, self.period(), units(max_usd), int(time()) + 600))
        return token

    def reserve(self, provider, operation, estimate_usd, *, token=None):
        estimate = units(estimate_usd)
        if estimate <= 0:
            raise PaymentDenied("A paid operation needs a positive verified maximum quote")
        period = self.period()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                grant = db.execute(
                    "SELECT maximum FROM cost_grants WHERE token=? AND provider=? AND operation=? AND period=? AND used=0 AND expires_at>?",
                    (token, provider, operation, period, int(time()))).fetchone()
                if grant is None or estimate > grant[0]:
                    raise PaymentDenied("Explicit matching approval required")
                spent = db.execute(
                    "SELECT COALESCE(SUM(CASE WHEN state='settled' THEN actual ELSE amount END),0) "
                    "FROM cost_reservations WHERE period=? AND state!='released'", (period,)).fetchone()[0]
                cap, hold = db.execute("SELECT cap,hold FROM cost_settings WHERE id=1").fetchone()
                if hold:
                    raise PaymentDenied("Paid calls frozen: reconcile unexpected provider charge")
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
                if row is None or row[1] != "reserved":
                    raise PaymentDenied("Invalid settlement; reconcile provider charge")
                overage = actual > row[0]
                # Persist the real charge even if it violates the upper bound.
                db.execute("UPDATE cost_reservations SET state='settled',actual=? WHERE id=?",
                           (actual, reservation))
                if overage:
                    db.execute("UPDATE cost_settings SET hold=1 WHERE id=1")
                db.commit()
                if overage:
                    raise PaymentDenied("Provider charged above verified quote; actual charge recorded and paid calls frozen")
            except BaseException:
                db.rollback()
                raise


    @staticmethod
    def _proof(evidence, confirmed_by_user):
        if confirmed_by_user is not True:
            raise PaymentDenied("Explicit user confirmation required for reconciliation")
        reference = str(evidence or "").strip()
        if not reference or len(reference) > 256:
            raise PaymentDenied("Provider invoice or reconciliation reference required")
        return reference

    def reconcile(self, reservation, actual_usd, *, evidence, confirmed_by_user=False):
        """Manually settle an uncertain call using verified provider evidence.

        Zero means a confirmed non-charge, not an automatic timeout release.
        Reconciliation is durable and cannot be repeated for the same call.
        """
        reference = self._proof(evidence, confirmed_by_user)
        actual = units(actual_usd)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT amount,state FROM cost_reservations WHERE id=?",
                                 (reservation,)).fetchone()
                if row is None or row[1] != "reserved":
                    raise PaymentDenied("Reservation not awaiting reconciliation")
                state = "settled" if actual else "released"
                db.execute("UPDATE cost_reservations SET state=?,actual=? WHERE id=?",
                           (state, actual, reservation))
                if actual > row[0]:
                    db.execute("UPDATE cost_settings SET hold=1 WHERE id=1")
                db.execute(
                    "INSERT INTO cost_audit(id,event,reservation_id,evidence,created_utc) VALUES(?,?,?,?,?)",
                    (uuid4().hex, "reconcile", reservation, reference,
                     datetime.now(timezone.utc).isoformat()))
                db.commit()
                return state
            except BaseException:
                db.rollback()
                raise

    def clear_paid_hold(self, *, evidence, confirmed_by_user=False):
        """Explicitly clear an overcharge hold only after all uncertain calls are resolved."""
        reference = self._proof(evidence, confirmed_by_user)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                outstanding = db.execute(
                    "SELECT COUNT(*) FROM cost_reservations WHERE state='reserved'").fetchone()[0]
                if outstanding:
                    raise PaymentDenied("Resolve all uncertain paid calls before unfreezing")
                db.execute("UPDATE cost_settings SET hold=0 WHERE id=1")
                db.execute(
                    "INSERT INTO cost_audit(id,event,reservation_id,evidence,created_utc) VALUES(?,?,?,?,?)",
                    (uuid4().hex, "clear_hold", None, reference,
                     datetime.now(timezone.utc).isoformat()))
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def usage(self, period=None):
        period = period or self.period()
        with self._connect() as db:
            db.execute("BEGIN")
            try:
                cap, hold = db.execute("SELECT cap,hold FROM cost_settings WHERE id=1").fetchone()
                used, spent, pending = db.execute(
                    "SELECT COALESCE(SUM(CASE WHEN state='settled' THEN actual "
                    "WHEN state='reserved' THEN amount ELSE 0 END),0), "
                    "COALESCE(SUM(CASE WHEN state='settled' THEN actual ELSE 0 END),0), "
                    "COALESCE(SUM(CASE WHEN state='reserved' THEN amount ELSE 0 END),0) "
                    "FROM cost_reservations WHERE period=?", (period,)).fetchone()
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return {"cap_usd": Decimal(cap)/SCALE, "committed_usd": Decimal(used)/SCALE,
                "available_usd": max(Decimal(0), Decimal(cap-used)/SCALE),
                "paid_calls_frozen": bool(hold),
                "spent_usd": Decimal(spent)/SCALE,
                "pending_usd": Decimal(pending)/SCALE}
