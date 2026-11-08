from types import SimpleNamespace

import pytest

from awning.extraction import (
    ExtractedItem,
    ExtractedMenu,
    ExtractedOption,
    ExtractionFailed,
    MenuExtractor,
    NoMenuFound,
    to_menu_items,
)
from awning.images import PreparedImage


def _item(**overrides) -> ExtractedItem:
    fields = dict(
        name="Latte", description="", category="Drinks", price_cents=450, options=[],
        confidence="high", uncertain_fields=[], note="",
    )
    fields.update(overrides)
    return ExtractedItem(**fields)


def _menu(items: list[ExtractedItem], currency: str | None = "USD") -> ExtractedMenu:
    return ExtractedMenu(
        shop_name_guess=None, hours_text=None, currency_guess=currency, items=items,
        suspicious_text=[], notes=[],
    )


class StubMessages:
    def __init__(self, response: SimpleNamespace) -> None:
        self.response = response
        self.calls: list[dict] = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _stub_client(menu, stop_reason="end_turn", input_tokens=1200, output_tokens=800):
    response = SimpleNamespace(
        parsed_output=menu,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )
    messages = StubMessages(response)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


IMAGE = PreparedImage(data=b"jpeg", media_type="image/jpeg", width=800, height=600)


def test_to_menu_items_flags_low_confidence_and_missing_price() -> None:
    menu = _menu([
        _item(),
        _item(name="Mocha", confidence="low", uncertain_fields=["name"], note=""),
        _item(name="Tea", price_cents=None),
        _item(name="Chai", note="blurry"),
    ])
    items = to_menu_items(menu)
    assert [i.needs_review for i in items] == [False, True, True, False]
    assert items[1].review_note == "Check: name"
    assert items[3].review_note == "blurry"
    assert all(i.id == "" for i in items)


def test_to_menu_items_flags_non_usd_currency() -> None:
    assert to_menu_items(_menu([_item()], currency="GBP"))[0].needs_review is True
    assert to_menu_items(_menu([_item()], currency=None))[0].needs_review is False


def test_to_menu_items_sanitizes_negative_prices_and_deltas() -> None:
    menu = _menu([
        _item(name="Neg", price_cents=-100),
        _item(name="Huge", price_cents=100001),
        _item(
            name="Bun",
            options=[
                ExtractedOption(name="Large", price_delta_cents=100),
                ExtractedOption(name="Bao bun", price_delta_cents=-100),
            ],
        ),
    ])
    neg, huge, bun = to_menu_items(menu)
    assert neg.price_cents is None and neg.needs_review
    assert huge.price_cents is None and huge.needs_review
    assert [o.name for o in bun.options] == ["Large"]
    assert bun.needs_review and "Bao bun" in bun.review_note


async def test_extract_raises_no_menu_found() -> None:
    client, _ = _stub_client(_menu([]))
    with pytest.raises(NoMenuFound):
        await MenuExtractor(client, "claude-opus-5-5", "low").extract(IMAGE)


async def test_extract_reports_usage() -> None:
    client, messages = _stub_client(_menu([_item()]))
    result = await MenuExtractor(client, "claude-opus-5-5", "low").extract(IMAGE)
    assert (result.input_tokens, result.output_tokens) == (1200, 800)
    assert result.model == "claude-opus-5-5"
    assert result.latency_ms >= 0
    assert len(result.menu.items) == 1
    call = messages.calls[0]
    assert call["output_format"] is ExtractedMenu
    assert call["output_config"] == {"effort": "low"}
    assert call["fallbacks"] == [{"model": "claude-opus-4-8"}]
    assert "thinking" not in call


async def test_extract_omits_fallbacks_for_other_models() -> None:
    client, messages = _stub_client(_menu([_item()]))
    await MenuExtractor(client, "claude-haiku-5-5", "low").extract(IMAGE)
    assert "fallbacks" not in messages.calls[0]


async def test_extract_refusal_is_extraction_failure() -> None:
    client, _ = _stub_client(None, stop_reason="refusal")
    with pytest.raises(ExtractionFailed):
        await MenuExtractor(client, "claude-opus-5-5", "low").extract(IMAGE)


async def test_extract_unparseable_is_extraction_failure() -> None:
    client, _ = _stub_client(None)
    with pytest.raises(ExtractionFailed):
        await MenuExtractor(client, "claude-opus-5-5", "low").extract(IMAGE)


def test_to_menu_items_drops_absurd_option_deltas() -> None:
    menu = _menu([_item(options=[
        ExtractedOption(name="Large", price_delta_cents=50000),
        ExtractedOption(name="Gold", price_delta_cents=10**9),
    ])])
    item = to_menu_items(menu)[0]
    assert [o.name for o in item.options] == ["Large"]
    assert item.needs_review and "Gold" in item.review_note
