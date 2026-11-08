import asyncio
from datetime import datetime, timedelta, timezone

from fastapi.sse import ServerSentEvent

from awning.models import OrderLineRequest
from awning.web.routes_board import shop_event_stream
from test_web_shop import paypal, place, publish, shop, web  # noqa: F401 (fixtures)

PAYPAL_ACTIVITY = "https://www.sandbox.paypal.com/myaccount/activities/"


def pay(web, order: dict) -> None:
    web.get(f"/o/{order['order_id']}/return", follow_redirects=False)


def test_board_requires_valid_token(web, shop):
    assert web.get("/m/nope/board").status_code == 404
    assert web.get("/api/m/nope/orders").status_code == 404
    assert web.get("/api/m/nope/events").status_code == 404
    assert web.post("/api/m/nope/orders/x/status", json={"status": "ready"}).status_code == 404
    assert web.post("/api/m/nope/orders/x/refund").status_code == 404


def test_board_page_headers(web, shop):
    response = web.get(f"/m/{shop.admin_token}/board")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert 'name="robots" content="noindex"' in response.text
    assert "Sandbox demo: every payment is a real PayPal sandbox transaction. No real money moves." in response.text
    assert "test-secret" not in response.text


def test_orders_list_newest_first_and_scoped(ctx, web, shop):
    other = publish(ctx, name="Other")
    first, second = place(web, shop), place(web, shop)
    web.post(f"/api/s/{other.slug}/orders", json={
        "lines": [{"item_id": other.items[0].id, "option_ids": [], "quantity": 1}],
        "customer_name": "Zed", "pickup_time": "9", "note": ""})
    response = web.get(f"/api/m/{shop.admin_token}/orders")
    assert response.headers["cache-control"] == "no-store"
    ids = [o["id"] for o in response.json()["orders"]]
    assert ids == [second["order_id"], first["order_id"]]


def test_events_stream_delivers_order_paid(ctx, web, shop):
    async def scenario() -> list[ServerSentEvent]:
        stream = shop_event_stream(ctx, shop.id)
        received = []
        received.append(await asyncio.wait_for(stream.__anext__(), 2))
        order = await ctx.orders.place_order(
            shop.slug, [OrderLineRequest(item_id=shop.items[0].id, option_ids=[], quantity=1)],
            "Sam", "8", "", "web")
        received.append(await asyncio.wait_for(stream.__anext__(), 2))
        await ctx.orders.complete_payment(order.id)
        received.append(await asyncio.wait_for(stream.__anext__(), 2))
        await stream.aclose()
        return received

    hello, created, paid = asyncio.run(scenario())
    assert hello.comment == "connected"
    assert created.event == "order_created" and paid.event == "order_paid"
    assert paid.data["id"] and paid.data["status"] == "paid"
    assert "approval_url" not in paid.data and "capture_id" not in paid.data
    assert ctx.events._queues.get(shop.id) is None


def test_status_buttons(web, shop):
    order = place(web, shop)
    base = f"/api/m/{shop.admin_token}/orders/{order['order_id']}"
    assert web.post(base + "/status", json={"status": "ready"}).status_code == 409
    pay(web, order)
    assert web.post(base + "/status", json={"status": "picked_up"}).status_code == 409
    ready = web.post(base + "/status", json={"status": "ready"})
    assert ready.status_code == 200 and ready.json()["status"] == "ready"
    done = web.post(base + "/status", json={"status": "picked_up"})
    assert done.json()["status"] == "picked_up"
    assert web.post(base + "/status", json={"status": "bogus"}).status_code == 422
    assert web.post(f"/api/m/{shop.admin_token}/orders/order_x/status", json={"status": "ready"}).status_code == 404


def test_refund_409_for_merchant_payee(ctx, web):
    shop = publish(ctx, name="Third", email="x@y.com")
    order = place(web, shop)
    pay(web, order)
    response = web.post(f"/api/m/{shop.admin_token}/orders/{order['order_id']}/refund")
    assert response.status_code == 409
    assert response.json() == {
        "message": "Refund this order from your PayPal account.",
        "paypal_url": PAYPAL_ACTIVITY,
    }


def test_refund_demo_payee_succeeds(ctx, web, shop):
    order = place(web, shop)
    pay(web, order)
    response = web.post(f"/api/m/{shop.admin_token}/orders/{order['order_id']}/refund")
    assert response.status_code == 200 and response.json()["status"] == "refunded"


def test_refund_unpaid_is_409_and_failure_is_502(ctx, web, paypal, shop):
    order = place(web, shop)
    url = f"/api/m/{shop.admin_token}/orders/{order['order_id']}/refund"
    assert web.post(url).status_code == 409
    pay(web, order)

    async def boom(*a, **k):
        from awning.paypal import PayPalError
        raise PayPalError(500, "X", [], "no")
    paypal.refund_capture = boom
    response = web.post(url)
    assert response.status_code == 502 and response.json()["message"].startswith("refund failed")


def _age(ctx, order_id: str, minutes: int) -> None:
    created = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    ctx.db.conn.execute("UPDATE orders SET created_at = ? WHERE id = ?", (created, order_id))
    ctx.db.conn.commit()


def test_board_hides_stale_awaiting_orders(ctx, web, shop):
    stale, fresh, old_paid = place(web, shop), place(web, shop), place(web, shop)
    pay(web, old_paid)
    _age(ctx, stale["order_id"], 61)
    _age(ctx, fresh["order_id"], 59)
    _age(ctx, old_paid["order_id"], 600)
    ids = {o["id"] for o in web.get(f"/api/m/{shop.admin_token}/orders").json()["orders"]}
    assert ids == {fresh["order_id"], old_paid["order_id"]}


def test_status_change_publishes_on_event_loop(ctx, web, shop):
    order = place(web, shop)
    pay(web, order)
    on_loop: list[bool] = []
    original = ctx.events.publish

    def recording(shop_id, event):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        original(shop_id, event)

    ctx.events.publish = recording
    response = web.post(f"/api/m/{shop.admin_token}/orders/{order['order_id']}/status", json={"status": "ready"})
    assert response.status_code == 200
    assert on_loop == [True]
