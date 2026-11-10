"""Turn "buy item X" into a PaymentRequest using facts the server owns.

The agent chooses an item, a quantity and a reason. Price, category and
merchant come from the catalog, never from the agent, so a manipulated agent
can't relabel a gift card as groceries or quote its own price.
"""

from app.adapters.catalog import Catalog
from app.domain.models import PaymentRequest
from app.domain.money import Money

from .payments import NotFound, PaymentResult, PaymentService

MAX_QUANTITY = 20


async def purchase_item(
    payments: PaymentService,
    catalog: Catalog,
    allowance_id: str,
    item_id: str,
    quantity: int,
    reason: str,
) -> PaymentResult:
    item = catalog.get(item_id)
    if item is None:
        raise NotFound(f"catalog item {item_id}")
    if not 1 <= quantity <= MAX_QUANTITY:
        raise ValueError(f"quantity must be between 1 and {MAX_QUANTITY}")

    label = item.name if quantity == 1 else f"{quantity} x {item.name}"
    request = PaymentRequest(
        item=label,
        category=item.category,
        merchant_id=item.merchant_id,
        amount=Money(item.price.amount * quantity, item.price.currency),
        reason=reason,
    )
    return await payments.request_payment(allowance_id, request)
