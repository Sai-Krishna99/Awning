import json
from urllib.parse import urlparse

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as McpToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from awning import tools
from awning.context import AppContext
from awning.mcp_app import CARD_URI, build_apps

_SPECS = {spec.name: spec for spec in tools.TOOL_SPECS}


class ShopSummary(BaseModel):
    slug: str
    name: str
    description: str
    pickup_location: str
    pickup_hours: str
    storefront_url: str


class FindShopsResult(BaseModel):
    shops: list[ShopSummary]


class MenuOption(BaseModel):
    id: str
    name: str
    price_delta: str


class MenuItemOut(BaseModel):
    id: str
    name: str
    description: str
    category: str
    price: str
    options: list[MenuOption]


class MenuResult(BaseModel):
    shop: ShopSummary
    items: list[MenuItemOut]
    note: str


class OrderLineIn(BaseModel):
    item_id: str = Field(min_length=1)
    option_ids: list[str] = []
    quantity: int = Field(ge=1, le=20)


class OrderLineOut(BaseModel):
    item_id: str
    name: str
    options: list[str]
    quantity: int
    unit_price: str
    line_total: str


class CreateOrderResult(BaseModel):
    order_id: str
    status: str
    lines: list[OrderLineOut]
    total: str
    currency: str
    shop_name: str = ""
    pickup_time: str = ""
    pickup_location: str = ""
    approval_url: str | None
    status_url: str
    next_step: str


class OrderStatusResult(BaseModel):
    order_id: str
    status: str
    shop_name: str
    pickup_time: str
    total: str
    payment_held: bool = False
    approval_url: str | None = None
    message: str


def _raise(exc: tools.ToolError) -> McpToolError:
    text = f"{exc.code}: {exc.message}"
    if exc.details:
        text += f" Details: {json.dumps(exc.details)}"
    return McpToolError(text)


def allowed_hosts_for(configured: list[str]) -> list[str]:
    hosts: list[str] = ["testserver"]
    for entry in configured:
        bare = entry.rsplit(":", 1)[0] if entry.count(":") == 1 else entry
        for candidate in (bare, f"{bare}:*"):
            if candidate not in hosts:
                hosts.append(candidate)
    return hosts


def build_transport_security(ctx: AppContext) -> TransportSecuritySettings:
    public = urlparse(ctx.settings.public_url)
    origins = [
        f"{public.scheme}://{public.netloc}",
        "https://claude.ai",
        "https://www.claude.ai",
        "https://claude.com",
        "https://chatgpt.com",
        "https://chat.openai.com",
        "http://localhost:*",
        "http://127.0.0.1:*",
    ]
    return TransportSecuritySettings(
        allowed_hosts=allowed_hosts_for(ctx.settings.allowed_hosts), allowed_origins=origins
    )


def build_mcp(ctx: AppContext) -> MCPServer:
    apps = build_apps()

    async def run(name: str, **args) -> dict:
        try:
            return await getattr(tools, name)(ctx, **args)
        except tools.ToolError as exc:
            raise _raise(exc) from exc

    @apps.tool(
        resource_uri=CARD_URI,
        visibility=["model", "app"],
        meta={"ui/resourceUri": CARD_URI},
        name="create_order",
        description=_SPECS["create_order"].description,
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True),
    )
    async def create_order(
        shop_slug: str,
        items: list[OrderLineIn],
        pickup_time: str,
        customer_name: str,
        note: str = "",
    ) -> CreateOrderResult:
        # Global limit only: Claude.ai connector calls arrive from Anthropic's shared IPs, so a per-IP
        # bucket would make every connector user share one quota.
        if not ctx.allow_order(None):
            raise _raise(tools.ToolError("rate_limited", "Too many orders right now. Please try again later."))
        result = await run(
            "create_order",
            shop_slug=shop_slug,
            items=[i.model_dump() for i in items],
            pickup_time=pickup_time,
            customer_name=customer_name,
            note=note,
            channel="agent",
        )
        return CreateOrderResult(**result)

    mcp = MCPServer(
        "Awning",
        extensions=[apps],
        instructions="Find small local shops and place pickup orders that the buyer pays for with PayPal.",
    )
    read_only = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

    @mcp.tool(name="find_shops", description=_SPECS["find_shops"].description, annotations=read_only)
    async def find_shops(query: str, location_hint: str = "") -> FindShopsResult:
        return FindShopsResult(**await run("find_shops", query=query, location_hint=location_hint))

    @mcp.tool(name="get_menu", description=_SPECS["get_menu"].description, annotations=read_only)
    async def get_menu(shop_slug: str) -> MenuResult:
        return MenuResult(**await run("get_menu", shop_slug=shop_slug))

    @mcp.tool(name="get_order_status", description=_SPECS["get_order_status"].description, annotations=read_only)
    async def get_order_status(order_id: str) -> OrderStatusResult:
        return OrderStatusResult(**await run("get_order_status", order_id=order_id))

    return mcp
