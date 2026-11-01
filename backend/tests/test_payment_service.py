"""Payment service tests: real policy + real SQLite (in memory) + fake PayPal.

The fake lets us simulate what we can't safely trigger in the sandbox:
timeouts after a charge, 5xx errors, and races between requests.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.store import Store
from app.domain.models import Allowance, PaymentRequest, PaymentStatus
from app.domain.money import Money
from app.services.payments import InvalidState, PaymentService
from app.services.ports import (
    GatewayError,
    GatewayUnreachable,
    OrderResult,
    PaymentToken,
    RefundResult,
    SetupToken,
)

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
SHOP = "shop@example.com"
USD = lambda v: Money.of(v, "USD")  # noqa: E731


@dataclass
class FakePayPal:
    """Behaves like PayPal: idempotent by request_id, scriptable failures."""

    fail_next: list[Exception] = field(default_factory=list)
    orders: dict[str, OrderResult] = field(default_factory=dict)  # by request_id
    charges: list[str] = field(default_factory=list)  # request_ids that moved money
    calls: list[tuple[str, str]] = field(default_factory=list)
    captured: list[str] = field(default_factory=list)
    refunds: list[str] = field(default_factory=list)

    def _maybe_fail(self):
        if self.fail_next:
            raise self.fail_next.pop(0)

    async def create_setup_token(self, return_url, cancel_url):
        return SetupToken("SETUP1", "https://paypal.test/approve-setup")

    async def create_payment_token(self, setup_token_id):
        return PaymentToken("VAULT1", "CUST1", "owner@personal.example.com")

    async def delete_payment_token(self, payment_token_id):
        pass

    async def create_order(self, *, amount, payee_email, description, request_id, vault_id=None,
                           return_url=None, cancel_url=None):
        self.calls.append(("create_order", request_id))
        await asyncio.sleep(0)  # yield like a real network call, so races can happen
        if request_id not in self.orders:  # idempotency: replay returns the original
            if vault_id:
                self.charges.append(request_id)
                order = OrderResult(f"O-{request_id}", "COMPLETED", None, f"CAP-{request_id}")
            else:
                order = OrderResult(
                    f"O-{request_id}", "PAYER_ACTION_REQUIRED",
                    f"https://paypal.test/approve/{request_id}", None,
                )
            self.orders[request_id] = order
        self._maybe_fail()  # fail *after* the charge: the nasty case
        return self.orders[request_id]

    async def capture_order(self, order_id, request_id):
        self.captured.append(order_id)
        self._maybe_fail()
        return OrderResult(order_id, "COMPLETED", None, f"CAP-{order_id}")

    async def refund_capture(self, capture_id, request_id, amount=None):
        self.refunds.append(capture_id)
        return RefundResult("REF1", "COMPLETED")


@pytest.fixture
def store():
    s = Store(":memory:")
    s.create_allowance(
        Allowance(
            id="alw",
            budget=USD("120"),
            per_purchase_limit=USD("100"),
            approval_threshold=USD("50"),
            expires_at=NOW + timedelta(days=7),
            spent=USD("0"),
            reserved=USD("0"),
            merchant_allowlist=frozenset({SHOP}),
            max_purchases_per_hour=10,
        )
    )
    yield s
    s.close()


@pytest.fixture
def paypal():
    return FakePayPal()


@pytest.fixture
def service(store, paypal):
    ids = iter(f"pay_{n}" for n in range(100))
    return PaymentService(store, paypal, "http://localhost:8000", clock=lambda: NOW,
                          new_id=lambda: next(ids))


@pytest.fixture
async def linked(service):
    await service.finish_wallet_link("SETUP1")
    return service


def req(amount="12.50", **kw):
    return PaymentRequest(**{"item": "Coffee", "category": "groceries", "merchant_id": SHOP,
                             "amount": USD(amount), "reason": "Out of coffee", **kw})


def remaining(store):
    return store.get_allowance("alw").remaining


async def test_small_purchase_is_paid_with_vault(linked, store, paypal):
    result = await linked.request_payment("alw", req("12.50"))
    assert result.status is PaymentStatus.PAID
    assert result.capture_id == "CAP-pay_0"
    assert paypal.charges == ["pay_0"]  # our payment id is the PayPal-Request-Id
    assert remaining(store) == USD("107.50")
    kinds = [e["kind"] for e in store.list_audit()]
    assert kinds[-2:] == ["payment.decided", "payment.paid"]


async def test_without_wallet_small_purchase_is_escalated(service, paypal):
    result = await service.request_payment("alw", req("12.50"))
    assert result.status is PaymentStatus.PENDING_OWNER
    assert "wallet.not_linked" in {r.rule_id for r in result.decision.triggered}
    assert result.approve_url and paypal.charges == []


async def test_denied_request_never_reaches_paypal(linked, paypal):
    result = await linked.request_payment("alw", req("150"))
    assert result.status is PaymentStatus.DENIED
    assert paypal.calls == []


async def test_escalation_reserves_budget_until_owner_approves(linked, store, paypal):
    result = await linked.request_payment("alw", req("95"))
    assert result.status is PaymentStatus.PENDING_OWNER
    assert remaining(store) == USD("25.00")  # reserved while waiting

    record = await linked.owner_approved(result.payment_id, order_token="O-pay_0")
    assert record.status is PaymentStatus.PAID
    assert remaining(store) == USD("25.00")  # now spent instead of reserved


async def test_forged_return_token_is_refused(linked, paypal):
    result = await linked.request_payment("alw", req("95"))
    with pytest.raises(InvalidState):
        await linked.owner_approved(result.payment_id, order_token="SOMEONE-ELSES-ORDER")
    assert paypal.captured == []


async def test_owner_cancel_releases_reservation(linked, store):
    result = await linked.request_payment("alw", req("95"))
    record = await linked.owner_rejected(result.payment_id)
    assert record.status is PaymentStatus.REJECTED
    assert remaining(store) == USD("120.00")


async def test_freeze_between_escalation_and_approval_blocks_capture(linked, store, paypal):
    result = await linked.request_payment("alw", req("95"))
    store.set_frozen("alw", True)
    record = await linked.owner_approved(result.payment_id, order_token="O-pay_0")
    assert record.status is PaymentStatus.REJECTED
    assert paypal.captured == []


async def test_network_drop_after_charge_retries_without_double_charge(linked, store, paypal):
    paypal.fail_next = [GatewayUnreachable("timeout")]
    result = await linked.request_payment("alw", req("12.50"))
    assert result.status is PaymentStatus.PAID
    assert paypal.calls == [("create_order", "pay_0"), ("create_order", "pay_0")]
    assert paypal.charges == ["pay_0"]  # charged once
    assert remaining(store) == USD("107.50")


async def test_unknown_outcome_keeps_budget_reserved(linked, store, paypal):
    paypal.fail_next = [GatewayUnreachable("timeout"), GatewayUnreachable("timeout")]
    result = await linked.request_payment("alw", req("40"))
    assert result.status is PaymentStatus.UNKNOWN
    assert remaining(store) == USD("80.00")  # not released: money may have moved


async def test_paypal_5xx_is_uncertain_but_4xx_is_a_clean_failure(linked, store, paypal):
    paypal.fail_next = [GatewayError(422, "UNPROCESSABLE", "nope", "dbg")]
    failed = await linked.request_payment("alw", req("10"))
    assert failed.status is PaymentStatus.FAILED
    assert remaining(store) == USD("120.00")

    paypal.fail_next = [GatewayError(500, "INTERNAL", "x", "d"), GatewayError(500, "INTERNAL", "x", "d")]
    unknown = await linked.request_payment("alw", req("10"))
    assert unknown.status is PaymentStatus.UNKNOWN
    assert remaining(store) == USD("110.00")


async def test_concurrent_requests_cannot_overspend(linked, store):
    # 120 budget: three concurrent 45s would be 135. One must be denied.
    results = await asyncio.gather(*(linked.request_payment("alw", req("45")) for _ in range(3)))
    statuses = sorted(r.status for r in results)
    assert statuses.count(PaymentStatus.PAID) == 2
    assert statuses.count(PaymentStatus.DENIED) == 1
    assert remaining(store) == USD("30.00")


async def test_refund_releases_budget(linked, store, paypal):
    paid = await linked.request_payment("alw", req("12.50"))
    record = await linked.refund(paid.payment_id)
    assert record.status is PaymentStatus.REFUNDED
    assert paypal.refunds == ["CAP-pay_0"]
    assert remaining(store) == USD("120.00")
    with pytest.raises(InvalidState):
        await linked.refund(paid.payment_id)  # can't refund twice
