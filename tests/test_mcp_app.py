import json

import pytest
from mcp import Client

from awning.context import AppContext
from awning.mcp_server import build_mcp
from awning.models import MenuItem, Shop, ShopDetails
from awning.orders import OrderService
from fakes import FakePayPal

CARD_URI = "ui://awning/order-status.html"
APP_MIME = "text/html;profile=mcp-app"


@pytest.fixture(autouse=True)
def fake_paypal(ctx: AppContext) -> FakePayPal:
    paypal = FakePayPal()
    ctx.paypal = paypal
    ctx.orders = OrderService(ctx.db, ctx.catalog, paypal, ctx.events, ctx.settings.public_url, False)
    return paypal


@pytest.fixture
def shop(ctx: AppContext) -> Shop:
    draft = ctx.catalog.create_draft([MenuItem(id="", name="Croissant", price_cents=350)])
    return ctx.catalog.publish(
        draft.admin_token,
        ShopDetails(
            name="Maria's Cart",
            description="",
            pickup_location="Corner of 5th",
            pickup_hours="",
            payee_merchant_id=None,
            payee_email=None,
        ),
    )


async def _create(client: Client, shop: Shop):
    return await client.call_tool(
        "create_order",
        {
            "shop_slug": shop.slug,
            "items": [{"item_id": shop.items[0].id, "quantity": 2}],
            "pickup_time": "8:30am",
            "customer_name": "Ann",
        },
    )


async def test_create_order_declares_card_in_nested_and_flat_meta(ctx: AppContext) -> None:
    async with Client(build_mcp(ctx)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    meta = tools["create_order"].meta
    assert meta["ui"]["resourceUri"] == CARD_URI
    assert meta["ui/resourceUri"] == CARD_URI
    assert set(meta["ui"]["visibility"]) == {"model", "app"}
    for name in ("find_shops", "get_menu", "get_order_status"):
        assert "ui" not in (tools[name].meta or {})
        assert "ui/resourceUri" not in (tools[name].meta or {})


async def test_get_order_status_stays_visible_to_model_and_app(ctx: AppContext) -> None:
    async with Client(build_mcp(ctx)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    visibility = ((tools["get_order_status"].meta or {}).get("ui") or {}).get("visibility")
    assert visibility is None or set(visibility) == {"model", "app"}
    assert tools["get_order_status"].annotations.read_only_hint is True


async def test_card_resource_is_mcp_app_html_without_paypal_or_order_data(ctx: AppContext) -> None:
    async with Client(build_mcp(ctx)) as client:
        result = await client.read_resource(CARD_URI)
    content = result.contents[0]
    assert content.mime_type == APP_MIME
    html = content.text
    assert "<html" in html.lower()
    assert "paypal" not in html.lower()  # no PayPal link, button or reference at all
    assert "callServerTool" in html and "get_order_status" in html and "openLink" in html
    assert "<script src=" not in html and "https://unpkg.com" not in html
    assert (content.meta or {}).get("ui", {}).get("csp") is None


async def test_create_order_structured_content_feeds_card_and_text_keeps_approval_url(
    ctx: AppContext, shop: Shop
) -> None:
    async with Client(build_mcp(ctx)) as client:
        result = await _create(client, shop)
    data = result.structured_content
    assert data["shop_name"] == "Maria's Cart"
    assert data["pickup_time"] == "8:30am"
    assert data["pickup_location"] == "Corner of 5th"
    assert data["total"] == "7.00"
    assert data["status"] == "awaiting_payment"
    assert data["lines"][0]["name"] == "Croissant" and data["lines"][0]["quantity"] == 2
    assert data["status_url"] == f"{ctx.settings.public_url}/o/{data['order_id']}"
    assert data["approval_url"].startswith("https://")
    assert data["approval_url"] in result.content[0].text
    assert json.loads(result.content[0].text)["approval_url"] == data["approval_url"]


async def test_get_order_status_result_for_model_is_unchanged(ctx: AppContext, shop: Shop) -> None:
    async with Client(build_mcp(ctx)) as client:
        created = (await _create(client, shop)).structured_content
        status = await client.call_tool("get_order_status", {"order_id": created["order_id"]})
    data = status.structured_content
    assert data["status"] == "awaiting_payment"
    assert data["shop_name"] == "Maria's Cart"
    assert data["approval_url"] == created["approval_url"]
    assert data["payment_held"] is False
    assert data["message"] == "Payment not completed yet. The buyer can pay at approval_url."


def test_bundle_export_clause_becomes_global_without_external_imports() -> None:
    from awning.mcp_app import render_card_html

    html = render_card_html()
    assert "const __extApps={App:" in html
    assert "export{" not in html and "/*__EXT_APPS_BUNDLE__*/" not in html
    assert "view order page" in html.lower()
