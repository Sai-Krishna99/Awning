import json
import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from awning.models import Order
from awning.orders import PaymentNotCompleted
from awning.paypal import PayPalError
from awning.web.routes_shop import _ctx

logger = logging.getLogger(__name__)
router = APIRouter()


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _find_order(orders, event_type: str, resource: dict) -> Order | None:
    custom_id = _text(resource.get("custom_id"))
    paypal_order_id = capture_id = None
    if event_type == "CHECKOUT.ORDER.APPROVED":
        units = resource.get("purchase_units")
        unit = units[0] if isinstance(units, list) and units and isinstance(units[0], dict) else {}
        custom_id = custom_id or _text(unit.get("custom_id"))
        paypal_order_id = _text(resource.get("id"))
    elif event_type == "PAYMENT.CAPTURE.COMPLETED":
        related = (resource.get("supplementary_data") or {}).get("related_ids") or {}
        paypal_order_id = _text(related.get("order_id"))
        capture_id = _text(resource.get("id"))
    elif event_type == "PAYMENT.CAPTURE.REFUNDED":
        for link in resource.get("links") or []:
            if isinstance(link, dict) and link.get("rel") == "up" and isinstance(link.get("href"), str):
                capture_id = _text(link["href"].rstrip("/").rsplit("/", 1)[-1])
    order = orders.get(custom_id) if custom_id else None
    if order is None and paypal_order_id:
        order = orders.get_by_paypal_order_id(paypal_order_id)
    if order is None and capture_id:
        order = orders.get_by_capture_id(capture_id)
    return order


@router.post("/webhooks/paypal")
async def paypal_webhook(request: Request) -> Response:
    ctx = _ctx(request)
    webhook_id = ctx.settings.paypal_webhook_id
    if not webhook_id:
        return JSONResponse({"error": "webhook not configured"}, status_code=400)
    body = await request.body()
    try:
        valid = await ctx.paypal.verify_webhook(request.headers, body, webhook_id)
    except (PayPalError, httpx.HTTPError):
        valid = False
    if not valid:
        return JSONResponse({"error": "invalid signature"}, status_code=400)
    try:
        event = json.loads(body)
        event_type = event["event_type"]
        resource = event.get("resource") or {}
    except (ValueError, KeyError, TypeError, AttributeError):
        return JSONResponse({"error": "malformed event"}, status_code=400)
    if not isinstance(resource, dict) or event_type not in (
        "CHECKOUT.ORDER.APPROVED", "PAYMENT.CAPTURE.COMPLETED", "PAYMENT.CAPTURE.REFUNDED"
    ):
        return JSONResponse({"ok": True})
    order = _find_order(ctx.orders, event_type, resource)
    if order is None:
        logger.info("webhook %s: no matching order", event_type)
        return JSONResponse({"ok": True})
    try:
        if event_type == "PAYMENT.CAPTURE.REFUNDED":
            await ctx.orders.mark_refunded(order.id)
        else:
            await ctx.orders.complete_payment(order.id)
    except PaymentNotCompleted as exc:
        logger.warning("webhook %s: order %s not completed: %s", event_type, order.id, exc.reason)
        return JSONResponse({"error": "payment not completed yet"}, status_code=500)
    return JSONResponse({"ok": True})
