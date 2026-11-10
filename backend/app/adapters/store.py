"""SQLite persistence: wallet, allowances, payment requests, audit log.

- Amounts are TEXT ("12.50"): SQLite REAL is a float.
- Spent and reserved are computed from payment rows, never stored as counters
  that could drift from the rows they summarize.
- audit_event is append-only: there is no update or delete method for it.
- One connection, shared by FastAPI's worker threads (plain `def` routes run
  in a thread pool). sqlite3 connections are not safe for concurrent use, so
  every method holds the store's lock; the store owns its connection, so it
  owns access to it too.
"""

import functools
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.domain.models import Allowance, Decision, PaymentRequest, PaymentStatus
from app.domain.money import Money

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallet (
    owner_id     TEXT PRIMARY KEY,
    vault_id     TEXT NOT NULL,
    payer_email  TEXT,
    linked_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS allowance (
    id                     TEXT PRIMARY KEY,
    currency               TEXT NOT NULL,
    budget                 TEXT NOT NULL,
    per_purchase_limit     TEXT NOT NULL,
    approval_threshold     TEXT NOT NULL,
    expires_at             TEXT NOT NULL,
    merchant_allowlist     TEXT,          -- JSON list, NULL = any merchant
    merchant_denylist      TEXT NOT NULL, -- JSON list
    blocked_categories     TEXT NOT NULL, -- JSON list
    max_purchases_per_hour INTEGER,
    frozen                 INTEGER NOT NULL DEFAULT 0,
    created_at             TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payment (
    id               TEXT PRIMARY KEY,   -- also the PayPal-Request-Id
    allowance_id     TEXT NOT NULL REFERENCES allowance(id),
    item             TEXT NOT NULL,
    category         TEXT NOT NULL,
    merchant_id      TEXT NOT NULL,
    amount           TEXT NOT NULL,
    currency         TEXT NOT NULL,
    reason           TEXT NOT NULL,
    status           TEXT NOT NULL,
    outcome          TEXT NOT NULL,
    rules            TEXT NOT NULL,      -- JSON list of {rule_id, outcome, reason}
    paypal_order_id  TEXT,
    capture_id       TEXT,
    approve_url      TEXT,
    error            TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS payment_by_allowance ON payment(allowance_id, status);
CREATE TABLE IF NOT EXISTS audit_event (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    payment_id  TEXT,
    kind        TEXT NOT NULL,
    data        TEXT NOT NULL
);
"""


# Statuses whose amount is committed against the budget.
_SPENT = (PaymentStatus.PAID,)
# UNKNOWN is reserved too: money may have moved, so don't spend it twice.
_RESERVED = (PaymentStatus.PENDING_OWNER, PaymentStatus.UNKNOWN)
# Statuses that count as a purchase attempt for the velocity limit.
_ATTEMPTED = (
    PaymentStatus.PAID,
    PaymentStatus.PENDING_OWNER,
    PaymentStatus.UNKNOWN,
    PaymentStatus.REFUNDED,
)


@dataclass(frozen=True, slots=True)
class Wallet:
    vault_id: str
    payer_email: str | None
    linked_at: datetime


@dataclass(frozen=True, slots=True)
class PaymentRecord:
    id: str
    allowance_id: str
    request: PaymentRequest
    status: PaymentStatus
    outcome: str
    rules: list[dict[str, str]]
    paypal_order_id: str | None
    capture_id: str | None
    approve_url: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


def _serialized(method):
    """Run the method while holding the store's (re-entrant) lock."""

    @functools.wraps(method)
    def locked(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return locked


def utcnow() -> datetime:
    return datetime.now(UTC)


class Store:
    def __init__(self, path: str | Path = "spendguard.db"):
        # check_same_thread=False only disables sqlite3's guard; _lock replaces it.
        self._lock = threading.RLock()  # re-entrant: get_allowance calls _sum
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)

    @_serialized
    def close(self) -> None:
        self._db.close()

    # ---- wallet -----------------------------------------------------------

    @_serialized
    def get_wallet(self, owner_id: str = "owner") -> Wallet | None:
        row = self._db.execute("SELECT * FROM wallet WHERE owner_id = ?", (owner_id,)).fetchone()
        if row is None:
            return None
        return Wallet(row["vault_id"], row["payer_email"], datetime.fromisoformat(row["linked_at"]))

    @_serialized
    def save_wallet(self, vault_id: str, payer_email: str | None, owner_id: str = "owner") -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO wallet VALUES (?, ?, ?, ?)",
                (owner_id, vault_id, payer_email, utcnow().isoformat()),
            )

    @_serialized
    def delete_wallet(self, owner_id: str = "owner") -> None:
        with self._db:
            self._db.execute("DELETE FROM wallet WHERE owner_id = ?", (owner_id,))

    # ---- allowances -------------------------------------------------------

    @_serialized
    def create_allowance(self, a: Allowance) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO allowance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    a.id,
                    a.budget.currency,
                    str(a.budget.amount),
                    str(a.per_purchase_limit.amount),
                    str(a.approval_threshold.amount),
                    a.expires_at.isoformat(),
                    None if a.merchant_allowlist is None else json.dumps(sorted(a.merchant_allowlist)),
                    json.dumps(sorted(a.merchant_denylist)),
                    json.dumps(sorted(a.blocked_categories)),
                    a.max_purchases_per_hour,
                    int(a.frozen),
                    utcnow().isoformat(),
                ),
            )

    @_serialized
    def set_frozen(self, allowance_id: str, frozen: bool) -> None:
        with self._db:
            self._db.execute(
                "UPDATE allowance SET frozen = ? WHERE id = ?", (int(frozen), allowance_id)
            )

    @_serialized
    def list_allowance_ids(self) -> list[str]:
        rows = self._db.execute("SELECT id FROM allowance ORDER BY created_at DESC")
        return [r["id"] for r in rows]

    @_serialized
    def get_allowance(self, allowance_id: str) -> Allowance | None:
        row = self._db.execute("SELECT * FROM allowance WHERE id = ?", (allowance_id,)).fetchone()
        if row is None:
            return None
        currency = row["currency"]
        allowlist = row["merchant_allowlist"]
        return Allowance(
            id=row["id"],
            budget=Money.of(row["budget"], currency),
            per_purchase_limit=Money.of(row["per_purchase_limit"], currency),
            approval_threshold=Money.of(row["approval_threshold"], currency),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            spent=self._sum(allowance_id, currency, _SPENT),
            reserved=self._sum(allowance_id, currency, _RESERVED),
            merchant_allowlist=None if allowlist is None else frozenset(json.loads(allowlist)),
            merchant_denylist=frozenset(json.loads(row["merchant_denylist"])),
            blocked_categories=frozenset(json.loads(row["blocked_categories"])),
            max_purchases_per_hour=row["max_purchases_per_hour"],
            frozen=bool(row["frozen"]),
        )

    @_serialized
    def _sum(self, allowance_id: str, currency: str, statuses: tuple[PaymentStatus, ...]) -> Money:
        marks = ",".join("?" * len(statuses))
        rows = self._db.execute(
            f"SELECT amount FROM payment WHERE allowance_id = ? AND currency = ? "
            f"AND status IN ({marks})",
            (allowance_id, currency, *statuses),
        )
        # Sum in Decimal, in Python: SQL SUM() on TEXT would convert to float.
        return Money(sum((Decimal(r["amount"]) for r in rows), Decimal(0)), currency)

    @_serialized
    def recent_attempt_times(self, allowance_id: str, since: datetime) -> list[datetime]:
        marks = ",".join("?" * len(_ATTEMPTED))
        rows = self._db.execute(
            f"SELECT created_at FROM payment WHERE allowance_id = ? AND created_at > ? "
            f"AND status IN ({marks})",
            (allowance_id, since.isoformat(), *_ATTEMPTED),
        )
        return [datetime.fromisoformat(r["created_at"]) for r in rows]

    # ---- payments ---------------------------------------------------------

    @_serialized
    def insert_payment(
        self,
        payment_id: str,
        allowance_id: str,
        req: PaymentRequest,
        decision: Decision,
        status: PaymentStatus,
        now: datetime,
    ) -> None:
        rules = [
            {"rule_id": r.rule_id, "outcome": r.outcome.value, "reason": r.reason}
            for r in decision.triggered
        ]
        with self._db:
            self._db.execute(
                "INSERT INTO payment (id, allowance_id, item, category, merchant_id, amount, "
                "currency, reason, status, outcome, rules, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    payment_id,
                    allowance_id,
                    req.item,
                    req.category,
                    req.merchant_id,
                    str(req.amount.amount),
                    req.amount.currency,
                    req.reason,
                    status,
                    decision.outcome.value,
                    json.dumps(rules),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )

    _UPDATABLE = {"status", "paypal_order_id", "capture_id", "approve_url", "error"}

    @_serialized
    def update_payment(self, payment_id: str, **fields: Any) -> None:
        unknown = set(fields) - self._UPDATABLE
        if unknown:
            raise ValueError(f"cannot update {unknown}")
        fields["updated_at"] = utcnow().isoformat()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._db:
            self._db.execute(
                f"UPDATE payment SET {assignments} WHERE id = ?", (*fields.values(), payment_id)
            )

    @_serialized
    def get_payment(self, payment_id: str) -> PaymentRecord | None:
        row = self._db.execute("SELECT * FROM payment WHERE id = ?", (payment_id,)).fetchone()
        return None if row is None else _payment(row)

    @_serialized
    def list_payments(self, allowance_id: str) -> list[PaymentRecord]:
        rows = self._db.execute(
            "SELECT * FROM payment WHERE allowance_id = ? ORDER BY created_at", (allowance_id,)
        )
        return [_payment(r) for r in rows]

    # ---- audit (append-only) ----------------------------------------------

    @_serialized
    def append_audit(self, kind: str, data: dict[str, Any], payment_id: str | None = None) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO audit_event (at, payment_id, kind, data) VALUES (?, ?, ?, ?)",
                (utcnow().isoformat(), payment_id, kind, json.dumps(data, default=str)),
            )

    @_serialized
    def list_audit(self, after_seq: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM audit_event WHERE seq > ? ORDER BY seq LIMIT ?", (after_seq, limit)
        )
        return [
            {
                "seq": r["seq"],
                "at": r["at"],
                "payment_id": r["payment_id"],
                "kind": r["kind"],
                "data": json.loads(r["data"]),
            }
            for r in rows
        ]


def _payment(row: sqlite3.Row) -> PaymentRecord:
    return PaymentRecord(
        id=row["id"],
        allowance_id=row["allowance_id"],
        request=PaymentRequest(
            item=row["item"],
            category=row["category"],
            merchant_id=row["merchant_id"],
            amount=Money.of(row["amount"], row["currency"]),
            reason=row["reason"],
        ),
        status=PaymentStatus(row["status"]),
        outcome=row["outcome"],
        rules=json.loads(row["rules"]),
        paypal_order_id=row["paypal_order_id"],
        capture_id=row["capture_id"],
        approve_url=row["approve_url"],
        error=row["error"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
