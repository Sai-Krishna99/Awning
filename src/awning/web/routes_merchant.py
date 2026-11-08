import asyncio
import logging
import re
import secrets
from urllib.parse import quote, urlencode
from datetime import datetime, timezone

import anthropic
import httpx
from collections.abc import Callable

from fastapi import APIRouter, Request, UploadFile
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ValidationError

from awning.catalog import CatalogValidationError, PublishError
from awning.connect import PayPalConnections
from awning.context import AppContext
from awning.extraction import ExtractionFailed, NoMenuFound, to_menu_items
from awning.images import MAX_RAW_BYTES, ImageRejected, prepare_image
from awning.models import MenuItem, Shop, ShopDetails
from awning.paypal import PayPalError, login_web_base
from awning.seed import SAMPLE_MENUS_DIR
from awning.web.routes_shop import _ctx, _not_found, templates

logger = logging.getLogger(__name__)
MAX_UPLOAD_BODY_BYTES = MAX_RAW_BYTES + 1024 * 1024
TOO_LARGE_MESSAGE = "That photo is too large — please upload one under 15 MB and 25 megapixels."
TOO_SMALL_MESSAGE = "That photo is too small to read — try a closer, sharper photo."
NOT_AN_IMAGE_MESSAGE = "That file isn't a photo we can read — try a JPG or PNG."


class UploadSizeGuardRoute(APIRoute):
    """Rejects oversized bodies from the Content-Length header before the multipart form is parsed."""

    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            declared = request.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > MAX_UPLOAD_BODY_BYTES:
                return _start_page(request, 413, TOO_LARGE_MESSAGE)
            return await original(request)

        return handler


SAMPLE_MENUS = {
    "tacos": "rosas-taco-stand-ai-generated.webp",
    "pizza": "slice-corner-pizza-synthetic.png",
}
router = APIRouter()
upload_router = APIRouter(route_class=UploadSizeGuardRoute)

LOGIN_SCOPE = "openid email https://uri.paypal.com/services/paypalattributes"
STATE_EXPIRED_MESSAGE = (
    "That PayPal link expired or was already used. Open your review link again and press Connect with PayPal."
)
NOT_PAYABLE_MESSAGE = "This PayPal account can't receive payments yet. Use a Business account."
CANCELLED_MESSAGE = "PayPal connection was cancelled."
CONNECT_UNREACHABLE_MESSAGE = "We couldn't reach PayPal to connect that account. Please try again."

MERCHANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{5,20}$")
PUBLISH_ERRORS = {
    "needs_review": "Confirm the highlighted items first.",
    "no_items": "Add at least one item before publishing.",
    "missing_name": "Give your shop a name.",
}
SHOP_FIELD_LIMITS = {
    "name": ("Shop name", 60),
    "description": ("Description", 280),
    "pickup_location": ("Pickup location", 120),
    "pickup_hours": ("Pickup hours", 80),
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


@upload_router.post("/start")
async def start_upload(request: Request, photo: UploadFile) -> Response:
    return await _create_draft_from_photo(request, await photo.read(MAX_RAW_BYTES + 1))


@router.post("/start/sample")
async def start_sample(request: Request) -> Response:
    name = (await request.form()).get("sample")
    filename = SAMPLE_MENUS.get(name) if isinstance(name, str) else None
    if filename is None:
        return _start_page(request, 400, "Pick one of the sample menus.")
    return await _create_draft_from_photo(request, (SAMPLE_MENUS_DIR / filename).read_bytes())


async def _create_draft_from_photo(request: Request, raw: bytes) -> Response:
    ctx = _ctx(request)
    try:
        image = await asyncio.to_thread(prepare_image, raw)
    except ImageRejected as exc:
        if exc.reason == "too_large":
            return _start_page(request, 413, TOO_LARGE_MESSAGE)
        if exc.reason == "too_small":
            return _start_page(request, 400, TOO_SMALL_MESSAGE)
        return _start_page(request, 400, NOT_AN_IMAGE_MESSAGE)
    # Behind Render's proxy uvicorn runs with --forwarded-allow-ips='*' (render.yaml startCommand),
    # so request.client.host is the real client address, not the proxy.
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
            "connection": PayPalConnections(ctx.db).get(shop.id),
            "paypal_login_enabled": ctx.settings.paypal_login_enabled,
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


def _field_length_error(form: dict) -> str | None:
    for key, (label, limit) in SHOP_FIELD_LIMITS.items():
        if len(form[key]) > limit:
            return f"{label} must be {limit} characters or fewer."
    return None


def _item_errors_message(errors: list[dict]) -> str:
    details = "; ".join(
        err["message"] if err["field"] == "items" else f"row {err['row'] + 1}: {err['message']}" for err in errors
    )
    return f"Fix these items and save first — {details}."


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
    if length_error := _field_length_error(form):
        return fail(length_error)
    if not shop.items:
        return fail(PUBLISH_ERRORS["no_items"])
    if any(item.needs_review for item in shop.items):
        return fail(PUBLISH_ERRORS["needs_review"])
    connection = PayPalConnections(ctx.db).get(shop.id)
    merchant_id = connection["merchant_id"] if connection else (form["payee_merchant_id"] or None)
    email = None if merchant_id else (form["payee_email"] or None)
    if email and not form["payee_email_confirmed"]:
        return fail("PayPal can't verify emails in advance — please double-check it and tick the box.")
    if merchant_id and not connection:
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
    except CatalogValidationError as exc:
        return fail(_item_errors_message(exc.errors))
    return _no_store(RedirectResponse(f"/m/{token}", status_code=303))


def _draft_for_connect(request: Request, token: str) -> Shop | None:
    ctx = _ctx(request)
    shop = ctx.catalog.get_by_admin_token(token)
    if shop is None or shop.status == "published" or not ctx.settings.paypal_login_enabled:
        return None
    return shop


@router.get("/m/{token}/paypal/connect")
def paypal_connect(request: Request, token: str) -> Response:
    ctx = _ctx(request)
    shop = _draft_for_connect(request, token)
    if shop is None:
        return _not_found(request, "page")
    state = PayPalConnections(ctx.db).create_state(shop.id)
    query = urlencode(
        {
            "flowEntry": "static",
            "client_id": ctx.settings.paypal_client_id,
            "response_type": "code",
            "scope": LOGIN_SCOPE,
            "redirect_uri": f"{ctx.settings.public_url}/paypal/callback",
            "state": state,
        },
        quote_via=quote,
    )
    return _no_store(RedirectResponse(f"{login_web_base(ctx.settings.paypal_base_url)}/connect?{query}", status_code=303))


@router.get("/paypal/callback")
async def paypal_callback(request: Request) -> Response:
    ctx = _ctx(request)
    params = request.query_params
    shop_id = PayPalConnections(ctx.db).consume_state(params.get("state", ""))
    shop = ctx.catalog.get_by_id(shop_id) if shop_id else None
    if shop is None:
        return _no_store(_start_page(request, 400, STATE_EXPIRED_MESSAGE))
    if shop.status == "published":
        return _no_store(RedirectResponse(f"/m/{shop.admin_token}", status_code=303))
    code = params.get("code")
    if params.get("error") or not code:
        return _review_page(request, shop, error=CANCELLED_MESSAGE)
    try:
        identity = await ctx.paypal.fetch_login_identity(code)
        payable = await ctx.orders.validate_payee_merchant_id(identity.payer_id)
    except (PayPalError, httpx.HTTPError):
        logger.warning("paypal connect failed")
        return _review_page(request, shop, error=CONNECT_UNREACHABLE_MESSAGE, status=502)
    if not payable:
        return _review_page(request, shop, error=NOT_PAYABLE_MESSAGE, status=422)
    PayPalConnections(ctx.db).save(shop.id, identity.payer_id, identity.email)
    return _no_store(RedirectResponse(f"/m/{shop.admin_token}/review", status_code=303))


@router.post("/m/{token}/paypal/disconnect")
def paypal_disconnect(request: Request, token: str) -> Response:
    ctx = _ctx(request)
    shop = ctx.catalog.get_by_admin_token(token)
    if shop is None:
        return _not_found(request, "page")
    PayPalConnections(ctx.db).clear(shop.id)
    return _no_store(RedirectResponse(f"/m/{token}/review", status_code=303))


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


router.include_router(upload_router)
