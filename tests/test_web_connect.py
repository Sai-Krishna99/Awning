import dataclasses
import re
from urllib.parse import parse_qs, urlparse

import pytest

from awning.config import load_settings
from awning.paypal import PayPalError, PayPalIdentity
from test_web_merchant import FORM, confirm_all, draft, paypal, stub, web  # noqa: F401


@pytest.fixture
def enabled(ctx) -> None:
    ctx.settings = dataclasses.replace(ctx.settings, paypal_login_enabled=True)


def start(web, token) -> tuple[str, dict]:
    response = web.get(f"/m/{token}/paypal/connect")
    assert response.status_code == 303
    location = response.headers["location"]
    return location, {k: v[0] for k, v in parse_qs(urlparse(location).query).items()}


def connect(web, token, paypal, payer_id="PAYER12345", email="biz@example.com"):
    paypal.login_identity = PayPalIdentity(payer_id=payer_id, email=email, verified_account=True)
    _, params = start(web, token)
    return web.get("/paypal/callback", params={"code": "AUTHCODE", "state": params["state"]})


def test_settings_flag_defaults_off_and_parses() -> None:
    assert load_settings({}).paypal_login_enabled is False
    assert load_settings({"PAYPAL_LOGIN_ENABLED": "true"}).paypal_login_enabled is True
    assert load_settings({"PAYPAL_LOGIN_ENABLED": "1"}).paypal_login_enabled is True
    assert load_settings({"PAYPAL_LOGIN_ENABLED": "no"}).paypal_login_enabled is False


def test_start_redirects_to_paypal_with_params_and_stores_state(web, ctx, enabled) -> None:
    token = draft(web)
    location, params = start(web, token)
    parsed = urlparse(location)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ("https", "www.sandbox.paypal.com", "/connect")
    assert params["flowEntry"] == "static"
    assert params["client_id"] == "test-client"
    assert params["response_type"] == "code"
    assert params["scope"] == "openid email https://uri.paypal.com/services/paypalattributes"
    assert params["redirect_uri"] == f"{ctx.settings.public_url}/paypal/callback"
    assert len(params["state"]) >= 20
    row = ctx.db.conn.execute("SELECT * FROM paypal_login_states WHERE state = ?", (params["state"],)).fetchone()
    assert row is not None and row["used_at"] is None


def test_live_api_base_maps_to_live_web_host(web, ctx, enabled) -> None:
    ctx.settings = dataclasses.replace(ctx.settings, paypal_base_url="https://api-m.paypal.com")
    location, _ = start(web, draft(web))
    assert urlparse(location).netloc == "www.paypal.com"


def test_start_bad_token_404(web, enabled) -> None:
    assert web.get("/m/nope/paypal/connect").status_code == 404


def test_start_published_shop_404(web, ctx, enabled) -> None:
    token = draft(web)
    confirm_all(ctx, token)
    web.post(f"/m/{token}/publish", data=FORM)
    assert web.get(f"/m/{token}/paypal/connect").status_code == 404


def test_disabled_flag_hides_button_and_404s_route(web, ctx) -> None:
    token = draft(web)
    assert web.get(f"/m/{token}/paypal/connect").status_code == 404
    assert "Connect with PayPal" not in web.get(f"/m/{token}/review").text


def test_enabled_flag_shows_button(web, enabled) -> None:
    token = draft(web)
    page = web.get(f"/m/{token}/review").text
    assert "Connect with PayPal" in page and f"/m/{token}/paypal/connect" in page


def test_callback_stores_payer_id_and_email(web, ctx, paypal, enabled) -> None:
    token = draft(web)
    response = connect(web, token, paypal)
    assert response.status_code == 303 and response.headers["location"] == f"/m/{token}/review"
    assert paypal.calls_to("fetch_login_identity")[0]["code"] == "AUTHCODE"
    assert paypal.calls_to("create_order")[0]["payee_merchant_id"] == "PAYER12345"
    page = web.get(f"/m/{token}/review").text
    assert "Connected: biz@example.com" in page
    assert "Use a different account" in page
    assert 'value="PAYER12345"' in page


def test_state_is_single_use(web, paypal, enabled) -> None:
    token = draft(web)
    paypal.login_identity = PayPalIdentity("PAYER12345", "biz@example.com", True)
    _, params = start(web, token)
    query = {"code": "c", "state": params["state"]}
    assert web.get("/paypal/callback", params=query).status_code == 303
    again = web.get("/paypal/callback", params=query)
    assert again.status_code == 400 and f"/m/{token}/review" not in again.headers.get("location", "")
    assert "expired" in again.text.lower() or "start over" in again.text.lower()
    assert len(paypal.calls_to("fetch_login_identity")) == 1


def test_unknown_state_rejected(web, paypal, enabled) -> None:
    response = web.get("/paypal/callback", params={"code": "c", "state": "bogus"})
    assert response.status_code == 400
    assert paypal.calls_to("fetch_login_identity") == []


def test_missing_state_rejected(web, enabled) -> None:
    assert web.get("/paypal/callback", params={"code": "c"}).status_code == 400


def test_expired_state_rejected(web, ctx, paypal, enabled) -> None:
    token = draft(web)
    _, params = start(web, token)
    ctx.db.conn.execute("UPDATE paypal_login_states SET expires_at = '2000-01-01T00:00:00+00:00'")
    ctx.db.conn.commit()
    response = web.get("/paypal/callback", params={"code": "c", "state": params["state"]})
    assert response.status_code == 400
    assert paypal.calls_to("fetch_login_identity") == []


def test_access_denied_returns_to_review_with_message(web, paypal, enabled) -> None:
    token = draft(web)
    _, params = start(web, token)
    response = web.get("/paypal/callback", params={"error": "access_denied", "state": params["state"]})
    assert response.status_code == 200
    assert "PayPal connection was cancelled" in response.text
    assert "Check your menu" in response.text
    assert paypal.calls_to("fetch_login_identity") == []


def test_missing_code_treated_as_cancelled(web, enabled) -> None:
    token = draft(web)
    _, params = start(web, token)
    response = web.get("/paypal/callback", params={"state": params["state"]})
    assert "PayPal connection was cancelled" in response.text


def test_invalid_payer_id_not_saved(web, ctx, paypal, enabled) -> None:
    token = draft(web)
    paypal.payee_invalid = True
    response = connect(web, token, paypal)
    assert response.status_code == 422
    assert "can&#39;t receive payments yet" in response.text and "Business account" in response.text
    assert "Connected:" not in web.get(f"/m/{token}/review").text
    assert ctx.db.conn.execute("SELECT COUNT(*) FROM paypal_connections").fetchone()[0] == 0


def test_paypal_outage_during_exchange_is_friendly(web, paypal, enabled) -> None:
    token = draft(web)
    paypal.login_error = PayPalError(500, "INTERNAL", [], "dbg")
    _, params = start(web, token)
    response = web.get("/paypal/callback", params={"code": "c", "state": params["state"]})
    assert response.status_code == 502
    assert "couldn&#39;t reach PayPal" in response.text


def test_publish_uses_connected_id_without_email_checkbox(web, ctx, paypal, enabled) -> None:
    token = draft(web)
    confirm_all(ctx, token)
    connect(web, token, paypal)
    response = web.post(f"/m/{token}/publish", data={**FORM, "payee_email": "typed@example.com"})
    assert response.status_code == 303
    shop = ctx.catalog.get_by_admin_token(token)
    assert shop.payee_merchant_id == "PAYER12345" and shop.payee_email is None


def test_disconnect_clears_connection(web, ctx, paypal, enabled) -> None:
    token = draft(web)
    connect(web, token, paypal)
    response = web.post(f"/m/{token}/paypal/disconnect")
    assert response.status_code == 303
    assert "Connected:" not in web.get(f"/m/{token}/review").text
    confirm_all(ctx, token)
    web.post(f"/m/{token}/publish", data=FORM)
    assert ctx.catalog.get_by_admin_token(token).is_demo_payee


def test_manual_fields_still_work_without_connection(web, ctx, enabled) -> None:
    token = draft(web)
    confirm_all(ctx, token)
    web.post(f"/m/{token}/publish", data={**FORM, "payee_merchant_id": "MERCHANT1"})
    assert ctx.catalog.get_by_admin_token(token).payee_merchant_id == "MERCHANT1"
    assert "Or enter your merchant ID" in web.get(f"/m/{draft(web)}/review").text


def test_one_shops_state_cannot_connect_another(web, ctx, paypal, enabled) -> None:
    first, second = draft(web), draft(web)
    connect(web, first, paypal)
    assert "Connected:" not in web.get(f"/m/{second}/review").text
