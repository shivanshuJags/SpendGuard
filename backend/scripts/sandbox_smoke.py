"""Real PayPal sandbox check: OAuth works and an order can be created.

Run from backend/:  uv run python -m scripts.sandbox_smoke you-merchant@business.example.com
The payee must be one of your sandbox *business* accounts.
"""

import asyncio
import sys
import uuid

import httpx

from app.adapters.paypal.client import PayPalClient
from app.config import get_settings
from app.domain.money import Money


async def main(payee_email: str) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30) as http:
        paypal = PayPalClient(
            settings.paypal_client_id,
            settings.paypal_client_secret.get_secret_value(),
            settings.paypal_base_url,
            http,
        )
        order = await paypal.create_order(
            amount=Money.of("1.00", "USD"),
            payee_email=payee_email,
            description="SpendGuard sandbox smoke test",
            request_id=str(uuid.uuid4()),
            return_url=f"{settings.public_base_url}/ok",
            cancel_url=f"{settings.public_base_url}/cancel",
        )
        print(f"OAuth OK. Order {order.id} status={order.status}")
        print(f"Approve as a sandbox personal account: {order.approve_url}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1]))
