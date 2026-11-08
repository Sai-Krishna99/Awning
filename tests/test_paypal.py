import json

import httpx
import pytest

from awning.models import PricedLine, PricedOrder
from awning.paypal import PayeeInvalidError, PayPalClient, PayPalError

BASE = "https://paypal.test"


def make_priced() -> PricedOrder:
    return PricedOrder(
        lines=[
            PricedLine(item_id="a", name="Taco", option_names=["Spicy"], unit_price_cents=525,
                       quantity=2, line_total_cents=1050),
            PricedLine(item_id="b", name="Horchata", option_names=[], unit_price_cents=350,
                       quantity=1, line_total_cents=350),
        ],
        total_cents=1400,
    )


def make_client(handler) -> tuple[PayPalClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 32400})
        return handler(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    return PayPalClient("cid", "secret", BASE, http=http), seen


def order_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "PP1", "status": "PAYER_ACTION_REQUIRED",
        "links": [
            {"rel": "self", "href": "https://x/self"},
            {"rel": "payer-action", "href": "https://paypal.test/checkoutnow?token=PP1"},
        ],
    })


async def create(client: PayPalClient, **overrides):
    kwargs = dict(reference_id="ord_x", priced=make_priced(), payee_merchant_id=None,
                  payee_email=None, brand_name="Maria's Cart", description="Order",
                  return_url="https://a/r", cancel_url="https://a/c")
    kwargs.update(overrides)
    return await client.create_order(**kwargs)


async def test_token_cached_across_calls():
    client, seen = make_client(order_response)
    await create(client)
    await create(client)
    assert sum(r.url.path == "/v1/oauth2/token" for r in seen) == 1


async def test_create_order_body():
    client, seen = make_client(order_response)
    order = await create(client, payee_merchant_id="MERCH1", payee_email="m@x.com")
    assert order.id == "PP1"
    assert order.approval_url == "https://paypal.test/checkoutnow?token=PP1"
    req = seen[-1]
    assert req.headers["PayPal-Request-Id"] == "create-ord_x"
    body = json.loads(req.content)
    assert body["intent"] == "CAPTURE"
    unit = body["purchase_units"][0]
    assert unit["reference_id"] == unit["custom_id"] == "ord_x"
    assert unit["amount"]["value"] == "14.00"
    assert unit["amount"]["breakdown"]["item_total"]["value"] == "14.00"
    assert unit["items"][0]["unit_amount"]["value"] == "5.25"
    assert unit["items"][0]["quantity"] == "2"
    assert unit["payee"] == {"merchant_id": "MERCH1"}
    ctx = body["payment_source"]["paypal"]["experience_context"]
    assert ctx["shipping_preference"] == "NO_SHIPPING"
    assert ctx["user_action"] == "PAY_NOW"
    assert ctx["return_url"] == "https://a/r"


async def test_create_order_payee_email_and_none():
    client, seen = make_client(order_response)
    await create(client, payee_email="m@x.com")
    assert json.loads(seen[-1].content)["purchase_units"][0]["payee"] == {"email_address": "m@x.com"}
    await create(client)
    assert "payee" not in json.loads(seen[-1].content)["purchase_units"][0]


async def test_capture_already_captured_is_idempotent():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(422, json={
                "name": "UNPROCESSABLE_ENTITY", "debug_id": "d1",
                "details": [{"issue": "ORDER_ALREADY_CAPTURED"}],
            })
        return httpx.Response(200, json={"id": "PP1", "status": "COMPLETED", "purchase_units": [{
            "payee": {"email_address": "m@x.com", "merchant_id": "M1"},
            "payments": {"captures": [{"id": "CAP1", "status": "COMPLETED"}]},
        }]})

    client, _ = make_client(handler)
    result = await client.capture_order("PP1", request_key="k")
    assert result.capture_id == "CAP1"
    assert result.status == "COMPLETED"
    assert result.payee_merchant_id == "M1"


async def test_capture_without_capture_object_is_not_completed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"id": "PP1", "status": "COMPLETED", "purchase_units": [{}]})

    client, _ = make_client(handler)
    result = await client.capture_order("PP1", request_key="k")
    assert result.status != "COMPLETED"
    assert result.capture_id is None


async def test_brand_name_truncated_to_paypal_limit():
    client, seen = make_client(order_response)
    await create(client, brand_name="B" * 200)
    ctx = json.loads(seen[-1].content)["payment_source"]["paypal"]["experience_context"]
    assert ctx["brand_name"] == "B" * 127


async def test_capture_sends_request_id():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"id": "PP1", "status": "COMPLETED", "purchase_units": [{
            "payments": {"captures": [{"id": "CAP1", "status": "COMPLETED"}]}}]})

    client, seen = make_client(handler)
    result = await client.capture_order("PP1", request_key="k")
    assert seen[-1].headers["PayPal-Request-Id"] == "capture-k"
    assert result.capture_id == "CAP1"


async def test_payee_invalid_maps_to_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={
            "name": "UNPROCESSABLE_ENTITY", "debug_id": "d2",
            "details": [{"issue": "PAYEE_ACCOUNT_INVALID"}],
        })

    client, _ = make_client(handler)
    with pytest.raises(PayeeInvalidError) as exc:
        await create(client, payee_merchant_id="BAD")
    assert exc.value.status_code == 422
    assert exc.value.issues == ["PAYEE_ACCOUNT_INVALID"]
    assert isinstance(exc.value, PayPalError)


async def test_refund_capture_returns_status():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/payments/captures/CAP1/refund"
        assert request.headers["PayPal-Request-Id"] == "refund-k"
        return httpx.Response(201, json={"id": "R1", "status": "COMPLETED"})

    client, _ = make_client(handler)
    assert await client.refund_capture("CAP1", request_key="k") == "COMPLETED"


async def test_verify_webhook_true_on_success_status():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["webhook_id"] == "WH1"
        assert body["transmission_id"] == "t1"
        assert body["webhook_event"] == {"id": "EV1"}
        return httpx.Response(200, json={"verification_status": "SUCCESS"})

    client, _ = make_client(handler)
    headers = {"PayPal-Transmission-Id": "t1", "paypal-transmission-time": "now",
               "paypal-transmission-sig": "s", "paypal-cert-url": "u", "paypal-auth-algo": "a"}
    assert await client.verify_webhook(headers, b'{"id": "EV1"}', "WH1") is True


async def test_verify_webhook_false_on_failure_or_bad_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"verification_status": "FAILURE"})

    client, _ = make_client(handler)
    assert await client.verify_webhook({}, b'{"id": "EV1"}', "WH1") is False
    assert await client.verify_webhook({}, b"not json", "WH1") is False
