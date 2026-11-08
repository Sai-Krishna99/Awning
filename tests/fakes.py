from collections.abc import Mapping
from typing import Any

from awning.models import PricedOrder
from awning.paypal import CaptureResult, PayeeInvalidError, PayPalOrder


class FakePayPal:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.capture_count = 0
        self.order_statuses: dict[str, str] = {}
        self.payee_invalid = False
        self.webhook_valid = True
        self.default_payee_email: str | None = "merchant@example.com"
        self.default_payee_merchant_id: str | None = "MERCHANT1"
        self._order_counter = 0

    def _record(self, method: str, **kwargs: Any) -> None:
        self.calls.append((method, kwargs))

    def calls_to(self, method: str) -> list[dict[str, Any]]:
        return [kwargs for name, kwargs in self.calls if name == method]

    async def create_order(
        self,
        *,
        reference_id: str,
        priced: PricedOrder,
        payee_merchant_id: str | None,
        payee_email: str | None,
        brand_name: str,
        description: str,
        return_url: str,
        cancel_url: str,
    ) -> PayPalOrder:
        self._record(
            "create_order",
            reference_id=reference_id,
            priced=priced,
            payee_merchant_id=payee_merchant_id,
            payee_email=payee_email,
            brand_name=brand_name,
            description=description,
            return_url=return_url,
            cancel_url=cancel_url,
        )
        if self.payee_invalid:
            raise PayeeInvalidError(422, "UNPROCESSABLE_ENTITY", ["PAYEE_ACCOUNT_INVALID"], "fake")
        self._order_counter += 1
        order_id = f"PPORDER{self._order_counter}"
        self.order_statuses[order_id] = "PAYER_ACTION_REQUIRED"
        return PayPalOrder(
            id=order_id,
            status="PAYER_ACTION_REQUIRED",
            approval_url=f"https://www.sandbox.paypal.com/checkoutnow?token={order_id}",
        )

    async def capture_order(self, paypal_order_id: str, *, request_key: str) -> CaptureResult:
        self._record("capture_order", paypal_order_id=paypal_order_id, request_key=request_key)
        self.capture_count += 1
        self.order_statuses[paypal_order_id] = "COMPLETED"
        return CaptureResult(
            status="COMPLETED",
            capture_id=f"CAP-{paypal_order_id}",
            payee_email=self.default_payee_email,
            payee_merchant_id=self.default_payee_merchant_id,
        )

    async def get_order(self, paypal_order_id: str) -> dict:
        self._record("get_order", paypal_order_id=paypal_order_id)
        return {"id": paypal_order_id, "status": self.order_statuses.get(paypal_order_id, "CREATED")}

    async def refund_capture(self, capture_id: str, *, request_key: str) -> str:
        self._record("refund_capture", capture_id=capture_id, request_key=request_key)
        return "COMPLETED"

    async def verify_webhook(
        self, headers: Mapping[str, str], body: bytes, webhook_id: str
    ) -> bool:
        self._record("verify_webhook", headers=headers, body=body, webhook_id=webhook_id)
        return self.webhook_valid
