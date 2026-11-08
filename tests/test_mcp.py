import pytest
from fastapi.testclient import TestClient
from mcp import Client

from awning.config import load_settings
from awning.context import AppContext, build_context
from awning.mcp_server import build_mcp
from awning.models import MenuItem, Shop, ShopDetails
from awning.web.app import create_app

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
}
HEADERS = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}


@pytest.fixture
def shop(ctx: AppContext) -> Shop:
    draft = ctx.catalog.create_draft([MenuItem(id="", name="Croissant", price_cents=350)])
    return ctx.catalog.publish(
        draft.admin_token,
        ShopDetails(name="Cart", description="", pickup_location="", pickup_hours="", payee_merchant_id=None, payee_email=None),
    )


async def test_mcp_lists_and_calls_tools(ctx: AppContext, shop: Shop) -> None:
    async with Client(build_mcp(ctx)) as client:
        listed = await client.list_tools()
        assert {t.name for t in listed.tools} == {"find_shops", "get_menu", "create_order", "get_order_status"}
        result = await client.call_tool("get_menu", {"shop_slug": shop.slug})
        assert result.structured_content["items"][0]["price"] == "3.50"
        missing = await client.call_tool("get_menu", {"shop_slug": "nope"})
        assert missing.is_error
        assert "shop_not_found" in missing.content[0].text


async def test_mcp_unknown_item_error_lists_choices(ctx: AppContext, shop: Shop) -> None:
    async with Client(build_mcp(ctx)) as client:
        result = await client.call_tool(
            "create_order",
            {
                "shop_slug": shop.slug,
                "items": [{"item_id": "Croissant", "quantity": 1}],
                "pickup_time": "8am",
                "customer_name": "Ann",
            },
        )
        assert result.is_error
        text = result.content[0].text
        assert "unknown_item" in text and shop.items[0].id in text


def test_mcp_host_check_allows_configured_hosts(tmp_path) -> None:
    settings = load_settings(
        {
            "AWNING_PUBLIC_URL": "https://awning.onrender.com",
            "AWNING_DB_PATH": str(tmp_path / "t.db"),
        }
    )
    with TestClient(create_app(build_context(settings))) as client:
        ok = client.post("/mcp", json=INIT, headers={**HEADERS, "Host": "awning.onrender.com"})
        assert ok.status_code == 200
        bad = client.post("/mcp", json=INIT, headers={**HEADERS, "Host": "evil.example.com"})
        assert bad.status_code == 421


def test_healthz_still_works_with_mcp_mounted(client: TestClient) -> None:
    with client:
        assert client.get("/healthz").status_code == 200
