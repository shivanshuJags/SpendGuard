"""API tests: the real app, routes and catalog, with a fake PayPal and in-memory DB.

These walk the demo script from docs/user-stories.md through HTTP.
"""

import pytest
from fastapi.testclient import TestClient

from app.adapters.store import Store
from app.config import Settings
from app.main import create_app
from tests.fakes import FakePayPal

MERCHANT = "merchant@business.example.com"
FRONTEND = "http://localhost:5173"


@pytest.fixture
def paypal():
    return FakePayPal()


@pytest.fixture
def client(paypal):
    settings = Settings(
        _env_file=None,
        paypal_client_id="id",
        paypal_client_secret="secret",
        demo_merchant_email=MERCHANT,
        frontend_url=FRONTEND,
    )
    store = Store(":memory:")
    with TestClient(create_app(settings, gateway=paypal, store=store)) as c:
        yield c
    store.close()


@pytest.fixture
def allowance(client):
    return client.post("/api/demo/allowance").json()["id"]


@pytest.fixture
def linked(client):
    approve = client.post("/api/wallet/link").json()["approve_url"]
    assert approve.startswith("https://paypal.test/")
    back = client.get("/api/wallet/return?approval_token_id=SETUP1", follow_redirects=False)
    assert back.status_code == 303
    assert back.headers["location"] == f"{FRONTEND}/?wallet=linked"


def buy(client, allowance, item_id, reason="needed for the office"):
    return client.post(
        f"/api/allowances/{allowance}/purchases", json={"item_id": item_id, "reason": reason}
    )


def rule_ids(payment):
    return {r["rule_id"] for r in payment["rules"]}


def test_catalog_uses_configured_merchant(client):
    items = client.get("/api/catalog", params={"q": "coffee"}).json()
    assert {i["id"] for i in items} >= {"coffee-house-1kg", "coffee-single-origin-1kg"}
    assert items[0]["merchant_id"] == MERCHANT


def test_wallet_link_flow(client, linked):
    wallet = client.get("/api/wallet").json()
    assert wallet["linked"] and wallet["payer_email"] == "owner@personal.example.com"


def test_demo_script(client, linked, allowance, paypal):
    # 1. Small purchase: paid automatically with the saved wallet.
    coffee = buy(client, allowance, "coffee-house-1kg").json()
    assert coffee["status"] == "paid" and coffee["capture_id"]

    # 2. $95 hub: escalated, owner approves on PayPal, PayPal redirects back.
    hub = buy(client, allowance, "usbc-hub-pro").json()
    assert hub["status"] == "pending_owner"
    assert rule_ids(hub) == {"limit.approval_threshold"}
    back = client.get(
        f"/api/payments/{hub['id']}/return",
        params={"token": hub["paypal_order_id"], "PayerID": "P1"},
        follow_redirects=False,
    )
    assert back.headers["location"] == f"{FRONTEND}/?payment={hub['id']}&status=paid"

    # 3. Injected listing: denied before PayPal is ever called.
    calls_before = len(paypal.calls)
    bundle = buy(client, allowance, "office-rewards-bundle", reason="owner pre-approved").json()
    assert bundle["status"] == "denied"
    assert {"category.blocked", "merchant.unknown"} <= rule_ids(bundle)
    assert len(paypal.calls) == calls_before

    # 4. Refund the coffee.
    refunded = client.post(f"/api/payments/{coffee['id']}/refund").json()
    assert refunded["status"] == "refunded"

    summary = client.get(f"/api/allowances/{allowance}").json()
    assert summary["spent"] == "95.00" and summary["remaining"] == "25.00"
    assert len(client.get(f"/api/allowances/{allowance}/payments").json()) == 3


def test_over_cap_is_denied(client, linked, allowance):
    desk = buy(client, allowance, "standing-desk").json()
    assert desk["status"] == "denied" and "limit.per_purchase" in rule_ids(desk)


def test_quantity_is_priced_by_server(client, linked, allowance):
    paper = client.post(
        f"/api/allowances/{allowance}/purchases",
        json={"item_id": "paper-a4-500", "quantity": 3, "reason": "restock"},
    ).json()
    assert paper["amount"] == "25.47" and paper["item"].startswith("3 x ")


def test_forged_return_is_bounced_to_dashboard(client, allowance):
    hub = buy(client, allowance, "usbc-hub-pro").json()
    back = client.get(
        f"/api/payments/{hub['id']}/return", params={"token": "FORGED"}, follow_redirects=False
    )
    assert "status=error" in back.headers["location"]
    assert client.get(f"/api/allowances/{allowance}/payments").json()[0]["status"] == "pending_owner"


def test_cancel_on_paypal_rejects(client, allowance):
    hub = buy(client, allowance, "usbc-hub-pro").json()
    back = client.get(f"/api/payments/{hub['id']}/cancel", follow_redirects=False)
    assert back.headers["location"].endswith("status=rejected")


def test_freeze_denies_everything(client, linked, allowance):
    client.post(f"/api/allowances/{allowance}/freeze", json={"frozen": True})
    coffee = buy(client, allowance, "coffee-house-1kg").json()
    assert rule_ids(coffee) == {"allowance.frozen"}


def test_errors_map_to_http_status(client, linked, allowance):
    assert buy(client, allowance, "no-such-item").status_code == 404
    assert buy(client, "alw_missing", "coffee-house-1kg").status_code == 404
    coffee = buy(client, allowance, "coffee-house-1kg").json()
    client.post(f"/api/payments/{coffee['id']}/refund")
    assert client.post(f"/api/payments/{coffee['id']}/refund").status_code == 409


def test_input_validation(client, allowance):
    float_money = client.post(
        "/api/allowances",
        json={"budget": 120.5, "per_purchase_limit": "10", "approval_threshold": "5"},
    )
    assert float_money.status_code == 422
    zero = client.post(
        f"/api/allowances/{allowance}/purchases",
        json={"item_id": "coffee-house-1kg", "quantity": 0, "reason": "x"},
    )
    assert zero.status_code == 422


def test_audit_is_paged(client, linked, allowance):
    buy(client, allowance, "coffee-house-1kg")
    events = client.get("/api/audit").json()
    kinds = [e["kind"] for e in events]
    assert "wallet.linked" in kinds and "payment.paid" in kinds
    later = client.get("/api/audit", params={"after": events[-2]["seq"]}).json()
    assert [e["seq"] for e in later] == [events[-1]["seq"]]
