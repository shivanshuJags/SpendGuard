"""Ports: what the services need from the outside world, as Protocols.

The real PayPal client satisfies PaymentGateway; tests pass a fake. Result
types live here (not in the adapter) so dependencies point inward.
"""

from dataclasses import dataclass
from typing import Protocol

from app.domain.money import Money


class GatewayError(Exception):
    """The payment provider answered with an error.

    4xx: the request was rejected and nothing was charged.
    5xx: the provider failed internally; a write may or may not have happened.
    """

    def __init__(self, status: int, name: str, message: str, debug_id: str | None):
        super().__init__(f"{status} {name}: {message} (debug_id={debug_id})")
        self.status = status
        self.name = name
        self.debug_id = debug_id  # quote this when searching the provider's logs

    @property
    def outcome_known(self) -> bool:
        return self.status < 500


class GatewayUnreachable(Exception):
    """No answer from the provider: the outcome of a write is unknown."""


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
    capture_id: str | None  # set once a capture was attempted
    # The capture's own status. PayPal can report the *order* COMPLETED while the
    # capture inside it is DECLINED, so money moved only if this is COMPLETED.
    capture_status: str | None = None

    @property
    def is_paid(self) -> bool:
        return self.status == "COMPLETED" and self.capture_status == "COMPLETED"

    @property
    def is_declined(self) -> bool:
        return self.capture_status in ("DECLINED", "FAILED") or self.status == "VOIDED"


@dataclass(frozen=True, slots=True)
class RefundResult:
    id: str
    status: str


class PaymentGateway(Protocol):
    async def create_setup_token(self, return_url: str, cancel_url: str) -> SetupToken: ...

    async def create_payment_token(self, setup_token_id: str) -> PaymentToken: ...

    async def delete_payment_token(self, payment_token_id: str) -> None: ...

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
    ) -> OrderResult: ...

    async def capture_order(self, order_id: str, request_id: str) -> OrderResult: ...

    async def get_order(self, order_id: str) -> OrderResult: ...

    async def refund_capture(
        self, capture_id: str, request_id: str, amount: Money | None = None
    ) -> RefundResult: ...
