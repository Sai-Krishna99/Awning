import pytest
from fastapi.testclient import TestClient

from awning.config import load_settings
from awning.context import AppContext, build_context
from awning.models import MenuItem, Shop, ShopDetails
from awning.orders import OrderService
from awning.paypal import PayPalError
from awning.web.app import create_app
from fakes import FakePayPal

FOOTER = "Sandbox demo: every payment is a real PayPal sandbox transaction. No real money moves."
HELD = "PayPal is holding this payment for the merchant to accept."
BANNER = "Demo merchant — payments go to Awning's PayPal sandbox demo account."


def make_ctx(tmp_path, **extra) -> AppContext:
    env = {
        "PAYPAL_CLIENT_ID": "test-client",
        "PAYPAL_CLIENT_SECRET": "test-secret",
        "ANTHROPIC_API_KEY": "k",
        "AWNING_DB_PATH": str(tmp_path / "t.db"),
        **extra,
    }
    return build_context(load_settings(env))


def publish(ctx: AppContext, name="Maria's Cart", merchant_id=None, email=None) -> Shop:
    draft = ctx.catalog.create_draft(
        [
            MenuItem(id="", name="Oat Latte", description="Creamy", category="Drinks", price_cents=450),
            MenuItem(id="", name="Croissant", category="Food", price_cents=350),
        ]
    )
    return ctx.catalog.publish(
        draft.admin_token,
        ShopDetails(
            name=name, description="Coffee cart", pickup_location="Corner of 5th",
            pickup_hours="7-11am", payee_merchant_id=merchant_id, payee_email=email,
            payee_email_confirmed=bool(email),
        ),
    )


@pytest.fixture
def paypal() -> FakePayPal:
    return FakePayPal()


@pytest.fixture
def web(ctx: AppContext, paypal: FakePayPal) -> TestClient:
    ctx.paypal = paypal
    ctx.orders = OrderService(ctx.db, ctx.catalog, paypal, ctx.events, ctx.settings.public_url, False)
    return TestClient(create_app(ctx))


@pytest.fixture
def shop(ctx: AppContext, web: TestClient) -> Shop:
    return publish(ctx)


def order_body(shop: Shop) -> dict:
    return {
        "lines": [{"item_id": shop.items[0].id, "option_ids": [], "quantity": 2}],
        "customer_name": "Sam",
        "pickup_time": "8:15",
        "note": "",
    }


def place(web: TestClient, shop: Shop) -> dict:
    resp = web.post(f"/api/s/{shop.slug}/orders", json=order_body(shop))
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_storefront_renders_menu_and_demo_banner(web, shop):
    html = web.get(f"/s/{shop.slug}").text
    assert "Oat Latte" in html and "4.50" in html and "Croissant" in html
    assert BANNER in html and FOOTER in html
    assert shop.items[0].id in html
    assert "sdk/js?client-id=test-client" in html
    assert "test-secret" not in html


def test_demo_shop_uses_platform_merchant_id(tmp_path):
    ctx = make_ctx(tmp_path, PAYPAL_PLATFORM_MERCHANT_ID="PLAT123")
    shop = publish(ctx)
    html = TestClient(create_app(ctx)).get(f"/s/{shop.slug}").text
    assert "merchant-id=PLAT123" in html


def test_demo_shop_omits_merchant_id_when_unset(web, shop):
    assert "merchant-id" not in web.get(f"/s/{shop.slug}").text


def test_merchant_id_shop_uses_its_own_id_and_no_banner(ctx, web):
    shop = publish(ctx, name="Other", merchant_id="MERCH9")
    html = web.get(f"/s/{shop.slug}").text
    assert "merchant-id=MERCH9" in html and BANNER not in html and FOOTER in html


def test_email_only_shop_gets_redirect_button(ctx, web):
    shop = publish(ctx, name="Mail", email="a@b.com")
    html = web.get(f"/s/{shop.slug}").text
    assert "sdk/js" not in html
    assert "Pay with PayPal" in html and 'data-checkout="redirect"' in html


def test_unknown_slug_404(web):
    assert web.get("/s/nope").status_code == 404
    assert web.get("/s/nope/qr.svg").status_code == 404
    assert web.post("/api/s/nope/orders", json={"lines": [], "customer_name": "x", "pickup_time": "1", "note": ""}).status_code == 404


def test_create_web_order_returns_paypal_id(web, shop, ctx, paypal):
    data = place(web, shop)
    assert data["paypal_order_id"] == "PPORDER1"
    order = ctx.orders.get(data["order_id"])
    assert order.channel == "web" and order.total_cents == 900
    assert data["approval_url"] == order.approval_url


def test_create_order_pricing_error_422(web, shop):
    body = order_body(shop)
    body["lines"][0]["item_id"] = "bogus"
    resp = web.post(f"/api/s/{shop.slug}/orders", json=body)
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "unknown_item" and err["valid_choices"]


def test_create_order_input_error_422_with_field(web, shop):
    body = order_body(shop)
    body["customer_name"] = " "
    resp = web.post(f"/api/s/{shop.slug}/orders", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["field"] == "customer_name"


def test_create_order_paypal_failure_502(web, shop, paypal):
    paypal.payee_invalid = True
    resp = web.post(f"/api/s/{shop.slug}/orders", json=order_body(shop))
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "payment_setup_failed"


def test_return_twice_captures_once(web, shop, paypal):
    order_id = place(web, shop)["order_id"]
    for _ in range(2):
        resp = web.get(f"/o/{order_id}/return", follow_redirects=False)
        assert resp.status_code == 303 and resp.headers["location"] == f"/o/{order_id}"
    assert paypal.capture_count == 1
    assert "paid" in web.get(f"/o/{order_id}").text.lower()


def test_return_not_completed_renders_confirming_not_500(web, shop, paypal):
    order_id = place(web, shop)["order_id"]
    paypal.capture_error = PayPalError(500, "INTERNAL_SERVER_ERROR", [], None)
    resp = web.get(f"/o/{order_id}/return", follow_redirects=True)
    assert resp.status_code == 200
    assert "confirming your payment" in resp.text


def test_pending_capture_shows_holding_message_and_stops_polling(web, shop, paypal):
    order_id = place(web, shop)["order_id"]
    paypal.capture_status = "PENDING"
    resp = web.get(f"/o/{order_id}/return", follow_redirects=True)
    assert resp.status_code == 200
    assert HELD in resp.text
    assert "confirming your payment" not in resp.text
    assert "Continue to PayPal" not in resp.text
    assert 'data-held="true"' in resp.text
    body = web.get(f"/api/orders/{order_id}").json()
    assert body["status"] == "awaiting_payment"
    assert body["payment_held"] is True and body["message"] == HELD


def test_capture_api(web, shop, paypal):
    order_id = place(web, shop)["order_id"]
    resp = web.post(f"/api/orders/{order_id}/capture")
    assert resp.json() == {"order_id": order_id, "status": "paid"}
    paypal.capture_status = "PENDING"
    other = place(web, shop)["order_id"]
    resp = web.post(f"/api/orders/{other}/capture")
    assert resp.status_code == 200 and resp.json()["status"] == "awaiting_payment"
    assert web.post("/api/orders/ord_nope/capture").status_code == 404


def test_cancelled_page_offers_retry(web, shop):
    data = place(web, shop)
    resp = web.get(f"/o/{data['order_id']}/cancelled")
    assert "Payment not completed" in resp.text
    assert data["approval_url"] in resp.text
    assert FOOTER in resp.text


def test_order_status_api(web, shop, paypal):
    order_id = place(web, shop)["order_id"]
    body = web.get(f"/api/orders/{order_id}").json()
    assert body == {
        "order_id": order_id, "status": "awaiting_payment", "total": "9.00",
        "pickup_time": "8:15", "shop_name": "Maria's Cart", "payment_held": False,
    }
    paypal.order_statuses["PPORDER1"] = "APPROVED"
    assert web.get(f"/api/orders/{order_id}").json()["status"] == "paid"
    assert web.get("/api/orders/ord_nope").status_code == 404


def test_order_status_api_survives_incomplete_capture(web, shop, paypal):
    order_id = place(web, shop)["order_id"]
    paypal.order_statuses["PPORDER1"] = "APPROVED"
    paypal.capture_status = "PENDING"
    resp = web.get(f"/api/orders/{order_id}")
    assert resp.status_code == 200 and resp.json()["status"] == "awaiting_payment"


def test_order_page(web, shop):
    order_id = place(web, shop)["order_id"]
    html = web.get(f"/o/{order_id}").text
    assert "Sam" in html and "9.00" in html and FOOTER in html
    assert web.get("/o/ord_nope").status_code == 404
    assert web.get("/o/ord_nope/return").status_code == 404
    assert web.get("/o/ord_nope/cancelled").status_code == 404


def test_qr_svg(web, shop):
    resp = web.get(f"/s/{shop.slug}/qr.svg")
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in resp.text


def test_static_assets_served(web):
    assert web.get("/static/shop.js").status_code == 200
    assert web.get("/static/app.css").status_code == 200
