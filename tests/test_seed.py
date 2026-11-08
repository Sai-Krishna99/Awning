from awning.config import load_settings
from awning.context import AppContext
from awning.seed import DEFAULT_SEED_PATH, seed_demo_shop


def test_seed_is_idempotent(ctx: AppContext) -> None:
    first = seed_demo_shop(ctx.catalog, DEFAULT_SEED_PATH)
    second = seed_demo_shop(ctx.catalog, DEFAULT_SEED_PATH)
    assert first is not None and first.slug == "marias-cart"
    assert second is None
    assert [s.slug for s in ctx.catalog.list_published()] == ["marias-cart"]
    assert len(first.items) >= 8 and any(i.options for i in first.items)


def test_seed_payee_prefers_merchant_id_then_email(ctx: AppContext) -> None:
    shop = seed_demo_shop(ctx.catalog, DEFAULT_SEED_PATH, payee_merchant_id="MID1", payee_email="a@b.co")
    assert (shop.payee_merchant_id, shop.payee_email) == ("MID1", None)


def test_seed_payee_email_fallback(ctx: AppContext) -> None:
    shop = seed_demo_shop(ctx.catalog, DEFAULT_SEED_PATH, payee_email="a@b.co")
    assert (shop.payee_merchant_id, shop.payee_email) == (None, "a@b.co")


def test_seed_runs_on_startup(client) -> None:
    with client:
        assert client.app.state.ctx.catalog.get_published("marias-cart") is not None


def test_demo_payee_settings_blank_is_none() -> None:
    s = load_settings({"PAYPAL_DEMO_PAYEE_EMAIL": "", "PAYPAL_DEMO_PAYEE_MERCHANT_ID": "M"})
    assert s.demo_payee_email is None and s.demo_payee_merchant_id == "M"
