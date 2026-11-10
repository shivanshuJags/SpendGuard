"""Test doubles shared by service and API tests."""

import asyncio
from dataclasses import dataclass, field

from app.services.ports import OrderResult, PaymentToken, RefundResult, SetupToken


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
