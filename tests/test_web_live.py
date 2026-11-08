import asyncio

import pytest

from awning.models import OrderLineRequest
from awning.web.routes_board import public_event_stream
from test_web_board import pay
from test_web_shop import paypal, place, publish, shop, web  # noqa: F401 (fixtures)

NOTE = "Platform 9 please"
PUBLIC_KEYS = {"id", "status", "channel", "lines", "total_cents", "customer_name", "pickup_time", "created_at"}


def place_with_note(web, shop, name="Sam") -> dict:
    body = {
        "lines": [{"item_id": shop.items[0].id, "option_ids": [], "quantity": 1}],
        "customer_name": name, "pickup_time": "8:15", "note": NOTE,
    }
    response = web.post(f"/api/s/{shop.slug}/orders", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_non_demo_slug_is_404_on_all_routes(ctx, web, shop):
    other = publish(ctx, name="Other Cart")
    for slug in (other.slug, "nope"):
        assert web.get(f"/s/{slug}/live").status_code == 404
        assert web.get(f"/api/s/{slug}/live/orders").status_code == 404
        assert web.get(f"/api/s/{slug}/live/events").status_code == 404


def test_live_orders_masks_names_and_hides_private_fields(web, shop):
    place_with_note(web, shop, name="Sam")
    response = web.get(f"/api/s/{shop.slug}/live/orders")
    assert response.status_code == 200
    (order,) = response.json()["orders"]
    assert order["customer_name"] == "S."
    assert set(order) == PUBLIC_KEYS
    assert NOTE not in response.text and "Sam" not in response.text
    assert shop.admin_token not in response.text and "approval" not in response.text


def test_live_events_mask_names(ctx, web, shop):
    async def scenario():
        stream = public_event_stream(ctx, shop.id)
        await asyncio.wait_for(stream.__anext__(), 2)
        order = await ctx.orders.place_order(
            shop.slug, [OrderLineRequest(item_id=shop.items[0].id, option_ids=[], quantity=1)],
            "Sam", "8", NOTE, "web")
        created = await asyncio.wait_for(stream.__anext__(), 2)
        await ctx.orders.complete_payment(order.id)
        paid = await asyncio.wait_for(stream.__anext__(), 2)
        await stream.aclose()
        return created, paid

    created, paid = asyncio.run(scenario())
    for event in (created, paid):
        assert event.data["customer_name"] == "S."
        assert set(event.data) == PUBLIC_KEYS
    assert paid.event == "order_paid"


def test_live_page_is_read_only(web, shop):
    response = web.get(f"/s/{shop.slug}/live")
    assert response.status_code == 200
    assert "Public demo board for Maria's Cart, a fictional shop." in response.text
    assert "Customer names are shortened." in response.text
    assert 'data-readonly="true"' in response.text
    assert 'id="sound-toggle"' in response.text
    assert shop.admin_token not in response.text
    assert "/api/s/marias-cart/live" in response.text


def test_public_routes_expose_no_actions(web, shop):
    order = place_with_note(web, shop)
    pay(web, order)
    for suffix in ("status", "refund"):
        url = f"/api/s/{shop.slug}/live/orders/{order['order_id']}/{suffix}"
        assert web.post(url, json={"status": "ready"}).status_code in (404, 405)
    assert web.post(f"/api/s/{shop.slug}/live/orders", json={}).status_code in (404, 405)
    assert web.get(f"/api/m/{shop.slug}/orders").status_code == 404


def test_token_board_unchanged(web, shop):
    order = place_with_note(web, shop)
    page = web.get(f"/m/{shop.admin_token}/board")
    assert page.status_code == 200
    assert 'data-readonly' not in page.text and "Public demo board" not in page.text
    orders = web.get(f"/api/m/{shop.admin_token}/orders").json()["orders"]
    assert orders[0]["customer_name"] == "Sam" and orders[0]["note"] == NOTE


def test_demo_links(ctx, web, shop):
    other = publish(ctx, name="Other Cart")
    assert "Watch Maria's live order board" in web.get("/directory").text
    assert web.get("/directory").text.count("/s/marias-cart/live") == 1
    assert "/s/marias-cart/live" in web.get("/").text
    assert "/s/marias-cart/live" in web.get(f"/s/{shop.slug}").text
    assert "/live" not in web.get(f"/s/{other.slug}").text


@pytest.mark.parametrize("path", ["/directory", "/connect", "/start", "/", "/s/marias-cart"])
def test_site_nav_present(web, shop, path):
    text = web.get(path).text
    for href in ('href="/directory"', 'href="/start"', 'href="/connect"'):
        assert href in text
    assert "Open a shop" in text


def test_nav_hidden_on_board_and_review(web, shop):
    assert "Open a shop" not in web.get(f"/m/{shop.admin_token}/board").text


def test_directory_banner(web, shop):
    assert "Turn your menu into one.</a>" in web.get("/directory").text
