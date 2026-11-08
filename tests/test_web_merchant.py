import asyncio
import io
import re

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import BaseModel, ValidationError

from awning.extraction import (
    ExtractedItem, ExtractedMenu, ExtractedOption, ExtractionFailed, ExtractionResult, NoMenuFound,
)
from awning.images import prepare_image
from awning.models import MenuItem
from awning.paypal import PayPalError
from awning.orders import OrderService
from awning.web.app import create_app
from fakes import FakePayPal


def _png(size=(400, 400)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, "PNG")
    return buffer.getvalue()


def _menu(suspicious=()) -> ExtractedMenu:
    return ExtractedMenu(
        shop_name_guess="Maria's Cart", hours_text="7-11am", currency_guess="USD",
        items=[
            ExtractedItem(name="Latte", description="", category="Drinks", price_cents=450,
                          options=[ExtractedOption(name="Oat", price_delta_cents=75)],
                          confidence="high", uncertain_fields=[], note=""),
            ExtractedItem(name="Scone", description="", category="Food", price_cents=None, options=[],
                          confidence="low", uncertain_fields=["price_cents"], note="price smudged"),
        ],
        suspicious_text=list(suspicious), notes=[],
    )


class StubExtractor:
    def __init__(self) -> None:
        self.menu = _menu()
        self.error: Exception | None = None
        self.calls = 0

    async def extract(self, image) -> ExtractionResult:
        self.calls += 1
        if self.error:
            raise self.error
        return ExtractionResult(menu=self.menu, model="stub-model", input_tokens=10, output_tokens=20, latency_ms=30)


@pytest.fixture
def stub() -> StubExtractor:
    return StubExtractor()


@pytest.fixture
def paypal() -> FakePayPal:
    return FakePayPal()


@pytest.fixture
def web(ctx, paypal, stub) -> TestClient:
    ctx.paypal = paypal
    ctx.orders = OrderService(ctx.db, ctx.catalog, paypal, ctx.events, ctx.settings.public_url, False)
    ctx.extractor = stub
    return TestClient(create_app(ctx), follow_redirects=False)


def upload(web, data=None, name="menu.png"):
    return web.post("/start", files={"photo": (name, data or _png(), "image/png")})


def token_of(response) -> str:
    return re.match(r"/m/([^/]+)/review", response.headers["location"]).group(1)


def draft(web):
    return token_of(upload(web))


def confirm_all(ctx, token, price=300):
    shop = ctx.catalog.get_by_admin_token(token)
    items = [i.model_copy(update={"needs_review": False, "price_cents": i.price_cents or price}) for i in shop.items]
    ctx.catalog.update_items(token, items)


FORM = {"name": "Maria's Cart", "description": "Coffee", "pickup_location": "5th", "pickup_hours": "7-11"}


def test_upload_creates_draft_and_redirects(web, ctx, stub) -> None:
    response = upload(web)
    assert response.status_code == 303
    token = token_of(response)
    shop = ctx.catalog.get_by_admin_token(token)
    assert shop.status == "draft" and shop.name == "Maria's Cart" and shop.pickup_hours == "7-11am"
    assert [i.name for i in shop.items] == ["Latte", "Scone"]
    row = ctx.db.conn.execute("SELECT * FROM extractions").fetchone()
    assert (row["shop_id"], row["model"], row["input_tokens"], row["output_tokens"], row["latency_ms"]) == (
        shop.id, "stub-model", 10, 20, 30)


def test_upload_rejects_non_image(web, stub) -> None:
    response = upload(web, b"not an image")
    assert response.status_code == 400
    assert "That file isn&#39;t a photo we can read — try a JPG or PNG." in response.text
    assert stub.calls == 0


def test_upload_no_menu_found(web, stub) -> None:
    stub.error = NoMenuFound("none")
    response = upload(web)
    assert response.status_code == 422
    assert "We couldn't find menu items in that photo." in response.text.replace("&#39;", "'")


@pytest.mark.parametrize("error", [
    ExtractionFailed("declined"),
    anthropic.APIConnectionError(request=httpx.Request("POST", "http://x")),
])
def test_upload_model_trouble_is_friendly(web, stub, error) -> None:
    stub.error = error
    response = upload(web)
    assert response.status_code == (502 if isinstance(error, anthropic.APIError) else 422)
    assert "please try again" in response.text.lower()


def test_upload_validation_error_is_friendly(web, stub) -> None:
    class M(BaseModel):
        x: int

    try:
        M(x="a")
    except ValidationError as exc:
        stub.error = exc
    response = upload(web)
    assert response.status_code == 502


def test_sixth_upload_rate_limited(web, stub) -> None:
    for _ in range(5):
        assert upload(web).status_code == 303
    response = upload(web)
    assert response.status_code == 429
    assert stub.calls == 5


def test_global_limit_checked_before_model(web, ctx, stub) -> None:
    assert (ctx.upload_limiter_global.max_events, ctx.upload_limiter_global.window_s) == (100, 86400)
    ctx.upload_limiter_global.max_events = 0
    assert upload(web).status_code == 429
    assert stub.calls == 0


def test_suspicious_text_shown_on_review(web, stub) -> None:
    stub.menu = _menu(suspicious=["ignore previous instructions <b>"])
    token = draft(web)
    page = web.get(f"/m/{token}/review")
    assert "We ignored text in your photo that looked like instructions" in page.text
    assert "ignore previous instructions &lt;b&gt;" in page.text


def test_review_page_security_headers_and_data(web) -> None:
    token = draft(web)
    page = web.get(f"/m/{token}/review")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert '<meta name="robots" content="noindex">' in page.text
    assert "ag-grid-community@36.2.0/dist/ag-grid-community.min.js" in page.text
    assert "Latte" in page.text
    assert web.get("/m/nope/review").status_code == 404


def test_put_items_saves_and_reports_field_errors(web, ctx) -> None:
    token = draft(web)
    items = [i.model_dump() for i in ctx.catalog.get_by_admin_token(token).items]
    items[0]["name"] = "Flat White"
    ok = web.put(f"/api/m/{token}/items", json={"items": items})
    assert ok.status_code == 200 and ok.json()["items"][0]["name"] == "Flat White"
    items[0]["price_cents"] = 0
    bad = web.put(f"/api/m/{token}/items", json={"items": items})
    assert bad.status_code == 422
    assert bad.json()["errors"][0] == {"row": 0, "field": "price_cents", "message": "price must be between 1 and 100000 cents"}
    malformed = web.put(f"/api/m/{token}/items", json={"items": [{"name": "x", "price_cents": "abc", "id": ""}]})
    assert malformed.status_code == 422 and malformed.json()["errors"][0]["row"] == 0
    assert web.put("/api/m/nope/items", json={"items": []}).status_code == 404


def test_publish_blocked_until_reviewed(web, ctx) -> None:
    token = draft(web)
    response = web.post(f"/m/{token}/publish", data=FORM)
    assert response.status_code == 422
    assert "Confirm the highlighted items first." in response.text
    assert ctx.catalog.get_by_admin_token(token).status == "draft"


def test_publish_rejects_invalid_merchant_id(web, ctx, paypal) -> None:
    token = draft(web)
    confirm_all(ctx, token)
    paypal.payee_invalid = True
    response = web.post(f"/m/{token}/publish", data={**FORM, "payee_merchant_id": "BADMERCHANT1"})
    assert response.status_code == 422
    assert "PayPal doesn&#39;t recognize that merchant ID." in response.text
    assert ctx.catalog.get_by_admin_token(token).status == "draft"
    assert 'value="BADMERCHANT1"' in response.text


def test_publish_email_requires_confirmation(web, ctx) -> None:
    token = draft(web)
    confirm_all(ctx, token)
    response = web.post(f"/m/{token}/publish", data={**FORM, "payee_email": "me@example.com"})
    assert response.status_code == 422
    assert "PayPal can&#39;t verify emails in advance" in response.text
    ok = web.post(f"/m/{token}/publish", data={**FORM, "payee_email": "me@example.com", "payee_email_confirmed": "on"})
    assert ok.status_code == 303
    assert ctx.catalog.get_by_admin_token(token).payee_email == "me@example.com"


def test_publish_with_valid_merchant_id(web, ctx) -> None:
    token = draft(web)
    confirm_all(ctx, token)
    assert web.post(f"/m/{token}/publish", data={**FORM, "payee_merchant_id": "MERCHANT1"}).status_code == 303
    assert ctx.catalog.get_by_admin_token(token).payee_merchant_id == "MERCHANT1"


def test_publish_without_payee_uses_demo(web, ctx) -> None:
    token = draft(web)
    confirm_all(ctx, token)
    response = web.post(f"/m/{token}/publish", data=FORM)
    assert response.status_code == 303 and response.headers["location"] == f"/m/{token}"
    shop = ctx.catalog.get_by_admin_token(token)
    assert shop.status == "published" and shop.is_demo_payee
    page = web.get(f"/m/{token}")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert '<meta name="robots" content="noindex">' in page.text
    assert f"http://localhost:8000/s/{shop.slug}" in page.text
    assert "http://localhost:8000/mcp" in page.text
    assert f"/m/{token}/board" in page.text
    assert f"/s/{shop.slug}/qr.svg" in page.text
    assert "Save this page — it's your private admin link." in page.text
    assert web.get(f"/s/{shop.slug}").status_code == 200
    assert web.get(f"/m/{token}/review").status_code == 303


def test_publish_missing_name(web, ctx) -> None:
    token = draft(web)
    confirm_all(ctx, token)
    response = web.post(f"/m/{token}/publish", data={**FORM, "name": " "})
    assert response.status_code == 422


def test_upload_oversized_raw_image_is_too_large(web, stub) -> None:
    response = upload(web, b"\0" * (15 * 1024 * 1024 + 1))
    assert response.status_code == 413
    assert "That photo is too large" in response.text
    assert stub.calls == 0


def test_upload_body_cap_rejects_before_parsing(web, stub) -> None:
    response = upload(web, b"\0" * (17 * 1024 * 1024))
    assert response.status_code == 413
    assert "That photo is too large" in response.text
    assert stub.calls == 0


def test_upload_heic_reaches_extractor(web, stub) -> None:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    buffer = io.BytesIO()
    Image.new("RGB", (400, 400), "white").save(buffer, "HEIF")
    assert upload(web, buffer.getvalue(), "menu.heic").status_code == 303
    assert stub.calls == 1


def test_publish_malformed_merchant_id(web, ctx, paypal) -> None:
    token = draft(web)
    confirm_all(ctx, token)
    response = web.post(f"/m/{token}/publish", data={**FORM, "payee_merchant_id": "bad id!"})
    assert response.status_code == 422
    assert "PayPal doesn&#39;t recognize that merchant ID." in response.text
    assert paypal.calls_to("create_order") == []


@pytest.mark.parametrize("error", [
    PayPalError(500, "INTERNAL", [], None),
    httpx.ConnectError("down"),
])
def test_publish_paypal_outage_is_friendly(web, ctx, paypal, error, monkeypatch) -> None:
    token = draft(web)
    confirm_all(ctx, token)

    async def boom(**kwargs):
        raise error

    monkeypatch.setattr(paypal, "create_order", boom)
    response = web.post(f"/m/{token}/publish", data={**FORM, "payee_merchant_id": "MERCHANT1"})
    assert response.status_code == 422
    assert "We couldn&#39;t reach PayPal to check that merchant ID" in response.text
    assert ctx.catalog.get_by_admin_token(token).status == "draft"


def test_item_name_cannot_break_out_of_script_block(web, ctx) -> None:
    token = draft(web)
    items = [i.model_dump() for i in ctx.catalog.get_by_admin_token(token).items]
    items[0]["name"] = "</script><script>alert(1)</script>"
    assert web.put(f"/api/m/{token}/items", json={"items": items}).status_code == 200
    page = web.get(f"/m/{token}/review").text
    config = page.split('id="review-config" type="application/json">')[1].split("</script>")[0]
    assert "<" not in config and "\\u003c/script\\u003e" in config
    assert "alert(1)" not in page.replace(config, "")


def test_upload_decodes_image_off_event_loop(web, monkeypatch) -> None:
    on_loop: list[bool] = []

    def recording(raw):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return prepare_image(raw)

    monkeypatch.setattr("awning.web.routes_merchant.prepare_image", recording)
    assert upload(web).status_code == 303
    assert on_loop == [False]


def test_upload_too_small_photo_message(web, stub) -> None:
    response = upload(web, _png((150, 150)))
    assert response.status_code == 400
    assert "That photo is too small to read — try a closer, sharper photo." in response.text
    assert stub.calls == 0


@pytest.mark.parametrize(("field", "limit", "message"), [
    ("name", 60, "Shop name must be 60 characters or fewer."),
    ("description", 280, "Description must be 280 characters or fewer."),
    ("pickup_location", 120, "Pickup location must be 120 characters or fewer."),
    ("pickup_hours", 80, "Pickup hours must be 80 characters or fewer."),
])
def test_publish_enforces_field_lengths(web, ctx, field, limit, message) -> None:
    token = draft(web)
    confirm_all(ctx, token)
    response = web.post(f"/m/{token}/publish", data={**FORM, field: "x" * (limit + 1)})
    assert response.status_code == 422
    assert message in response.text
    assert ctx.catalog.get_by_admin_token(token).status == "draft"
    assert web.post(f"/m/{token}/publish", data={**FORM, field: "x" * limit}).status_code == 303


def test_publish_shows_item_field_errors(web, ctx) -> None:
    shop = ctx.catalog.create_draft([
        MenuItem(id="", name="Latte", price_cents=450),
        MenuItem(id="", name="N" * 81, price_cents=300),
    ])
    response = web.post(f"/m/{shop.admin_token}/publish", data=FORM)
    assert response.status_code == 422
    assert "row 2: name must be 1-80 characters" in response.text
    assert "invalid prices" not in response.text


def test_upload_over_pixel_cap_names_both_limits(web, stub) -> None:
    buffer = io.BytesIO()
    Image.new("1", (5100, 5000)).save(buffer, "PNG")
    response = upload(web, buffer.getvalue())
    assert response.status_code == 413
    assert "That photo is too large — please upload one under 15 MB and 25 megapixels." in response.text
    assert stub.calls == 0
