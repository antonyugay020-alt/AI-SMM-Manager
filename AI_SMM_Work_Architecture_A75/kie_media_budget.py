"""Durable global monthly USD budget for paid Kie media dispatch.

Quotes are externally verified *before* the user approves a paid generation.
We do not infer Kie provider tariffs. Every paid intent consumes its entire
quoted allocation before network dispatch, including ambiguous outcomes.
"""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone

DEFAULT_MONTHLY_LIMIT_CENTS = 2000


def month_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m")


def install(db: sqlite3.Connection):
    db.execute("""CREATE TABLE IF NOT EXISTS kie_media_budget (
        id INTEGER PRIMARY KEY CHECK(id=1),
        monthly_limit_cents INTEGER NOT NULL CHECK(monthly_limit_cents>=0)
    )""")
    db.execute("""INSERT OR IGNORE INTO kie_media_budget
        (id,monthly_limit_cents) VALUES (1,?)""",
        (DEFAULT_MONTHLY_LIMIT_CENTS,))
    db.execute("""CREATE TABLE IF NOT EXISTS kie_media_allocations (
        task_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        month_utc TEXT NOT NULL,
        quote_cents INTEGER NOT NULL CHECK(quote_cents>0),
        created_at TEXT NOT NULL
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS kie_media_allocations_month
        ON kie_media_allocations(month_utc)""")


def _valid_cents(value, *, zero=False):
    if type(value) is not int or value < (0 if zero else 1) or value > 10_000_000:
        raise ValueError("invalid USD cents amount")
    return value


def set_monthly_limit(db, amount_cents):
    """Change the global cap; never erase past paid allocations."""
    _valid_cents(amount_cents, zero=True)
    if db.in_transaction:
        raise RuntimeError("cannot change media budget inside another transaction")
    try:
        db.execute("BEGIN IMMEDIATE")
        install(db)
        db.execute("UPDATE kie_media_budget SET monthly_limit_cents=? WHERE id=1",
                   (amount_cents,))
        db.commit()
    except BaseException:
        db.rollback()
        raise


def snapshot(db, *, month=None):
    if db.in_transaction:
        raise RuntimeError("cannot inspect media budget in another transaction")
    key=month or month_utc()
    if not isinstance(key,str) or len(key)!=7 or key[4]!="-":
        raise ValueError("invalid UTC budget month")
    install(db)
    db.commit()
    limit=db.execute("SELECT monthly_limit_cents FROM kie_media_budget WHERE id=1").fetchone()[0]
    spent=db.execute("""SELECT COALESCE(SUM(quote_cents),0) FROM kie_media_allocations
        WHERE month_utc=?""",(key,)).fetchone()[0]
    return {"month_utc":key,"limit_cents":limit,"allocated_cents":spent,
            "remaining_cents":max(0,limit-spent)}


def reserve_in_transaction(db, *, project_id, task_id, quote_cents):
    """Call only within the SAME BEGIN IMMEDIATE as prepared->submitting.

    Even a timeout or process death after dispatch keeps the allocation.
    """
    if not db.in_transaction:
        raise RuntimeError("budget allocation requires caller-owned transaction")
    amount=_valid_cents(quote_cents)
    install(db)
    if db.execute("SELECT 1 FROM kie_media_allocations WHERE task_id=?",
                  (task_id,)).fetchone():
        raise RuntimeError("media task budget already allocated")
    key=month_utc()
    limit=db.execute("SELECT monthly_limit_cents FROM kie_media_budget WHERE id=1").fetchone()[0]
    allocated=db.execute("""SELECT COALESCE(SUM(quote_cents),0) FROM kie_media_allocations
        WHERE month_utc=?""",(key,)).fetchone()[0]
    if amount>limit-allocated:
        raise RuntimeError("Kie media monthly USD budget exceeded")
    db.execute("""INSERT INTO kie_media_allocations
        (task_id,project_id,month_utc,quote_cents,created_at)
        VALUES (?,?,?,?,?)""",
        (task_id,project_id,key,amount,datetime.now(timezone.utc).isoformat()))
