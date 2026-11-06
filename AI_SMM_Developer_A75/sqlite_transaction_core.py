"""Shared SQLite transaction boundary for application services.

``compose`` is a savepoint: it may participate in a transaction already owned
by a higher-level operation, but must never commit that transaction.
``own`` is the final durable boundary: it refuses an unrelated transaction and
commits once, after all composed operations have succeeded.

The boundary is independent of Qt, models, workflows and filesystem effects.
"""
from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4
import sqlite3


def _connection(db):
    if not isinstance(db, sqlite3.Connection):
        raise TypeError('A SQLite connection is required')


@contextmanager
def compose(db, label='app'):
    """Rollback just this operation on failure; outer owner decides commit."""
    _connection(db)
    if not isinstance(label, str) or not label.replace('_', '').isalnum():
        raise ValueError('Invalid savepoint label')
    point = 'uow_' + label + '_' + uuid4().hex
    db.execute('SAVEPOINT ' + point)
    try:
        yield
    except BaseException:
        db.execute('ROLLBACK TO SAVEPOINT ' + point)
        db.execute('RELEASE SAVEPOINT ' + point)
        raise
    else:
        db.execute('RELEASE SAVEPOINT ' + point)


@contextmanager
def own(db):
    """Own one durable transaction, never commit or rollback a caller's work."""
    _connection(db)
    if db.in_transaction:
        raise RuntimeError('A different operation already owns this transaction')
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        if not db.in_transaction:
            raise RuntimeError('Transaction ended before the application boundary')
    except BaseException:
        if db.in_transaction:
            db.rollback()
        raise
    else:
        db.commit()
