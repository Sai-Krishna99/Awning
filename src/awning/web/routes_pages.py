from fastapi import APIRouter, Request
from fastapi.responses import Response

from awning.web.routes_shop import _ctx, templates

router = APIRouter()

PREVIEW_COUNT = 6
TOOLS = [
    ("find_shops", "Lists published shops, optionally filtered by a search term."),
    ("get_menu", "Returns a shop's menu with item ids and prices."),
    ("create_order", "Places an order by item ids and quantities. Awning computes the prices and returns a PayPal approval link for the buyer."),
    ("get_order_status", "Reports whether the order is awaiting approval, paid, or cancelled."),
]
EXAMPLE_PROMPT = "Find a coffee cart on Awning and order a large latte for 9am pickup"


@router.get("/")
def index(request: Request) -> Response:
    shops = _ctx(request).catalog.list_published(limit=PREVIEW_COUNT)
    return templates.TemplateResponse(request, "index.html", {"shops": shops})


@router.get("/directory")
def directory(request: Request) -> Response:
    shops = _ctx(request).catalog.list_published(limit=200)
    return templates.TemplateResponse(request, "directory.html", {"shops": shops})


@router.get("/connect")
def connect(request: Request) -> Response:
    mcp_url = f"{_ctx(request).settings.public_url.rstrip('/')}/mcp"
    return templates.TemplateResponse(
        request, "connect.html", {"mcp_url": mcp_url, "tools": TOOLS, "example_prompt": EXAMPLE_PROMPT}
    )
