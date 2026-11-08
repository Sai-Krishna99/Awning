import re

import pytest

from awning.catalog import CatalogValidationError, PublishError
from awning.context import AppContext
from awning.models import ItemOption, MenuItem, ShopDetails


def make_item(name: str = "Oat Latte", price: int | None = 450, **kwargs) -> MenuItem:
    return MenuItem(id=kwargs.pop("id", ""), name=name, price_cents=price, **kwargs)


def make_details(name: str = "Maria's Cart") -> ShopDetails:
    return ShopDetails(
        name=name,
        description="Coffee on the corner",
        pickup_location="5th and Main",
        pickup_hours="7-11am",
        payee_merchant_id=None,
        payee_email=None,
    )


def test_draft_then_publish(ctx: AppContext) -> None:
    draft = ctx.catalog.create_draft([make_item()], name_guess="Maria")
    assert draft.status == "draft"
    assert draft.items[0].id.startswith("itm_")
    shop = ctx.catalog.publish(draft.admin_token, make_details())
    assert shop.slug == "marias-cart"
    assert shop.status == "published"
    assert shop.published_at is not None
    assert ctx.catalog.get_by_id(shop.id) == shop
    assert ctx.catalog.get_by_admin_token(draft.admin_token) == shop


def test_publish_blocked_by_needs_review(ctx: AppContext) -> None:
    draft = ctx.catalog.create_draft([make_item(price=None, needs_review=True)])
    with pytest.raises(PublishError) as exc:
        ctx.catalog.publish(draft.admin_token, make_details())
    assert exc.value.code == "needs_review"


def test_publish_requires_items(ctx: AppContext) -> None:
    draft = ctx.catalog.create_draft([])
    with pytest.raises(PublishError) as exc:
        ctx.catalog.publish(draft.admin_token, make_details())
    assert exc.value.code == "no_items"


def test_publish_missing_name_and_already_published(ctx: AppContext) -> None:
    draft = ctx.catalog.create_draft([make_item()])
    with pytest.raises(PublishError) as exc:
        ctx.catalog.publish(draft.admin_token, make_details(name="  "))
    assert exc.value.code == "missing_name"
    ctx.catalog.publish(draft.admin_token, make_details())
    with pytest.raises(PublishError) as exc:
        ctx.catalog.publish(draft.admin_token, make_details())
    assert exc.value.code == "already_published"


def test_slug_collision_gets_suffix(ctx: AppContext) -> None:
    first = ctx.catalog.create_draft([make_item()])
    second = ctx.catalog.create_draft([make_item()])
    ctx.catalog.publish(first.admin_token, make_details())
    shop = ctx.catalog.publish(second.admin_token, make_details())
    assert re.match(r"^marias-cart-[a-z0-9]{4}$", shop.slug)


def test_update_items_assigns_ids_and_validates(ctx: AppContext) -> None:
    draft = ctx.catalog.create_draft([])
    item = make_item(options=[ItemOption(id="", name="Large", price_delta_cents=50)])
    shop = ctx.catalog.update_items(draft.admin_token, [item])
    assert shop.items[0].id.startswith("itm_")
    assert shop.items[0].options[0].id.startswith("opt_")
    with pytest.raises(CatalogValidationError) as exc:
        ctx.catalog.update_items(draft.admin_token, [make_item(), make_item(price=0)])
    assert exc.value.errors[0]["row"] == 1
    assert exc.value.errors[0]["field"] == "price_cents"
    ok = ctx.catalog.update_items(draft.admin_token, [make_item(price=0, needs_review=True)])
    assert ok.items[0].needs_review


def test_create_draft_keeps_existing_ids(ctx: AppContext) -> None:
    draft = ctx.catalog.create_draft([make_item(id="itm_keepme00")])
    assert draft.items[0].id == "itm_keepme00"


def test_search_matches_item_names(ctx: AppContext) -> None:
    draft = ctx.catalog.create_draft([make_item("Oat Latte")])
    ctx.catalog.create_draft([make_item("Oat Latte")])
    assert ctx.catalog.search("oat latte") == []
    ctx.catalog.publish(draft.admin_token, make_details())
    found = ctx.catalog.search("OAT latte")
    assert [s.id for s in found] == [draft.id]
    assert ctx.catalog.search("pizza") == []
    assert [s.id for s in ctx.catalog.search("")] == [draft.id]
    assert [s.id for s in ctx.catalog.list_published()] == [draft.id]


def test_get_published_hides_drafts(ctx: AppContext) -> None:
    draft = ctx.catalog.create_draft([make_item()])
    assert ctx.catalog.get_published("marias-cart") is None
    ctx.catalog.publish(draft.admin_token, make_details())
    assert ctx.catalog.get_published("marias-cart").id == draft.id
