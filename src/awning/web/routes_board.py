import asyncio
from collections.abc import AsyncIterable, Callable
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel

from awning.context import AppContext
from awning.models import Order, Shop
from awning.seed import DEMO_SLUG
from awning.orders import OrderNotFound, RefundFailed, RefundNotSupported, ShopNotFound, TransitionError
from awning.web.routes_shop import _ctx, _not_found, templates

router = APIRouter()

PAYPAL_ACTIVITY_URL = "https://www.sandbox.paypal.com/myaccount/activities/"
PRIVATE_FIELDS = {"approval_url", "capture_id", "paypal_order_id"}
AWAITING_VISIBLE_FOR = timedelta(minutes=60)


class StatusRequest(BaseModel):
    status: Literal["ready", "picked_up"]


def _board_order(order: Order | dict) -> dict:
    data = order if isinstance(order, dict) else order.model_dump(mode="json")
    return {key: value for key, value in data.items() if key not in PRIVATE_FIELDS}


PUBLIC_FIELDS = ("id", "status", "channel", "total_cents", "customer_name", "pickup_time", "created_at")
PUBLIC_LINE_FIELDS = ("name", "option_names", "quantity")


def _mask_name(name: str) -> str:
    name = name.strip()
    return f"{name[0]}." if name else ""


def _public_order(order: Order | dict) -> dict:
    data = order if isinstance(order, dict) else order.model_dump(mode="json")
    public = {key: data[key] for key in PUBLIC_FIELDS}
    public["customer_name"] = _mask_name(data["customer_name"])
    public["lines"] = [{key: line[key] for key in PUBLIC_LINE_FIELDS} for line in data["lines"]]
    return public


def _private(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _json(body: object, status: int = 200) -> Response:
    return _private(JSONResponse(body, status_code=status))


def _unknown_shop() -> Response:
    return _json({"error": {"code": "not_found", "message": "Unknown shop."}}, 404)


def _shop(request: Request, token: str) -> Shop | None:
    return _ctx(request).catalog.get_by_admin_token(token)


def _shop_or_404(request: Request, token: str) -> Shop:
    shop = _shop(request, token)
    if shop is None:
        raise HTTPException(status_code=404, detail="Unknown shop.")
    return shop


async def shop_event_stream(
    ctx: AppContext, shop_id: str, serialize: Callable[[Order | dict], dict] = _board_order
) -> AsyncIterable[ServerSentEvent]:
    events = ctx.events.subscribe(shop_id)
    # The bus registers the subscriber on the first __anext__, so start pulling before yielding anything.
    pending = asyncio.ensure_future(events.__anext__())
    try:
        await asyncio.sleep(0)
        yield ServerSentEvent(comment="connected", retry=3000)
        while True:
            event = await pending
            pending = asyncio.ensure_future(events.__anext__())
            yield ServerSentEvent(event=event["type"], data=serialize(event["order"]))
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await events.aclose()


def public_event_stream(ctx: AppContext, shop_id: str) -> AsyncIterable[ServerSentEvent]:
    return shop_event_stream(ctx, shop_id, _public_order)


def _demo_shop_or_404(request: Request, slug: str) -> Shop:
    shop = _ctx(request).catalog.get_published(slug) if slug == DEMO_SLUG else None
    if shop is None:
        raise HTTPException(status_code=404, detail="Unknown shop.")
    return shop


@router.get("/s/{slug}/live")
def live_board_page(request: Request, shop: Annotated[Shop, Depends(_demo_shop_or_404)]) -> Response:
    context = {"shop": shop, "token": "", "read_only": True, "api_base": f"/api/s/{shop.slug}/live"}
    return _private(templates.TemplateResponse(request, "board.html", context))


@router.get("/api/s/{slug}/live/orders")
def live_orders(request: Request, shop: Annotated[Shop, Depends(_demo_shop_or_404)]) -> Response:
    cutoff = datetime.now(timezone.utc) - AWAITING_VISIBLE_FOR
    orders = _ctx(request).orders.list_for_shop(shop.id, awaiting_since=cutoff)
    return _json({"orders": [_public_order(order) for order in orders]})


@router.get("/api/s/{slug}/live/events", response_class=EventSourceResponse)
async def live_events(
    request: Request, response: Response, shop: Annotated[Shop, Depends(_demo_shop_or_404)]
) -> AsyncIterable[ServerSentEvent]:
    response.headers["Referrer-Policy"] = "no-referrer"
    async for event in public_event_stream(_ctx(request), shop.id):
        yield event


@router.get("/m/{token}/board")
def board_page(request: Request, token: str) -> Response:
    shop = _shop(request, token)
    if shop is None or shop.status != "published":
        return _private(_not_found(request, "page"))
    return _private(templates.TemplateResponse(request, "board.html", {"shop": shop, "token": token, "read_only": False, "api_base": f"/api/m/{quote(token)}"}))


@router.get("/api/m/{token}/orders")
def list_orders(request: Request, token: str) -> Response:
    shop = _shop(request, token)
    if shop is None:
        return _unknown_shop()
    cutoff = datetime.now(timezone.utc) - AWAITING_VISIBLE_FOR
    orders = _ctx(request).orders.list_for_shop(shop.id, awaiting_since=cutoff)
    return _json({"orders": [_board_order(order) for order in orders]})


@router.get("/api/m/{token}/events", response_class=EventSourceResponse)
async def order_events(
    request: Request, response: Response, shop: Annotated[Shop, Depends(_shop_or_404)]
) -> AsyncIterable[ServerSentEvent]:
    response.headers["Referrer-Policy"] = "no-referrer"
    async for event in shop_event_stream(_ctx(request), shop.id):
        yield event


@router.post("/api/m/{token}/orders/{order_id}/status")
async def set_order_status(request: Request, token: str, order_id: str, payload: StatusRequest) -> Response:
    try:
        order = _ctx(request).orders.set_status(order_id, token, payload.status)
    except (ShopNotFound, OrderNotFound):
        return _unknown_shop()
    except TransitionError as exc:
        return _json({"message": str(exc)}, 409)
    return _json(_board_order(order))


@router.post("/api/m/{token}/orders/{order_id}/refund")
async def refund_order(request: Request, token: str, order_id: str) -> Response:
    try:
        order = await _ctx(request).orders.refund(order_id, token)
    except (ShopNotFound, OrderNotFound):
        return _unknown_shop()
    except RefundNotSupported:
        return _json({"message": "Refund this order from your PayPal account.", "paypal_url": PAYPAL_ACTIVITY_URL}, 409)
    except RefundFailed as exc:
        return _json({"message": exc.reason}, 502)
    except TransitionError as exc:
        return _json({"message": str(exc)}, 409)
    return _json(_board_order(order))
