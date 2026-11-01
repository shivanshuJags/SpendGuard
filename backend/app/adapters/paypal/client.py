"""PayPal REST adapter (sandbox): OAuth, Vault v3, Orders v2, Refunds.

This is the only module that knows PayPal URLs and JSON shapes. It returns
small typed results so the rest of the app never parses raw PayPal JSON.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.domain.money import Money


class PayPalError(Exception):
    def __init__(self, status: int, name: str, message: str, debug_id: str | None):
        super().__init__(f"PayPal {status} {name}: {message} (debug_id={debug_id})")
        self.status = status
        self.name = name
        self.debug_id = debug_id  # quote this when searching PayPal's logs


@dataclass(frozen=True, slots=True)
class SetupToken:
    id: str
    approve_url: str


@dataclass(frozen=True, slots=True)
class PaymentToken:
    id: str  # the vault_id we charge later
    customer_id: str | None
    payer_email: str | None


@dataclass(frozen=True, slots=True)
class OrderResult:
    id: str
    status: str  # CREATED, PAYER_ACTION_REQUIRED, APPROVED, COMPLETED, ...
    approve_url: str | None  # set when the owner must approve on PayPal
    capture_id: str | None  # set once money has moved

    @property
    def is_paid(self) -> bool:
        return self.status == "COMPLETED" and self.capture_id is not None


@dataclass(frozen=True, slots=True)
class RefundResult:
    id: str
    status: str


class PayPalClient:
    # Refresh the token a minute early so a request never races its expiry.
    _TOKEN_SKEW_SECONDS = 60

    def __init__(self, client_id: str, client_secret: str, base_url: str, http: httpx.AsyncClient):
        self._client_id = client_id
        self._client_secret = client_secret
        self._base_url = base_url.rstrip("/")
        self._http = http
        self._token: str | None = None
        self._token_expires_at = 0.0
        # Many concurrent requests at startup should fetch one token, not many.
        self._token_lock = asyncio.Lock()

    # ---- auth -----------------------------------------------------------

    async def _access_token(self) -> str:
        async with self._token_lock:
            if self._token and time.monotonic() < self._token_expires_at:
                return self._token
            response = await self._http.post(
                f"{self._base_url}/v1/oauth2/token",
                data={"grant_type": "client_credentials"},
                auth=(self._client_id, self._client_secret),
            )
            body = _json_or_raise(response)
            self._token = body["access_token"]
            self._token_expires_at = (
                time.monotonic() + int(body["expires_in"]) - self._TOKEN_SKEW_SECONDS
            )
            return self._token

    async def _request(
        self, method: str, path: str, *, json: Any = None, request_id: str | None = None
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {await self._access_token()}",
            "Prefer": "return=representation",
        }
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        response = await self._http.request(
            method, f"{self._base_url}{path}", json=json, headers=headers
        )
        return _json_or_raise(response)

    # ---- vault: link the owner's wallet once ----------------------------

    async def create_setup_token(self, return_url: str, cancel_url: str) -> SetupToken:
        body = await self._request(
            "POST",
            "/v3/vault/setup-tokens",
            json={
                "payment_source": {
                    "paypal": {
                        "usage_type": "MERCHANT",
                        "experience_context": {
                            "return_url": return_url,
                            "cancel_url": cancel_url,
                            "shipping_preference": "NO_SHIPPING",
                        },
                    }
                }
            },
        )
        return SetupToken(id=body["id"], approve_url=_link(body, "approve"))

    async def create_payment_token(self, setup_token_id: str) -> PaymentToken:
        body = await self._request(
            "POST",
            "/v3/vault/payment-tokens",
            json={"payment_source": {"token": {"id": setup_token_id, "type": "SETUP_TOKEN"}}},
            request_id=f"pt-{setup_token_id}",
        )
        paypal = body.get("payment_source", {}).get("paypal", {})
        return PaymentToken(
            id=body["id"],
            customer_id=body.get("customer", {}).get("id"),
            payer_email=paypal.get("email_address"),
        )

    async def delete_payment_token(self, payment_token_id: str) -> None:
        await self._request("DELETE", f"/v3/vault/payment-tokens/{payment_token_id}")

    # ---- orders ---------------------------------------------------------

    async def create_order(
        self,
        *,
        amount: Money,
        payee_email: str,
        description: str,
        request_id: str,
        vault_id: str | None = None,
        return_url: str | None = None,
        cancel_url: str | None = None,
    ) -> OrderResult:
        """With vault_id: charge the saved wallet now (agent auto-pay).
        Without: return an approve URL the owner must visit (escalation)."""
        if vault_id:
            payment_source = {"paypal": {"vault_id": vault_id}}
        elif return_url and cancel_url:
            payment_source = {
                "paypal": {
                    "experience_context": {
                        "return_url": return_url,
                        "cancel_url": cancel_url,
                        "user_action": "PAY_NOW",
                        "shipping_preference": "NO_SHIPPING",
                    }
                }
            }
        else:
            raise ValueError("need vault_id, or return_url and cancel_url")

        body = await self._request(
            "POST",
            "/v2/checkout/orders",
            json={
                "intent": "CAPTURE",
                "purchase_units": [
                    {
                        "reference_id": request_id,
                        "description": description[:127],  # PayPal max length
                        "amount": amount.to_paypal(),
                        "payee": {"email_address": payee_email},
                    }
                ],
                "payment_source": payment_source,
            },
            request_id=request_id,
        )
        return _order(body)

    async def capture_order(self, order_id: str, request_id: str) -> OrderResult:
        body = await self._request(
            "POST", f"/v2/checkout/orders/{order_id}/capture", json={}, request_id=request_id
        )
        return _order(body)

    async def get_order(self, order_id: str) -> OrderResult:
        return _order(await self._request("GET", f"/v2/checkout/orders/{order_id}"))

    async def refund_capture(
        self, capture_id: str, request_id: str, amount: Money | None = None
    ) -> RefundResult:
        body = await self._request(
            "POST",
            f"/v2/payments/captures/{capture_id}/refund",
            json={"amount": amount.to_paypal()} if amount else {},
            request_id=request_id,
        )
        return RefundResult(id=body["id"], status=body["status"])


# ---- parsing helpers ------------------------------------------------------


def _json_or_raise(response: httpx.Response) -> dict[str, Any]:
    if response.status_code == 204:
        return {}
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.is_success:
        return body
    raise PayPalError(
        status=response.status_code,
        name=body.get("name") or body.get("error") or "UNKNOWN",
        message=body.get("message") or body.get("error_description") or response.text[:200],
        debug_id=body.get("debug_id") or response.headers.get("paypal-debug-id"),
    )


def _link(body: dict[str, Any], *rels: str) -> str:
    for link in body.get("links", []):
        if link.get("rel") in rels:
            return link["href"]
    raise PayPalError(200, "MISSING_LINK", f"no {rels} link in response", body.get("debug_id"))


def _order(body: dict[str, Any]) -> OrderResult:
    approve_url = next(
        (l["href"] for l in body.get("links", []) if l.get("rel") in ("payer-action", "approve")),
        None,
    )
    captures = [
        capture
        for unit in body.get("purchase_units", [])
        for capture in unit.get("payments", {}).get("captures", [])
    ]
    return OrderResult(
        id=body["id"],
        status=body["status"],
        approve_url=approve_url,
        capture_id=captures[0]["id"] if captures else None,
    )
