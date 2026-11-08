import io
from pathlib import Path
from urllib.parse import quote

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from awning.context import AppContext
from awning.models import Order, OrderLineRequest, Shop
from awning.orders import (
    OrderCreationFailed,
    OrderInputError,
    OrderNotFound,
    PaymentNotCompleted,
    ShopNotFound,
)
from awning.pricing import PricingError, format_cents

WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=WEB_DIR / "templates")
templates.env.filters["money"] = format_cents

router = APIRouter()

STATUS_LABELS = {
    "awaiting_payment": "Awaiting payment",
    "paid": "Paid",
    "ready": "Ready for pickup",
    "picked_up": "Picked up",
    "refunded": "Refunded",
}


class WebOrderRequest(BaseModel):
    lines: list[OrderLineRequest]
    customer_name: str
    pickup_time: str
    note: str = ""


def _ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def _error(status: int, code: str, message: str, **extra: object) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, **extra}}, status_code=status)


def _not_found(request: Request, what: str) -> Response:
    return templates.TemplateResponse(request, "not_found.html", {"what": what}, status_code=404)


def _checkout_config(ctx: AppContext, shop: Shop) -> dict:
    merchant_id = shop.payee_merchant_id
    if shop.is_demo_payee:
        merchant_id = ctx.settings.paypal_platform_merchant_id or None
    use_sdk = shop.payee_merchant_id is not None or shop.is_demo_payee
    sdk_url = None
    if use_sdk:
        sdk_url = (
            f"https://www.paypal.com/sdk/js?client-id={quote(ctx.settings.paypal_client_id)}"
            "&currency=USD&intent=capture"
        )
        if merchant_id:
            sdk_url += f"&merchant-id={quote(merchant_id)}"
    return {"mode": "sdk" if use_sdk else "redirect", "sdk_url": sdk_url}


@router.get("/s/{slug}/qr.svg")
def storefront_qr(request: Request, slug: str) -> Response:
    ctx = _ctx(request)
    if ctx.catalog.get_published(slug) is None:
        return _not_found(request, "shop")
    image = qrcode.make(
        f"{ctx.settings.public_url}/s/{slug}",
        image_factory=qrcode.image.svg.SvgPathImage,
        box_size=10,
        border=2,
    )
    buffer = io.BytesIO()
    image.save(buffer)
    return Response(buffer.getvalue(), media_type="image/svg+xml")


@router.get("/s/{slug}")
def storefront(request: Request, slug: str) -> Response:
    ctx = _ctx(request)
    shop = ctx.catalog.get_published(slug)
    if shop is None:
        return _not_found(request, "shop")
    categories: dict[str, list] = {}
    for item in shop.items:
        if item.available and item.price_cents is not None:
            categories.setdefault(item.category or "Menu", []).append(item)
    cart_items = {
        item.id: {
            "name": item.name,
            "price_cents": item.price_cents,
            "options": {o.id: {"name": o.name, "delta_cents": o.price_delta_cents} for o in item.options},
        }
        for items in categories.values()
        for item in items
    }
    return templates.TemplateResponse(
        request,
        "shop.html",
        {
            "shop": shop,
            "categories": categories,
            "cart_items": cart_items,
            "checkout": _checkout_config(ctx, shop),
            "public_url": ctx.settings.public_url,
        },
    )


@router.post("/api/s/{slug}/orders")
async def create_web_order(request: Request, slug: str, body: WebOrderRequest) -> Response:
    ctx = _ctx(request)
    try:
        order = await ctx.orders.place_order(
            slug, body.lines, body.customer_name, body.pickup_time, body.note, "web"
        )
    except ShopNotFound:
        return _error(404, "shop_not_found", f"No published shop with slug '{slug}'.")
    except PricingError as exc:
        extra = {"valid_choices": exc.valid_choices} if exc.valid_choices else {}
        return _error(422, exc.code, exc.message, **extra)
    except OrderInputError as exc:
        return _error(422, "invalid_input", exc.message, field=exc.field)
    except OrderCreationFailed as exc:
        return _error(502, "payment_setup_failed", exc.reason)
    return JSONResponse(
        {
            "order_id": order.id,
            "paypal_order_id": order.paypal_order_id,
            "approval_url": order.approval_url,
        }
    )


@router.post("/api/orders/{order_id}/capture")
async def capture_order(request: Request, order_id: str) -> Response:
    ctx = _ctx(request)
    try:
        try:
            order = await ctx.orders.complete_payment(order_id)
        except PaymentNotCompleted:
            order = ctx.orders.get(order_id)
    except OrderNotFound:
        return _error(404, "order_not_found", f"No order with id '{order_id}'.")
    return JSONResponse({"order_id": order.id, "status": order.status})


@router.get("/api/orders/{order_id}")
async def order_status(request: Request, order_id: str) -> Response:
    ctx = _ctx(request)
    try:
        try:
            order = await ctx.orders.refresh_from_paypal(order_id)
        except PaymentNotCompleted:
            order = ctx.orders.get(order_id)
    except OrderNotFound:
        return _error(404, "order_not_found", f"No order with id '{order_id}'.")
    shop = ctx.catalog.get_by_id(order.shop_id)
    return JSONResponse(
        {
            "order_id": order.id,
            "status": order.status,
            "total": format_cents(order.total_cents),
            "pickup_time": order.pickup_time,
            "shop_name": shop.name if shop else "",
        }
    )


@router.get("/o/{order_id}/return")
async def payment_return(request: Request, order_id: str) -> Response:
    ctx = _ctx(request)
    target = f"/o/{order_id}"
    try:
        await ctx.orders.complete_payment(order_id)
    except OrderNotFound:
        return _not_found(request, "order")
    except PaymentNotCompleted:
        target += "?returned=1"
    return RedirectResponse(target, status_code=303)


def _order_and_shop(request: Request, order_id: str) -> tuple[Order, Shop] | None:
    ctx = _ctx(request)
    order = ctx.orders.get(order_id)
    shop = ctx.catalog.get_by_id(order.shop_id) if order else None
    return (order, shop) if order and shop else None


@router.get("/o/{order_id}/cancelled")
def payment_cancelled(request: Request, order_id: str) -> Response:
    found = _order_and_shop(request, order_id)
    if found is None:
        return _not_found(request, "order")
    order, shop = found
    return templates.TemplateResponse(request, "order_cancelled.html", {"order": order, "shop": shop})


@router.get("/o/{order_id}")
def order_page(request: Request, order_id: str, returned: int = 0) -> Response:
    found = _order_and_shop(request, order_id)
    if found is None:
        return _not_found(request, "order")
    order, shop = found
    return templates.TemplateResponse(
        request,
        "order.html",
        {
            "order": order,
            "shop": shop,
            "returned": bool(returned),
            "status_label": STATUS_LABELS[order.status],
        },
    )
