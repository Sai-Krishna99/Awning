import json
import time
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from awning.models import PricedOrder
from awning.pricing import format_cents

PAYEE_INVALID_ISSUE = "PAYEE_ACCOUNT_INVALID"
ALREADY_CAPTURED_ISSUE = "ORDER_ALREADY_CAPTURED"
TOKEN_EXPIRY_MARGIN_SECONDS = 60
CURRENCY = "USD"


@dataclass
class PayPalOrder:
    id: str
    status: str
    approval_url: str | None


@dataclass
class CaptureResult:
    status: str
    capture_id: str | None
    payee_email: str | None
    payee_merchant_id: str | None


class PayPalError(Exception):
    def __init__(self, status_code: int, name: str, issues: list[str], debug_id: str | None) -> None:
        super().__init__(f"PayPal {status_code} {name} {issues} debug_id={debug_id}")
        self.status_code = status_code
        self.name = name
        self.issues = issues
        self.debug_id = debug_id


class PayeeInvalidError(PayPalError):
    pass


def _money(cents: int) -> dict:
    return {"currency_code": CURRENCY, "value": format_cents(cents)}


def _item_name(name: str, option_names: list[str]) -> str:
    full = f"{name} ({', '.join(option_names)})" if option_names else name
    return full[:127]


def _approval_url(links: list[dict]) -> str | None:
    by_rel = {link["rel"]: link["href"] for link in links}
    return by_rel.get("payer-action") or by_rel.get("approve")


def _capture_result(order: dict) -> CaptureResult:
    unit = (order.get("purchase_units") or [{}])[0]
    captures = unit.get("payments", {}).get("captures") or []
    capture = captures[0] if captures else {}
    payee = unit.get("payee") or {}
    return CaptureResult(
        status=capture.get("status") or order.get("status", ""),
        capture_id=capture.get("id"),
        payee_email=payee.get("email_address"),
        payee_merchant_id=payee.get("merchant_id"),
    )


class PayPalClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._base_url = base_url.rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=30)
        self._token: str | None = None
        self._token_expires_at = 0.0

    async def _access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        response = await self._http.post(
            f"{self._base_url}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
        )
        self._raise_for_error(response)
        data = response.json()
        self._token = data["access_token"]
        self._token_expires_at = (
            time.monotonic() + int(data["expires_in"]) - TOKEN_EXPIRY_MARGIN_SECONDS
        )
        return self._token

    @staticmethod
    def _raise_for_error(response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        issues = [d.get("issue", "") for d in data.get("details") or [] if isinstance(d, dict)]
        name = data.get("name") or data.get("error") or "UNKNOWN"
        error_class = PayeeInvalidError if PAYEE_INVALID_ISSUE in issues else PayPalError
        raise error_class(response.status_code, name, issues, data.get("debug_id"))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        request_id: str | None = None,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {await self._access_token()}"}
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        response = await self._http.request(
            method, f"{self._base_url}{path}", json=json_body, headers=headers
        )
        self._raise_for_error(response)
        return response

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
        unit: dict = {
            "reference_id": reference_id,
            "custom_id": reference_id,
            "description": description[:127],
            "items": [
                {
                    "name": _item_name(line.name, line.option_names),
                    "unit_amount": _money(line.unit_price_cents),
                    "quantity": str(line.quantity),
                }
                for line in priced.lines
            ],
            "amount": {
                **_money(priced.total_cents),
                "breakdown": {"item_total": _money(priced.total_cents)},
            },
        }
        if payee_merchant_id:
            unit["payee"] = {"merchant_id": payee_merchant_id}
        elif payee_email:
            unit["payee"] = {"email_address": payee_email}
        body = {
            "intent": "CAPTURE",
            "purchase_units": [unit],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "brand_name": brand_name,
                        "shipping_preference": "NO_SHIPPING",
                        "user_action": "PAY_NOW",
                        "return_url": return_url,
                        "cancel_url": cancel_url,
                    }
                }
            },
        }
        response = await self._request(
            "POST", "/v2/checkout/orders", json_body=body, request_id=f"create-{reference_id}"
        )
        data = response.json()
        return PayPalOrder(
            id=data["id"], status=data["status"], approval_url=_approval_url(data.get("links", []))
        )

    async def capture_order(self, paypal_order_id: str, *, request_key: str) -> CaptureResult:
        try:
            response = await self._request(
                "POST",
                f"/v2/checkout/orders/{paypal_order_id}/capture",
                json_body={},
                request_id=f"capture-{request_key}",
            )
        except PayPalError as error:
            if error.status_code == 422 and ALREADY_CAPTURED_ISSUE in error.issues:
                return _capture_result(await self.get_order(paypal_order_id))
            raise
        return _capture_result(response.json())

    async def get_order(self, paypal_order_id: str) -> dict:
        return (await self._request("GET", f"/v2/checkout/orders/{paypal_order_id}")).json()

    async def refund_capture(self, capture_id: str, *, request_key: str) -> str:
        response = await self._request(
            "POST",
            f"/v2/payments/captures/{capture_id}/refund",
            json_body={},
            request_id=f"refund-{request_key}",
        )
        return response.json()["status"]

    async def verify_webhook(
        self, headers: Mapping[str, str], body: bytes, webhook_id: str
    ) -> bool:
        lowered = {key.lower(): value for key, value in headers.items()}
        try:
            event = json.loads(body)
        except ValueError:
            return False
        payload = {
            "auth_algo": lowered.get("paypal-auth-algo"),
            "cert_url": lowered.get("paypal-cert-url"),
            "transmission_id": lowered.get("paypal-transmission-id"),
            "transmission_sig": lowered.get("paypal-transmission-sig"),
            "transmission_time": lowered.get("paypal-transmission-time"),
            "webhook_id": webhook_id,
            "webhook_event": event,
        }
        try:
            response = await self._request(
                "POST", "/v1/notifications/verify-webhook-signature", json_body=payload
            )
        except PayPalError:
            return False
        return response.json().get("verification_status") == "SUCCESS"
