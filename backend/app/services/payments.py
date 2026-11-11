"""Payment use cases: link wallet, request payment, owner approval, refund.

Order of operations for every agent request, without exception:
    evaluate policy -> record decision -> (maybe) call PayPal -> record result
Every step appends to the audit log.
"""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.adapters.store import PaymentRecord, Store, Wallet, utcnow
from app.domain.models import Decision, Outcome, PaymentRequest, PaymentStatus, RuleResult
from app.domain.policy import PolicyContext, evaluate

from .ports import GatewayError, GatewayUnreachable, OrderResult, PaymentGateway


class NotFound(LookupError):
    pass


class InvalidState(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PaymentResult:
    payment_id: str
    status: PaymentStatus
    decision: Decision | None  # None for owner actions, which re-use the PayPal steps
    approve_url: str | None = None
    capture_id: str | None = None
    error: str | None = None


_WALLET_NOT_LINKED = RuleResult(
    "wallet.not_linked",
    Outcome.ESCALATE,
    "No saved PayPal wallet, so the owner must approve this payment.",
)


class PaymentService:
    def __init__(
        self,
        store: Store,
        gateway: PaymentGateway,
        public_base_url: str,
        clock: Callable[[], datetime] = utcnow,
        new_id: Callable[[], str] = lambda: f"pay_{uuid.uuid4().hex[:16]}",
    ):
        self._store = store
        self._gateway = gateway
        self._base = public_base_url.rstrip("/")
        self._clock = clock
        self._new_id = new_id
        # Overspend protection has two layers:
        # 1. request_payment inserts the payment row (reserving its amount)
        #    before its first await, so check-and-reserve can't interleave on
        #    asyncio's single thread.
        # 2. This lock keeps that true if an await is ever added in between,
        #    and serializes owner actions (e.g. a double-clicked approval).
        self._lock = asyncio.Lock()

    # ---- wallet -----------------------------------------------------------

    async def start_wallet_link(self) -> str:
        setup = await self._gateway.create_setup_token(
            return_url=f"{self._base}/api/wallet/return",
            cancel_url=f"{self._base}/api/wallet/cancel",
        )
        self._store.append_audit("wallet.link_started", {"setup_token": setup.id})
        return setup.approve_url

    async def finish_wallet_link(self, setup_token_id: str) -> Wallet:
        token = await self._gateway.create_payment_token(setup_token_id)
        self._store.save_wallet(token.id, token.payer_email)
        self._store.append_audit("wallet.linked", {"payer_email": token.payer_email})
        wallet = self._store.get_wallet()
        assert wallet is not None
        return wallet

    async def unlink_wallet(self) -> None:
        wallet = self._store.get_wallet()
        if wallet is None:
            return
        await self._gateway.delete_payment_token(wallet.vault_id)
        self._store.delete_wallet()
        self._store.append_audit("wallet.unlinked", {})

    # ---- agent payment requests -------------------------------------------

    async def request_payment(self, allowance_id: str, req: PaymentRequest) -> PaymentResult:
        async with self._lock:
            now = self._clock()
            allowance = self._store.get_allowance(allowance_id)
            if allowance is None:
                raise NotFound(f"allowance {allowance_id}")

            ctx = PolicyContext(
                now=now,
                recent_payment_times=self._store.recent_attempt_times(
                    allowance_id, since=now - timedelta(hours=1)
                ),
            )
            decision = evaluate(req, allowance, ctx)
            wallet = self._store.get_wallet()
            if decision.outcome is Outcome.APPROVE and wallet is None:
                decision = Decision(Outcome.ESCALATE, (*decision.triggered, _WALLET_NOT_LINKED))

            payment_id = self._new_id()
            initial = {
                Outcome.DENY: PaymentStatus.DENIED,
                Outcome.ESCALATE: PaymentStatus.PENDING_OWNER,
                Outcome.APPROVE: PaymentStatus.PENDING_OWNER,  # until PayPal confirms
            }[decision.outcome]
            self._store.insert_payment(payment_id, allowance_id, req, decision, initial, now)
            self._store.append_audit(
                "payment.decided",
                {
                    "item": req.item,
                    "amount": str(req.amount),
                    "merchant": req.merchant_id,
                    "agent_reason": req.reason,
                    "outcome": decision.outcome.value,
                    "rules": [r.rule_id for r in decision.triggered],
                    "reasons": decision.reasons,
                },
                payment_id,
            )

            match decision.outcome:
                case Outcome.DENY:
                    return PaymentResult(payment_id, PaymentStatus.DENIED, decision)
                case Outcome.APPROVE:
                    assert wallet is not None
                    return await self._auto_pay(payment_id, req, decision, wallet.vault_id)
                case Outcome.ESCALATE:
                    return await self._escalate(payment_id, req, decision)

    async def _auto_pay(
        self, payment_id: str, req: PaymentRequest, decision: Decision | None, vault_id: str,
        note: dict | None = None,
    ) -> PaymentResult:
        async def charge() -> OrderResult:
            order = await self._gateway.create_order(
                amount=req.amount,
                payee_email=req.merchant_id,
                description=req.item,
                request_id=payment_id,
                vault_id=vault_id,
            )
            if order.status == "APPROVED":  # created but not yet captured
                order = await self._gateway.capture_order(order.id, f"{payment_id}-capture")
            return order

        return await self._call_paypal(payment_id, decision, charge, note)

    async def _escalate(
        self, payment_id: str, req: PaymentRequest, decision: Decision | None,
        note: dict | None = None,
    ) -> PaymentResult:
        async def create() -> OrderResult:
            return await self._gateway.create_order(
                amount=req.amount,
                payee_email=req.merchant_id,
                description=req.item,
                request_id=payment_id,
                return_url=f"{self._base}/api/payments/{payment_id}/return",
                cancel_url=f"{self._base}/api/payments/{payment_id}/cancel",
            )

        return await self._call_paypal(payment_id, decision, create, note)

    async def _call_paypal(self, payment_id, decision, call, note=None) -> PaymentResult:
        """Run a PayPal write, retrying once on network failure or a 5xx.

        Retrying is safe because every write carries the same PayPal-Request-Id:
        PayPal returns the original result instead of charging twice.
        """
        for attempt in (1, 2):
            try:
                order = await call()
                break
            except GatewayError as error:
                if error.outcome_known:
                    # PayPal rejected the request: definitely nothing charged.
                    return self._finish(
                        payment_id, decision, PaymentStatus.FAILED, note, error=str(error)
                    )
                if attempt == 2:
                    return self._finish(
                        payment_id, decision, PaymentStatus.UNKNOWN, note,
                        error=f"PayPal server error, outcome unknown: {error}",
                    )
            except GatewayUnreachable as error:
                if attempt == 2:
                    # We never got an answer. Money may have moved: keep it reserved.
                    return self._finish(
                        payment_id, decision, PaymentStatus.UNKNOWN, note,
                        error=f"no answer from PayPal, outcome unknown: {error}",
                    )
        return self._settle(payment_id, decision, order, note)

    def _settle(self, payment_id, decision, order: OrderResult, note=None) -> PaymentResult:
        """Record what a PayPal order says happened. The one place that reads orders."""
        if order.is_paid:
            return self._finish(
                payment_id, decision, PaymentStatus.PAID, note,
                paypal_order_id=order.id, capture_id=order.capture_id, error=None,
            )
        if order.is_declined:
            return self._finish(
                payment_id, decision, PaymentStatus.FAILED, note,
                paypal_order_id=order.id,
                error=f"PayPal declined it (order {order.status}, capture {order.capture_status})",
            )
        if order.approve_url and order.status in ("CREATED", "PAYER_ACTION_REQUIRED"):
            return self._finish(
                payment_id, decision, PaymentStatus.PENDING_OWNER, note,
                paypal_order_id=order.id, approve_url=order.approve_url, error=None,
            )
        # e.g. a capture still PENDING at PayPal: money may yet move, keep it reserved.
        return self._finish(
            payment_id, decision, PaymentStatus.UNKNOWN, note,
            paypal_order_id=order.id,
            error=f"PayPal reports order {order.status}, capture {order.capture_status}",
        )

    def _finish(self, payment_id, decision, status: PaymentStatus, note=None, **fields):
        self._store.update_payment(payment_id, status=status, **fields)
        self._store.append_audit(f"payment.{status.value}", {**(note or {}), **fields}, payment_id)
        return PaymentResult(
            payment_id,
            status,
            decision,
            approve_url=fields.get("approve_url"),
            capture_id=fields.get("capture_id"),
            error=fields.get("error"),
        )

    # ---- owner actions ------------------------------------------------------

    async def owner_approved(self, payment_id: str, order_token: str) -> PaymentRecord:
        """PayPal redirected the owner back after approving. Capture the money."""
        async with self._lock:
            record = self._pending(payment_id)
            if order_token != record.paypal_order_id:
                raise InvalidState("PayPal token does not match this payment's order")

            if not self._active(record.allowance_id):
                return self._reject(payment_id, "allowance frozen or expired before capture")
            await self._capture(payment_id, record.paypal_order_id, {"by": "owner"})
            return self._get(payment_id)

    async def owner_rejected(self, payment_id: str) -> PaymentRecord:
        """The owner said no: on PayPal's cancel page or with the dashboard's Reject.

        Nothing was captured, so the reserved amount goes back to the budget. An
        order the owner approved on PayPal but never came back from stays uncaptured.
        """
        async with self._lock:
            self._pending(payment_id)
            return self._reject(payment_id, "owner rejected it")

    async def check_with_paypal(self, payment_id: str) -> PaymentRecord:
        """Settle an UNKNOWN payment by asking PayPal what actually happened."""
        async with self._lock:
            record = self._get(payment_id)
            if record.status is not PaymentStatus.UNKNOWN:
                raise InvalidState(f"payment is {record.status}, only unknown ones need checking")
            note = {"by": "check"}

            if record.paypal_order_id:
                # A read. If it fails the payment stays unknown and the owner can retry.
                order = await self._gateway.get_order(record.paypal_order_id)
                if order.status != "APPROVED":
                    self._settle(payment_id, None, order, note)
                elif not self._active(record.allowance_id):
                    # Approved but never captured, so nothing moved: safe to drop.
                    self._reject(payment_id, "allowance frozen or expired before capture")
                else:
                    # The capture's answer was lost. Same request id: PayPal returns
                    # that capture if it happened, and only captures now if it didn't.
                    await self._capture(payment_id, order.id, note)
                return self._get(payment_id)

            # PayPal never answered create_order. Replaying it with the same
            # PayPal-Request-Id returns the original order if PayPal made one.
            if not self._active(record.allowance_id):
                raise InvalidState(
                    "The allowance is frozen or expired, so SpendGuard won't replay this "
                    "payment. Unfreeze it to check, or look it up in your PayPal account."
                )
            if record.outcome == Outcome.APPROVE.value:
                wallet = self._store.get_wallet()
                if wallet is None:
                    raise InvalidState("Link the PayPal wallet again to check this auto-payment.")
                await self._auto_pay(payment_id, record.request, None, wallet.vault_id, note)
            else:
                await self._escalate(payment_id, record.request, None, note)
            return self._get(payment_id)

    async def _capture(self, payment_id: str, order_id: str, note: dict) -> None:
        async def capture() -> OrderResult:
            return await self._gateway.capture_order(order_id, f"{payment_id}-capture")

        await self._call_paypal(payment_id, None, capture, note)

    def _active(self, allowance_id: str) -> bool:
        allowance = self._store.get_allowance(allowance_id)
        return allowance is not None and not allowance.frozen and self._clock() < allowance.expires_at

    def _reject(self, payment_id: str, why: str) -> PaymentRecord:
        self._store.update_payment(payment_id, status=PaymentStatus.REJECTED)
        self._store.append_audit("payment.rejected", {"why": why}, payment_id)
        return self._get(payment_id)

    async def refund(self, payment_id: str) -> PaymentRecord:
        async with self._lock:
            record = self._get(payment_id)
            if record.status is not PaymentStatus.PAID or not record.capture_id:
                raise InvalidState(f"only paid payments can be refunded (is {record.status})")
            result = await self._gateway.refund_capture(record.capture_id, f"{payment_id}-refund")
            self._store.update_payment(payment_id, status=PaymentStatus.REFUNDED)
            self._store.append_audit(
                "payment.refunded", {"refund_id": result.id, "status": result.status}, payment_id
            )
            return self._get(payment_id)

    def _get(self, payment_id: str) -> PaymentRecord:
        record = self._store.get_payment(payment_id)
        if record is None:
            raise NotFound(f"payment {payment_id}")
        return record

    def _pending(self, payment_id: str) -> PaymentRecord:
        record = self._get(payment_id)
        if record.status is not PaymentStatus.PENDING_OWNER or not record.paypal_order_id:
            raise InvalidState(f"payment is {record.status}, not waiting for the owner")
        return record
