"""Real PayPal sandbox checks. Run from backend/ with keys in .env.

  uv run python -m scripts.sandbox_smoke order <business-email>
      Escalation path: create an order, print the owner's approve link.
  uv run python -m scripts.sandbox_smoke capture <order-id>
      After approving that link, capture the money.

  uv run python -m scripts.sandbox_smoke vault
      Auto-pay path, step 1: print a link to save the owner's wallet.
  uv run python -m scripts.sandbox_smoke vault-finish <setup-token-id>
      Step 2, after approving: exchange it for a payment token (vault_id).
  uv run python -m scripts.sandbox_smoke pay <vault-id> <business-email>
      Step 3: charge the saved wallet with no owner interaction.

The browser redirect after approving goes to localhost and may show an error
page while the server isn't running. That's fine: PayPal has recorded the approval.
"""

import asyncio
import sys
import uuid

import httpx

from app.adapters.paypal.client import PayPalClient
from app.config import get_settings
from app.domain.money import Money


async def run(command: str, args: list[str]) -> None:
    settings = get_settings()
    ok, cancel = f"{settings.public_base_url}/ok", f"{settings.public_base_url}/cancel"
    async with httpx.AsyncClient(timeout=30) as http:
        paypal = PayPalClient(
            settings.paypal_client_id,
            settings.paypal_client_secret.get_secret_value(),
            settings.paypal_base_url,
            http,
        )
        match command, args:
            case "order", [payee]:
                order = await paypal.create_order(
                    amount=Money.of("1.00", "USD"),
                    payee_email=payee,
                    description="SpendGuard sandbox smoke test",
                    request_id=str(uuid.uuid4()),
                    return_url=ok,
                    cancel_url=cancel,
                )
                print(f"OAuth OK. Order {order.id} status={order.status}")
                print(f"Approve as the sandbox personal account: {order.approve_url}")
            case "capture", [order_id]:
                order = await paypal.capture_order(order_id, request_id=f"cap-{order_id}")
                print(f"Order {order.id} status={order.status} capture={order.capture_id}")
            case "vault", []:
                setup = await paypal.create_setup_token(ok, cancel)
                print(f"Setup token {setup.id}")
                print(f"Approve as the sandbox personal account: {setup.approve_url}")
            case "vault-finish", [setup_token_id]:
                token = await paypal.create_payment_token(setup_token_id)
                print(f"vault_id={token.id} customer={token.customer_id} payer={token.payer_email}")
            case "pay", [vault_id, payee]:
                order = await paypal.create_order(
                    amount=Money.of("2.00", "USD"),
                    payee_email=payee,
                    description="SpendGuard vault auto-pay test",
                    request_id=str(uuid.uuid4()),
                    vault_id=vault_id,
                )
                print(f"Order {order.id} status={order.status} capture={order.capture_id}")
                print("Auto-pay works." if order.is_paid else "Not paid yet: inspect status.")
            case _:
                sys.exit(__doc__)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(run(sys.argv[1], sys.argv[2:]))
