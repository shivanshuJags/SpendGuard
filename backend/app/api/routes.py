"""HTTP routes. Each one translates HTTP to a service call and back; no rules here."""

import uuid
from datetime import timedelta
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse

from app.adapters.store import utcnow
from app.domain.models import Allowance
from app.domain.money import Money
from app.services.payments import InvalidState, NotFound
from app.services.ports import GatewayError, GatewayUnreachable
from app.services.shopping import purchase_item

from .schemas import (
    AllowanceCreate,
    AllowanceOut,
    AuditEventOut,
    CatalogItemOut,
    FreezeBody,
    PaymentOut,
    PurchaseBody,
    WalletOut,
)

router = APIRouter(prefix="/api")


def container(request: Request):
    return request.app.state.container


C = Annotated[object, Depends(container)]


def _to_dashboard(c, **params: str) -> RedirectResponse:
    """Browser-facing routes end by sending the owner back to the dashboard."""
    return RedirectResponse(f"{c.settings.frontend_url}/?{urlencode(params)}", status_code=303)


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ---- catalog ------------------------------------------------------------------


@router.get("/catalog")
def search_catalog(c: C, q: str = "") -> list[CatalogItemOut]:
    return [CatalogItemOut.of(item) for item in c.catalog.search(q, limit=50)]


# ---- wallet -------------------------------------------------------------------


@router.get("/wallet")
def get_wallet(c: C) -> WalletOut:
    return WalletOut.of(c.store.get_wallet())


@router.post("/wallet/link")
async def link_wallet(c: C) -> dict[str, str]:
    return {"approve_url": await c.payments.start_wallet_link()}


@router.get("/wallet/return", include_in_schema=False)
async def wallet_return(
    c: C,
    approval_token_id: str | None = None,
    approval_session_id: str | None = None,
) -> RedirectResponse:
    setup_token = approval_token_id or approval_session_id
    if not setup_token:
        return _to_dashboard(c, wallet="error", message="PayPal did not return a token")
    try:
        await c.payments.finish_wallet_link(setup_token)
    except (GatewayError, GatewayUnreachable) as error:
        return _to_dashboard(c, wallet="error", message=str(error))
    return _to_dashboard(c, wallet="linked")


@router.get("/wallet/cancel", include_in_schema=False)
def wallet_cancel(c: C) -> RedirectResponse:
    return _to_dashboard(c, wallet="cancelled")


@router.delete("/wallet")
async def unlink_wallet(c: C) -> WalletOut:
    await c.payments.unlink_wallet()
    return WalletOut.of(None)


# ---- allowances -----------------------------------------------------------------


def _create_allowance(c, body: AllowanceCreate) -> AllowanceOut:
    money = lambda v: Money.of(v, body.currency)  # noqa: E731
    allowance = Allowance(
        id=f"alw_{uuid.uuid4().hex[:12]}",
        budget=money(body.budget),
        per_purchase_limit=money(body.per_purchase_limit),
        approval_threshold=money(body.approval_threshold),
        expires_at=utcnow() + timedelta(days=body.expires_in_days),
        spent=Money.zero(body.currency),
        reserved=Money.zero(body.currency),
        merchant_allowlist=None if body.merchant_allowlist is None else frozenset(body.merchant_allowlist),
        merchant_denylist=frozenset(body.merchant_denylist),
        blocked_categories=frozenset(body.blocked_categories),
        max_purchases_per_hour=body.max_purchases_per_hour,
    )
    c.store.create_allowance(allowance)
    c.store.append_audit("allowance.created", body.model_dump())
    return AllowanceOut.of(c.store.get_allowance(allowance.id))


@router.post("/allowances", status_code=201)
def create_allowance(c: C, body: AllowanceCreate) -> AllowanceOut:
    return _create_allowance(c, body)


@router.post("/demo/allowance", status_code=201)
def create_demo_allowance(c: C) -> AllowanceOut:
    """The demo-script allowance: $120 budget, $100 cap, owner approves from $50."""
    return _create_allowance(
        c,
        AllowanceCreate(
            budget="120.00",
            per_purchase_limit="100.00",
            approval_threshold="50.00",
            merchant_allowlist=[c.settings.demo_merchant_email],
        ),
    )


@router.get("/allowances")
def list_allowances(c: C) -> list[AllowanceOut]:
    return [AllowanceOut.of(c.store.get_allowance(i)) for i in c.store.list_allowance_ids()]


def _allowance(c, allowance_id: str):
    allowance = c.store.get_allowance(allowance_id)
    if allowance is None:
        raise NotFound(f"allowance {allowance_id}")
    return allowance


@router.get("/allowances/{allowance_id}")
def get_allowance(c: C, allowance_id: str) -> AllowanceOut:
    return AllowanceOut.of(_allowance(c, allowance_id))


@router.post("/allowances/{allowance_id}/freeze")
def freeze_allowance(c: C, allowance_id: str, body: FreezeBody) -> AllowanceOut:
    _allowance(c, allowance_id)
    c.store.set_frozen(allowance_id, body.frozen)
    c.store.append_audit("allowance.frozen" if body.frozen else "allowance.unfrozen",
                         {"allowance_id": allowance_id})
    return AllowanceOut.of(_allowance(c, allowance_id))


# ---- payments -------------------------------------------------------------------


@router.get("/allowances/{allowance_id}/payments")
def list_payments(c: C, allowance_id: str) -> list[PaymentOut]:
    _allowance(c, allowance_id)
    return [PaymentOut.of(p) for p in c.store.list_payments(allowance_id)]


@router.post("/allowances/{allowance_id}/purchases")
async def purchase(c: C, allowance_id: str, body: PurchaseBody) -> PaymentOut:
    """Same path the agent's tool uses: catalog facts + policy + PayPal."""
    result = await purchase_item(
        c.payments, c.catalog, allowance_id, body.item_id, body.quantity, body.reason
    )
    return PaymentOut.of(c.store.get_payment(result.payment_id))


@router.get("/payments/{payment_id}/return", include_in_schema=False)
async def payment_return(c: C, payment_id: str, token: str = "") -> RedirectResponse:
    try:
        record = await c.payments.owner_approved(payment_id, token)
    except (NotFound, InvalidState, GatewayUnreachable) as error:
        return _to_dashboard(c, payment=payment_id, status="error", message=str(error))
    return _to_dashboard(c, payment=payment_id, status=record.status.value)


@router.get("/payments/{payment_id}/cancel", include_in_schema=False)
async def payment_cancel(c: C, payment_id: str) -> RedirectResponse:
    try:
        record = await c.payments.owner_rejected(payment_id)
    except (NotFound, InvalidState) as error:
        return _to_dashboard(c, payment=payment_id, status="error", message=str(error))
    return _to_dashboard(c, payment=payment_id, status=record.status.value)


@router.post("/payments/{payment_id}/refund")
async def refund(c: C, payment_id: str) -> PaymentOut:
    return PaymentOut.of(await c.payments.refund(payment_id))


# ---- audit ----------------------------------------------------------------------


@router.get("/audit")
def audit(
    c: C,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[AuditEventOut]:
    return [AuditEventOut(**e) for e in c.store.list_audit(after, limit)]
