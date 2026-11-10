"""HTTP request/response shapes: the API's contract with the dashboard."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field

from app.adapters.catalog import CatalogItem
from app.adapters.store import PaymentRecord, Wallet
from app.domain.models import Allowance

# Money travels as a decimal string ("12.50"): JSON numbers are floats in JS.
Amount = Annotated[str, Field(pattern=r"^\d{1,7}(\.\d{1,2})?$", examples=["120.00"])]


class AllowanceCreate(BaseModel):
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] = "USD"
    budget: Amount
    per_purchase_limit: Amount
    approval_threshold: Amount
    expires_in_days: Annotated[int, Field(ge=1, le=90)] = 7
    merchant_allowlist: list[str] | None = None
    merchant_denylist: list[str] = []
    blocked_categories: list[str] = ["gift_cards", "crypto"]
    max_purchases_per_hour: Annotated[int, Field(ge=1, le=1000)] | None = 10


class FreezeBody(BaseModel):
    frozen: bool


class PurchaseBody(BaseModel):
    item_id: str
    quantity: Annotated[int, Field(ge=1, le=20)] = 1
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class AllowanceOut(BaseModel):
    id: str
    currency: str
    budget: str
    spent: str
    reserved: str
    remaining: str
    per_purchase_limit: str
    approval_threshold: str
    expires_at: datetime
    merchant_allowlist: list[str] | None
    merchant_denylist: list[str]
    blocked_categories: list[str]
    max_purchases_per_hour: int | None
    frozen: bool

    @classmethod
    def of(cls, a: Allowance) -> "AllowanceOut":
        return cls(
            id=a.id,
            currency=a.budget.currency,
            budget=str(a.budget.amount),
            spent=str(a.spent.amount),
            reserved=str(a.reserved.amount),
            remaining=str(a.remaining.amount),
            per_purchase_limit=str(a.per_purchase_limit.amount),
            approval_threshold=str(a.approval_threshold.amount),
            expires_at=a.expires_at,
            merchant_allowlist=None if a.merchant_allowlist is None else sorted(a.merchant_allowlist),
            merchant_denylist=sorted(a.merchant_denylist),
            blocked_categories=sorted(a.blocked_categories),
            max_purchases_per_hour=a.max_purchases_per_hour,
            frozen=a.frozen,
        )


class RuleOut(BaseModel):
    rule_id: str
    outcome: str
    reason: str


class PaymentOut(BaseModel):
    id: str
    item: str
    category: str
    merchant_id: str
    amount: str
    currency: str
    reason: str
    status: str
    outcome: str
    rules: list[RuleOut]
    paypal_order_id: str | None
    capture_id: str | None
    approve_url: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, p: PaymentRecord) -> "PaymentOut":
        return cls(
            id=p.id,
            item=p.request.item,
            category=p.request.category,
            merchant_id=p.request.merchant_id,
            amount=str(p.request.amount.amount),
            currency=p.request.amount.currency,
            reason=p.request.reason,
            status=p.status.value,
            outcome=p.outcome,
            rules=[RuleOut(**r) for r in p.rules],
            paypal_order_id=p.paypal_order_id,
            capture_id=p.capture_id,
            approve_url=p.approve_url,
            error=p.error,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )


class WalletOut(BaseModel):
    linked: bool
    payer_email: str | None = None
    linked_at: datetime | None = None

    @classmethod
    def of(cls, w: Wallet | None) -> "WalletOut":
        if w is None:
            return cls(linked=False)
        return cls(linked=True, payer_email=w.payer_email, linked_at=w.linked_at)


class CatalogItemOut(BaseModel):
    id: str
    name: str
    description: str
    price: str
    currency: str
    category: str
    merchant_id: str
    merchant_name: str

    @classmethod
    def of(cls, i: CatalogItem) -> "CatalogItemOut":
        return cls(
            id=i.id,
            name=i.name,
            description=i.description,
            price=str(i.price.amount),
            currency=i.price.currency,
            category=i.category,
            merchant_id=i.merchant_id,
            merchant_name=i.merchant_name,
        )


class AuditEventOut(BaseModel):
    seq: int
    at: datetime
    payment_id: str | None
    kind: str
    data: dict
