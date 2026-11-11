"""PayPal adapter tests with faked HTTP (respx). No network, no credentials.

These prove our request shapes and parsing. They do NOT prove PayPal accepts
them; scripts/sandbox_smoke.py does that against the real sandbox.
"""

import json

import httpx
import pytest
import respx

from app.adapters.paypal.client import PayPalClient, PayPalError
from app.domain.money import Money

BASE = "https://api-m.sandbox.paypal.com"


@pytest.fixture
async def client():
    async with httpx.AsyncClient() as http:
        yield PayPalClient("id", "secret", BASE, http)


@pytest.fixture
def paypal():
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.post("/v1/oauth2/token").respond(
            json={"access_token": "tok", "expires_in": 32400}
        )
        yield mock


async def test_token_is_cached(client, paypal):
    order = paypal.get("/v2/checkout/orders/O1").respond(json={"id": "O1", "status": "CREATED"})
    await client.get_order("O1")
    await client.get_order("O1")
    assert paypal.routes[0].call_count == 1  # one OAuth call, two API calls
    assert order.call_count == 2
    assert order.calls[0].request.headers["Authorization"] == "Bearer tok"


async def test_vault_order_is_paid_immediately(client, paypal):
    route = paypal.post("/v2/checkout/orders").respond(
        json={
            "id": "O1",
            "status": "COMPLETED",
            "purchase_units": [{"payments": {"captures": [{"id": "CAP1", "status": "COMPLETED"}]}}],
        }
    )
    result = await client.create_order(
        amount=Money.of("12.5", "USD"),
        payee_email="shop@example.com",
        description="Coffee",
        request_id="req-1",
        vault_id="VAULT1",
    )
    assert result.is_paid and result.capture_id == "CAP1"

    sent = route.calls[0].request
    assert sent.headers["PayPal-Request-Id"] == "req-1"
    body = json.loads(sent.content)
    assert body["payment_source"] == {"paypal": {"vault_id": "VAULT1"}}
    assert body["purchase_units"][0]["amount"] == {"currency_code": "USD", "value": "12.50"}


async def test_escalated_order_returns_approve_url(client, paypal):
    paypal.post("/v2/checkout/orders").respond(
        json={
            "id": "O2",
            "status": "PAYER_ACTION_REQUIRED",
            "links": [{"rel": "payer-action", "href": "https://sandbox.paypal.com/approve/O2"}],
        }
    )
    result = await client.create_order(
        amount=Money.of("95", "USD"),
        payee_email="shop@example.com",
        description="USB-C hub",
        request_id="req-2",
        return_url="http://localhost/ok",
        cancel_url="http://localhost/cancel",
    )
    assert not result.is_paid
    assert result.approve_url == "https://sandbox.paypal.com/approve/O2"


async def test_errors_keep_debug_id(client, paypal):
    paypal.post("/v2/checkout/orders/O3/capture").respond(
        422,
        json={"name": "UNPROCESSABLE_ENTITY", "message": "Order not approved", "debug_id": "dbg9"},
    )
    with pytest.raises(PayPalError) as error:
        await client.capture_order("O3", "req-3")
    assert error.value.status == 422
    assert error.value.debug_id == "dbg9"


async def test_order_needs_vault_or_return_urls(client, paypal):
    with pytest.raises(ValueError):
        await client.create_order(
            amount=Money.of("1", "USD"),
            payee_email="shop@example.com",
            description="x",
            request_id="r",
        )


async def test_declined_capture_inside_completed_order_is_not_paid():
    # PayPal can mark the order COMPLETED while the capture itself is DECLINED.
    from app.adapters.paypal.client import _order

    order = _order({
        "id": "O2", "status": "COMPLETED",
        "purchase_units": [{"payments": {"captures": [{"id": "CAP2", "status": "DECLINED"}]}}],
    })
    assert not order.is_paid and order.is_declined
