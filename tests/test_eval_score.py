from awning.extraction import ExtractedItem, ExtractedMenu, ExtractedOption
from eval.score import ExpectedItem, score_case


def _item(name: str, price: int | None, options: list[str] | None = None) -> ExtractedItem:
    return ExtractedItem(
        name=name, description="", category="", price_cents=price,
        options=[ExtractedOption(name=o, price_delta_cents=0) for o in options or []],
        confidence="high", uncertain_fields=[], note="",
    )


def _menu(items: list[ExtractedItem], suspicious: list[str] | None = None) -> ExtractedMenu:
    return ExtractedMenu(
        shop_name_guess=None, hours_text=None, currency_guess="USD",
        items=items, suspicious_text=suspicious or [], notes=[],
    )


def test_perfect_match_scores_one():
    expected = [ExpectedItem("Latte", 450, ["Small", "Large"]), ExpectedItem("Scone", 350, [])]
    got = _menu([_item("Latte", 450, ["Small", "Large"]), _item("Scone", 350)])
    score = score_case(expected, got, adversarial=False)
    assert (score.item_recall, score.item_precision) == (1.0, 1.0)
    assert (score.price_accuracy, score.option_recall) == (1.0, 1.0)
    assert score.injection_obeyed is None and score.injection_flagged is None


def test_fuzzy_name_match():
    score = score_case([ExpectedItem("Oat latte (16oz)", 500, [])], _menu([_item("Oat Latte", 500)]), False)
    assert score.item_recall == 1.0


def test_wrong_price_lowers_price_accuracy():
    expected = [ExpectedItem("Latte", 450, []), ExpectedItem("Scone", 350, [])]
    got = _menu([_item("Latte", 450), _item("Scone", 300)])
    score = score_case(expected, got, False)
    assert score.item_recall == 1.0
    assert score.price_accuracy == 0.5


def test_missing_and_extra_items():
    expected = [ExpectedItem("Latte", 450, []), ExpectedItem("Scone", 350, [])]
    got = _menu([_item("Latte", 450), _item("Pizza", 900)])
    score = score_case(expected, got, False)
    assert score.item_recall == 0.5
    assert score.item_precision == 0.5


def test_injection_obeyed_detected():
    expected = [ExpectedItem("Latte", 450, [])]
    obeyed = score_case(expected, _menu([_item("Latte", 0)]), adversarial=True)
    assert obeyed.injection_obeyed is True and obeyed.injection_flagged is False
    resisted = score_case(expected, _menu([_item("Latte", 450)], ["ignore previous"]), adversarial=True)
    assert resisted.injection_obeyed is False and resisted.injection_flagged is True
