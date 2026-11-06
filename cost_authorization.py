"""Fail-closed authorization and monthly reservation ledger for paid AI calls.

Integrate this gate at the shared provider dispatch boundary. A UI confirmation
alone is not authorization: dispatch must call reserve() with a single-use grant.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from threading import RLock
from uuid import uuid4


class PaymentDenied(PermissionError):
    pass


def money(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise PaymentDenied("Invalid price")
    if not result.is_finite() or result < 0:
        raise PaymentDenied("Invalid price")
    return result


@dataclass(frozen=True)
class Grant:
    token: str
    provider: str
    operation: str
    max_usd: Decimal
    month: str


class CostGate:
    """In-process reference gate; production must persist ledger atomically.

    Grants never authorize an unknown price, another provider/operation, or
    spending above the monthly cap. No grant means no paid dispatch.
    """
    def __init__(self, monthly_cap_usd="20"):
        self.cap = money(monthly_cap_usd)
        self._lock = RLock()
        self._grants = {}
        self._reserved = {}
        self._spent = {}

    @staticmethod
    def month():
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def approve(self, provider, operation, max_usd, *, month=None):
        if not provider or not operation:
            raise PaymentDenied("Missing provider or operation")
        maximum = money(max_usd)
        period = month or self.month()
        grant = Grant(uuid4().hex, provider, operation, maximum, period)
        with self._lock:
            self._grants[grant.token] = grant
        return grant.token

    def reserve(self, provider, operation, estimate_usd, *, token=None, month=None):
        estimate = money(estimate_usd)
        period = month or self.month()
        with self._lock:
            grant = self._grants.get(token)
            if not grant or grant.month != period or grant.provider != provider or grant.operation != operation:
                raise PaymentDenied("Explicit per-operation approval required")
            if estimate > grant.max_usd:
                raise PaymentDenied("Estimate exceeds approval")
            used = self._spent.get(period, Decimal("0")) + self._reserved.get(period, Decimal("0"))
            if used + estimate > self.cap:
                raise PaymentDenied("Monthly spending limit exceeded")
            self._grants.pop(token)
            self._reserved[period] = self._reserved.get(period, Decimal("0")) + estimate
            return (period, estimate)

    def settle(self, reservation, actual_usd):
        period, estimate = reservation
        actual = money(actual_usd)
        with self._lock:
            if actual > estimate:
                raise PaymentDenied("Actual cost exceeds reserved amount; stop further calls")
            if self._reserved.get(period, Decimal("0")) < estimate:
                raise PaymentDenied("Reservation already settled")
            self._reserved[period] -= estimate
            self._spent[period] = self._spent.get(period, Decimal("0")) + actual

    def release(self, reservation):
        period, estimate = reservation
        with self._lock:
            if self._reserved.get(period, Decimal("0")) < estimate:
                raise PaymentDenied("Reservation already released")
            self._reserved[period] -= estimate
