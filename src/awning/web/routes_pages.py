from fastapi import APIRouter, Request
from fastapi.responses import Response

from awning.web.routes_shop import _ctx, _not_found, templates

router = APIRouter()
fallback_router = APIRouter()

PREVIEW_COUNT = 6
TOOLS = [
    ("find_shops", "Lists published shops, optionally filtered by a search term."),
    ("get_menu", "Returns a shop's menu with item ids and prices."),
    ("create_order", "Places an order by item ids and quantities. Awning computes the prices and returns a PayPal approval link for the buyer."),
    ("get_order_status", "Reports whether the order is awaiting payment, paid, ready, picked up or refunded."),
]
FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="7" fill="#FBF7F0"/>'
    '<path fill="#1F4D3A" d="M4 6h6v10a3 3 0 0 1-6 0zM16 6h6v10a3 3 0 0 1-6 0z"/>'
    '<path fill="#C8553D" d="M10 6h6v10a3 3 0 0 1-6 0zM22 6h6v10a3 3 0 0 1-6 0z"/>'
    '<rect x="6" y="23" width="20" height="3" rx="1.5" fill="#1F4D3A"/>'
    "</svg>"
)
EXAMPLE_PROMPT = "Find a coffee cart on Awning and order a large latte for 9am pickup"


@router.get("/")
def index(request: Request) -> Response:
    shops = _ctx(request).catalog.list_published(limit=PREVIEW_COUNT)
    return templates.TemplateResponse(request, "index.html", {"shops": shops})


@router.get("/directory")
def directory(request: Request) -> Response:
    shops = _ctx(request).catalog.list_published(limit=200)
    return templates.TemplateResponse(request, "directory.html", {"shops": shops})


def _mcp_url(request: Request) -> str:
    return f"{_ctx(request).settings.public_url.rstrip('/')}/mcp"


@router.get("/connect")
def connect(request: Request) -> Response:
    return templates.TemplateResponse(
        request, "connect.html", {"mcp_url": _mcp_url(request), "tools": TOOLS, "example_prompt": EXAMPLE_PROMPT}
    )


@router.get("/mcp", include_in_schema=False)
def mcp_explainer(request: Request) -> Response:
    """Browsers get an explainer; MCP's POST /mcp falls through to the mounted MCP app."""
    if "text/event-stream" in request.headers.get("accept", ""):
        # Stateless mode never opens the standalone GET stream; 405 is the spec's answer for that.
        return Response(status_code=405, headers={"Allow": "POST"})
    return templates.TemplateResponse(request, "mcp.html", {"mcp_url": _mcp_url(request)})


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(FAVICON_SVG, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


@fallback_router.get("/{path:path}", include_in_schema=False)
def page_not_found(request: Request, path: str) -> Response:
    return _not_found(request, "page")
