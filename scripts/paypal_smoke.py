"""Manual live check against the PayPal sandbox (not a test)."""

import asyncio
import os

from awning.config import load_settings
from awning.models import PricedLine, PricedOrder
from awning.paypal import PayPalClient


async def main() -> None:
    settings = load_settings()
    client = PayPalClient(
        settings.paypal_client_id, settings.paypal_client_secret, settings.paypal_base_url
    )
    priced = PricedOrder(
        lines=[PricedLine(item_id="smoke", name="Smoke test", option_names=[],
                          unit_price_cents=100, quantity=1, line_total_cents=100)],
        total_cents=100,
    )
    order = await client.create_order(
        reference_id="smoke_1",
        priced=priced,
        payee_merchant_id=None,
        payee_email=settings_payee_email(),
        brand_name="Awning smoke test",
        description="Smoke test",
        return_url="https://example.com/return",
        cancel_url="https://example.com/cancel",
    )
    print("Order:", order.id, order.status)
    print("Approve at:", order.approval_url)
    input("Press Enter after approving with the sandbox buyer...")
    result = await client.capture_order(order.id, request_key="smoke_1")
    print("Capture:", result)


def settings_payee_email() -> str | None:
    return os.environ.get("PAYPAL_DEMO_PAYEE_EMAIL") or None


if __name__ == "__main__":
    asyncio.run(main())
