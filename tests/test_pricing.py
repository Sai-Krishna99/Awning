from datetime import datetime

import pytest

from awning.models import ItemOption, MenuItem, OrderLineRequest, Shop
from awning.pricing import PricingError, format_cents, price_order


@pytest.fixture
def shop() -> Shop:
    return Shop(
        id="shp_1",
        slug="cafe",
        admin_token="tok",
        status="published",
        name="Cafe",
        description="",
        pickup_location="",
        pickup_hours="",
        payee_merchant_id=None,
        payee_email=None,
        created_at=datetime(2026, 1, 1),
        published_at=None,
        items=[
            MenuItem(
                id="itm_latte",
                name="Oat Latte",
                price_cents=450,
                options=[
                    ItemOption(id="opt_large", name="Large", price_delta_cents=75),
                    ItemOption(id="opt_shot", name="Extra shot", price_delta_cents=100),
                ],
            ),
            MenuItem(id="itm_croissant", name="Croissant", price_cents=350),
            MenuItem(id="itm_cold", name="Cold Brew", price_cents=500, available=False),
        ],
    )


def line(item_id: str, quantity: int = 1, option_ids: list[str] | None = None) -> OrderLineRequest:
    return OrderLineRequest(item_id=item_id, quantity=quantity, option_ids=option_ids or [])


def error_for(shop: Shop, lines: list[OrderLineRequest]) -> PricingError:
    with pytest.raises(PricingError) as exc_info:
        price_order(shop, lines)
    return exc_info.value


def test_format_cents() -> None:
    assert format_cents(1050) == "10.50"
    assert format_cents(5) == "0.05"


def test_prices_options_and_quantities(shop: Shop) -> None:
    order = price_order(shop, [line("itm_latte", 2, ["opt_large"]), line("itm_croissant")])
    assert [(l.unit_price_cents, l.quantity, l.line_total_cents) for l in order.lines] == [
        (525, 2, 1050),
        (350, 1, 350),
    ]
    assert order.total_cents == 1400


def test_unknown_item_lists_choices(shop: Shop) -> None:
    err = error_for(shop, [line("Oat Latte")])
    assert err.code == "unknown_item"
    assert {"id": "itm_latte", "name": "Oat Latte"} in err.valid_choices
    assert {"id": "itm_cold", "name": "Cold Brew"} not in err.valid_choices


def test_option_from_other_item_rejected(shop: Shop) -> None:
    err = error_for(shop, [line("itm_croissant", 1, ["opt_large"])])
    assert err.code == "unknown_option"
    assert err.valid_choices == []


def test_unknown_option_lists_item_options(shop: Shop) -> None:
    err = error_for(shop, [line("itm_latte", 1, ["Large"])])
    assert err.code == "unknown_option"
    assert {"id": "opt_large", "name": "Large"} in err.valid_choices


def test_duplicate_option_rejected(shop: Shop) -> None:
    assert error_for(shop, [line("itm_latte", 1, ["opt_large", "opt_large"])]).code == "duplicate_option"


def test_unavailable_item_rejected(shop: Shop) -> None:
    assert error_for(shop, [line("itm_cold")]).code == "item_unavailable"


@pytest.mark.parametrize("quantity", [0, 21])
def test_quantity_bounds(shop: Shop, quantity: int) -> None:
    assert error_for(shop, [line("itm_croissant", quantity)]).code == "bad_quantity"


def test_empty_and_too_many_lines(shop: Shop) -> None:
    assert error_for(shop, []).code == "empty_order"
    assert error_for(shop, [line("itm_croissant")] * 21).code == "too_many_lines"
