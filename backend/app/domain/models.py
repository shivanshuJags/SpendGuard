"""Domain entities. Plain data, no I/O."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .money import Money


class Outcome(StrEnum):
    # Ordered from least to most strict; the engine keeps the strictest.
    APPROVE = "approve"
    ESCALATE = "escalate"
    DENY = "deny"

    @property
    def severity(self) -> int:
        return list(Outcome).index(self)


@dataclass(frozen=True, slots=True)
class PaymentRequest:
    """What the agent asks for. Everything here came from the agent: untrusted."""

    item: str
    category: str
    merchant_id: str  # the payee: a PayPal merchant email in sandbox
    amount: Money
    reason: str


@dataclass(frozen=True, slots=True)
class Allowance:
    """The owner's rules for one agent. Owner-authored: trusted."""

    id: str
    budget: Money
    per_purchase_limit: Money  # hard cap: above this is denied
    approval_threshold: Money  # at or above this the owner must approve
    expires_at: datetime
    spent: Money  # captured payments
    reserved: Money  # escalations waiting for the owner
    merchant_allowlist: frozenset[str] | None = None  # None = any merchant
    merchant_denylist: frozenset[str] = frozenset()
    blocked_categories: frozenset[str] = frozenset()
    max_purchases_per_hour: int | None = None
    frozen: bool = False

    @property
    def remaining(self) -> Money:
        return self.budget - self.spent - self.reserved


@dataclass(frozen=True, slots=True)
class RuleResult:
    rule_id: str
    outcome: Outcome
    reason: str


@dataclass(frozen=True, slots=True)
class Decision:
    outcome: Outcome
    triggered: tuple[RuleResult, ...] = field(default=())

    @property
    def reasons(self) -> list[str]:
        return [result.reason for result in self.triggered]
