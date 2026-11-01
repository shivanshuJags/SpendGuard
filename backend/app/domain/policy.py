"""Policy engine: decides APPROVE / ESCALATE / DENY for a payment request.

Design:
- Each rule is a small pure function returning a RuleResult when it triggers,
  or None when it has nothing to say.
- Every rule runs; the strictest outcome wins (DENY > ESCALATE > APPROVE), and
  all triggered rules are kept so the audit log can explain the decision.
- Fail closed: a rule that crashes produces DENY, never a silent approval.
- No LLM, no network, no clock: `now` and payment history are passed in, so
  every decision is reproducible in a test.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import Allowance, Decision, Outcome, PaymentRequest, RuleResult


@dataclass(frozen=True, slots=True)
class PolicyContext:
    now: datetime
    recent_payment_times: Sequence[datetime] = ()


Rule = Callable[[PaymentRequest, Allowance, PolicyContext], RuleResult | None]


# Gate rules: if one triggers, the request is malformed or the allowance is
# unusable, and the remaining rules can't be evaluated meaningfully.


def rule_frozen(req: PaymentRequest, a: Allowance, ctx: PolicyContext) -> RuleResult | None:
    if a.frozen:
        return RuleResult("allowance.frozen", Outcome.DENY, "The owner froze this allowance.")
    return None


def rule_expired(req: PaymentRequest, a: Allowance, ctx: PolicyContext) -> RuleResult | None:
    if ctx.now >= a.expires_at:
        return RuleResult("allowance.expired", Outcome.DENY, "The allowance has expired.")
    return None


def rule_currency(req: PaymentRequest, a: Allowance, ctx: PolicyContext) -> RuleResult | None:
    if req.amount.currency != a.budget.currency:
        return RuleResult(
            "amount.currency",
            Outcome.DENY,
            f"Allowance is in {a.budget.currency}, request is in {req.amount.currency}.",
        )
    return None


def rule_positive(req: PaymentRequest, a: Allowance, ctx: PolicyContext) -> RuleResult | None:
    if not req.amount.is_positive:
        return RuleResult("amount.positive", Outcome.DENY, "Amount must be greater than zero.")
    return None


# Limit rules.


def rule_merchant_denylist(
    req: PaymentRequest, a: Allowance, ctx: PolicyContext
) -> RuleResult | None:
    if _norm(req.merchant_id) in {_norm(m) for m in a.merchant_denylist}:
        return RuleResult(
            "merchant.denylist", Outcome.DENY, f"Merchant {req.merchant_id} is blocked."
        )
    return None


def rule_category(req: PaymentRequest, a: Allowance, ctx: PolicyContext) -> RuleResult | None:
    if _norm(req.category) in {_norm(c) for c in a.blocked_categories}:
        return RuleResult(
            "category.blocked", Outcome.DENY, f"Category '{req.category}' is blocked."
        )
    return None


def rule_budget(req: PaymentRequest, a: Allowance, ctx: PolicyContext) -> RuleResult | None:
    if req.amount > a.remaining:
        return RuleResult(
            "budget.remaining",
            Outcome.DENY,
            f"{req.amount} exceeds the remaining budget of {a.remaining}.",
        )
    return None


def rule_per_purchase(req: PaymentRequest, a: Allowance, ctx: PolicyContext) -> RuleResult | None:
    if req.amount > a.per_purchase_limit:
        return RuleResult(
            "limit.per_purchase",
            Outcome.DENY,
            f"{req.amount} exceeds the per-purchase limit of {a.per_purchase_limit}.",
        )
    return None


def rule_velocity(req: PaymentRequest, a: Allowance, ctx: PolicyContext) -> RuleResult | None:
    if a.max_purchases_per_hour is None:
        return None
    window_start = ctx.now - timedelta(hours=1)
    count = sum(1 for t in ctx.recent_payment_times if t > window_start)
    if count >= a.max_purchases_per_hour:
        return RuleResult(
            "limit.velocity",
            Outcome.DENY,
            f"{count} purchases in the last hour; the limit is {a.max_purchases_per_hour}.",
        )
    return None


def rule_merchant_allowlist(
    req: PaymentRequest, a: Allowance, ctx: PolicyContext
) -> RuleResult | None:
    if a.merchant_allowlist is None:
        return None
    if _norm(req.merchant_id) not in {_norm(m) for m in a.merchant_allowlist}:
        return RuleResult(
            "merchant.unknown",
            Outcome.ESCALATE,
            f"Merchant {req.merchant_id} is not on the allowlist; owner must approve.",
        )
    return None


def rule_approval_threshold(
    req: PaymentRequest, a: Allowance, ctx: PolicyContext
) -> RuleResult | None:
    if req.amount >= a.approval_threshold:
        return RuleResult(
            "limit.approval_threshold",
            Outcome.ESCALATE,
            f"{req.amount} is at or above the approval threshold of {a.approval_threshold}.",
        )
    return None


GATE_RULES: tuple[Rule, ...] = (rule_frozen, rule_expired, rule_currency, rule_positive)
LIMIT_RULES: tuple[Rule, ...] = (
    rule_merchant_denylist,
    rule_category,
    rule_budget,
    rule_per_purchase,
    rule_velocity,
    rule_merchant_allowlist,
    rule_approval_threshold,
)


def evaluate(req: PaymentRequest, allowance: Allowance, ctx: PolicyContext) -> Decision:
    gate = _run(GATE_RULES, req, allowance, ctx)
    if gate:
        return Decision(Outcome.DENY, tuple(gate))

    triggered = _run(LIMIT_RULES, req, allowance, ctx)
    outcome = max((r.outcome for r in triggered), key=lambda o: o.severity, default=Outcome.APPROVE)
    return Decision(outcome, tuple(triggered))


def _run(
    rules: Sequence[Rule], req: PaymentRequest, a: Allowance, ctx: PolicyContext
) -> list[RuleResult]:
    results = []
    for rule in rules:
        try:
            result = rule(req, a, ctx)
        except Exception as error:  # fail closed
            result = RuleResult(
                f"policy.error.{rule.__name__}",
                Outcome.DENY,
                f"Rule {rule.__name__} failed ({type(error).__name__}); denying to be safe.",
            )
        if result is not None:
            results.append(result)
    return results


def _norm(value: str) -> str:
    return value.strip().casefold()
