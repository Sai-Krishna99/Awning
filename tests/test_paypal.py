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


def _identity_client(userinfo: dict | None = None, token_status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/identity/oauth2/userinfo":
            return httpx.Response(200, json=userinfo if userinfo is not None else {
                "payer_id": "PAYER12345", "verified_account": "true",
                "emails": [{"value": "alt@x.com", "primary": False}, {"value": "biz@x.com", "primary": True}],
            })
        return httpx.Response(404, json={})

    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/oauth2/token":
            if token_status != 200:
                return httpx.Response(token_status, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "USERTOKEN", "refresh_token": "REFRESHTOK", "expires_in": 28800})
        return handler(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    return PayPalClient("cid", "secret", BASE, http=http), seen


async def test_fetch_login_identity_exchanges_code_and_reads_userinfo():
    client, seen = _identity_client()
    identity = await client.fetch_login_identity("AUTHCODE")
    assert (identity.payer_id, identity.email, identity.verified_account) == ("PAYER12345", "biz@x.com", True)
    token_request, userinfo_request = seen
    assert token_request.content == b"grant_type=authorization_code&code=AUTHCODE"
    assert token_request.headers["authorization"].startswith("Basic ")
    assert userinfo_request.url.params["schema"] == "paypalv1.1"
    assert userinfo_request.headers["authorization"] == "Bearer USERTOKEN"


async def test_fetch_login_identity_never_logs_tokens_or_code(caplog):
    caplog.set_level("DEBUG")
    client, _ = _identity_client()
    await client.fetch_login_identity("AUTHCODE")
    for secret in ("AUTHCODE", "USERTOKEN", "REFRESHTOK"):
        assert secret not in caplog.text


async def test_fetch_login_identity_bad_code_raises():
    client, _ = _identity_client(token_status=400)
    with pytest.raises(PayPalError) as info:
        await client.fetch_login_identity("AUTHCODE")
    assert info.value.status_code == 400 and "AUTHCODE" not in str(info.value)


async def test_fetch_login_identity_missing_payer_id_raises():
    client, _ = _identity_client(userinfo={"emails": []})
    with pytest.raises(PayPalError):
        await client.fetch_login_identity("AUTHCODE")
