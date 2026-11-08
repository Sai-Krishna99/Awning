import logging
import re
import secrets
from datetime import datetime, timezone

import anthropic
import httpx
from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ValidationError

from awning.catalog import CatalogValidationError, PublishError
from awning.context import AppContext
from awning.extraction import ExtractionFailed, NoMenuFound, to_menu_items
from awning.images import MAX_RAW_BYTES, ImageRejected, prepare_image
from awning.models import MenuItem, Shop, ShopDetails
from awning.paypal import PayPalError
from awning.web.routes_shop import _ctx, _not_found, templates

logger = logging.getLogger(__name__)
router = APIRouter()

MERCHANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{5,20}$")
PUBLISH_ERRORS = {
    "needs_review": "Confirm the highlighted items first.",
    "no_items": "Add at least one item before publishing.",
    "missing_name": "Give your shop a name.",
}


class ItemsPayload(BaseModel):
    items: list[MenuItem]


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    return response


def _start_page(request: Request, status: int = 200, error: str | None = None) -> Response:
    return templates.TemplateResponse(request, "start.html", {"error": error}, status_code=status)


def _record_extraction(ctx: AppContext, shop_id: str, result) -> None:
    ctx.db.conn.execute(
        "INSERT INTO extractions (id, shop_id, model, input_tokens, output_tokens, latency_ms, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "ext_" + secrets.token_urlsafe(8), shop_id, result.model, result.input_tokens,
            result.output_tokens, result.latency_ms, datetime.now(timezone.utc).isoformat(),
        ),
    )
    ctx.db.conn.commit()


@router.get("/start")
def start_page(request: Request) -> Response:
    return _start_page(request)


@router.post("/start")
async def start_upload(request: Request, photo: UploadFile) -> Response:
    ctx = _ctx(request)
    raw = await photo.read(MAX_RAW_BYTES + 1)
    try:
        image = prepare_image(raw)
    except ImageRejected:
        return _start_page(request, 400, "That file isn't a photo we can read — try a JPG or PNG.")
    client_ip = request.client.host if request.client else "unknown"
    if not (ctx.upload_limiter_ip.allow(client_ip) and ctx.upload_limiter_global.allow("global")):
        return _start_page(request, 429, "You've reached the upload limit for now — please try again later.")
    try:
        result = await ctx.extractor.extract(image)
    except NoMenuFound:
        return _start_page(request, 422, "We couldn't find menu items in that photo.")
    except ExtractionFailed:
        return _start_page(request, 422, "We couldn't read that menu right now — please try again.")
    except (anthropic.APIError, ValidationError):
        logger.exception("menu extraction failed")
        return _start_page(request, 502, "We couldn't read that menu right now — please try again.")
    menu = result.menu
    shop = ctx.catalog.create_draft(
        to_menu_items(menu), menu.shop_name_guess, menu.hours_text, suspicious_text=menu.suspicious_text
    )
    _record_extraction(ctx, shop.id, result)
    return _no_store(RedirectResponse(f"/m/{shop.admin_token}/review", status_code=303))


def _review_page(
    request: Request, shop: Shop, form: dict | None = None, error: str | None = None, status: int = 200
) -> Response:
    ctx = _ctx(request)
    form = form or {
        "name": shop.name, "description": shop.description,
        "pickup_location": shop.pickup_location, "pickup_hours": shop.pickup_hours,
        "payee_merchant_id": "", "payee_email": "", "payee_email_confirmed": False,
    }
    response = templates.TemplateResponse(
        request,
        "review.html",
        {
            "shop": shop,
            "items": [item.model_dump() for item in shop.items],
            "suspicious_text": ctx.catalog.get_suspicious_text(shop.id),
            "form": form,
            "error": error,
        },
        status_code=status,
    )
    return _no_store(response)


@router.get("/m/{token}/review")
def review_page(request: Request, token: str) -> Response:
    shop = _ctx(request).catalog.get_by_admin_token(token)
    if shop is None:
        return _not_found(request, "page")
    if shop.status == "published":
        return _no_store(RedirectResponse(f"/m/{token}", status_code=303))
    return _review_page(request, shop)


def _validation_errors(exc: ValidationError) -> list[dict]:
    errors = []
    for err in exc.errors():
        loc = err["loc"]
        row = loc[1] if len(loc) > 1 and isinstance(loc[1], int) else 0
        field = ".".join(str(p) for p in loc[2:]) if len(loc) > 2 else "items"
        errors.append({"row": row, "field": field, "message": err["msg"]})
    return errors


@router.put("/api/m/{token}/items")
async def save_items(request: Request, token: str) -> Response:
    ctx = _ctx(request)
    shop = ctx.catalog.get_by_admin_token(token)
    if shop is None:
        return _no_store(JSONResponse({"error": {"code": "not_found", "message": "Unknown shop."}}, status_code=404))
    if shop.status == "published":
        return _no_store(JSONResponse(
            {"error": {"code": "already_published", "message": "This shop is already published."}}, status_code=409
        ))
    try:
        payload = ItemsPayload.model_validate(await request.json())
        saved = ctx.catalog.update_items(token, payload.items)
    except ValidationError as exc:
        return _no_store(JSONResponse({"errors": _validation_errors(exc)}, status_code=422))
    except ValueError:
        return _no_store(JSONResponse(
            {"errors": [{"row": 0, "field": "items", "message": "Request body must be JSON."}]}, status_code=422
        ))
    except CatalogValidationError as exc:
        return _no_store(JSONResponse({"errors": exc.errors}, status_code=422))
    return _no_store(JSONResponse({"items": [item.model_dump() for item in saved.items]}))


@router.post("/m/{token}/publish")
async def publish(request: Request, token: str) -> Response:
    ctx = _ctx(request)
    shop = ctx.catalog.get_by_admin_token(token)
    if shop is None:
        return _not_found(request, "page")
    if shop.status == "published":
        return _no_store(RedirectResponse(f"/m/{token}", status_code=303))
    data = await request.form()
    text = lambda key: str(data.get(key) or "").strip()  # noqa: E731
    form = {
        "name": text("name"), "description": text("description"),
        "pickup_location": text("pickup_location"), "pickup_hours": text("pickup_hours"),
        "payee_merchant_id": text("payee_merchant_id"), "payee_email": text("payee_email"),
        "payee_email_confirmed": bool(data.get("payee_email_confirmed")),
    }

    def fail(message: str) -> Response:
        return _review_page(request, shop, form, message, 422)

    if not form["name"]:
        return fail(PUBLISH_ERRORS["missing_name"])
    if not shop.items:
        return fail(PUBLISH_ERRORS["no_items"])
    if any(item.needs_review for item in shop.items):
        return fail(PUBLISH_ERRORS["needs_review"])
    merchant_id = form["payee_merchant_id"] or None
    email = None if merchant_id else (form["payee_email"] or None)
    if email and not form["payee_email_confirmed"]:
        return fail("PayPal can't verify emails in advance — please double-check it and tick the box.")
    if merchant_id:
        try:
            recognized = bool(MERCHANT_ID_PATTERN.match(merchant_id)) and await ctx.orders.validate_payee_merchant_id(
                merchant_id
            )
        except (PayPalError, httpx.HTTPError):
            logger.exception("merchant id check failed")
            return fail("We couldn't reach PayPal to check that merchant ID — please try again.")
        if not recognized:
            return fail("PayPal doesn't recognize that merchant ID.")
    try:
        ctx.catalog.publish(
            token,
            ShopDetails(
                name=form["name"], description=form["description"],
                pickup_location=form["pickup_location"], pickup_hours=form["pickup_hours"],
                payee_merchant_id=merchant_id, payee_email=email,
                payee_email_confirmed=form["payee_email_confirmed"],
            ),
        )
    except PublishError as exc:
        return fail(PUBLISH_ERRORS.get(exc.code, "This shop can't be published yet."))
    except CatalogValidationError:
        return fail("Some items have invalid prices — fix them and save first.")
    return _no_store(RedirectResponse(f"/m/{token}", status_code=303))


@router.get("/m/{token}")
def published_page(request: Request, token: str) -> Response:
    ctx = _ctx(request)
    shop = ctx.catalog.get_by_admin_token(token)
    if shop is None:
        return _not_found(request, "page")
    if shop.status != "published":
        return _no_store(RedirectResponse(f"/m/{token}/review", status_code=303))
    public_url = ctx.settings.public_url
    response = templates.TemplateResponse(
        request,
        "published.html",
        {
            "shop": shop,
            "storefront_url": f"{public_url}/s/{shop.slug}",
            "mcp_url": f"{public_url}/mcp",
            "board_path": f"/m/{token}/board",
        },
    )
    return _no_store(response)
