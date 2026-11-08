from eval.agent_score import (
    ExpectedLine,
    allowed_amounts,
    expected_total_cents,
    lines_match,
    score_conversation,
    unexpected_amounts,
)

MENU = [
    {"name": "Latte", "price_cents": 450, "options": [{"name": "Oat milk", "price_delta_cents": 75}]},
    {"name": "Cappuccino", "price_cents": 425, "options": [{"name": "Oat milk", "price_delta_cents": 75}]},
]
URL = "https://www.sandbox.paypal.com/checkoutnow?token=PPORDER1"


def order(lines: list[dict], total_cents: int) -> dict:
    return {"lines": lines, "total_cents": total_cents, "approval_url": URL}


def line(name: str, quantity: int = 1, options: list[str] | None = None) -> dict:
    return {"name": name, "quantity": quantity, "option_names": options or []}


def test_expected_total_cents_adds_option_deltas() -> None:
    assert expected_total_cents(MENU, [line("Cappuccino", 2), line("Cappuccino", 1, ["Oat milk"])]) == 3 * 425 + 75


def test_lines_match_ignores_order_case_and_line_splitting() -> None:
    expected = [ExpectedLine("Cappuccino", 1, ()), ExpectedLine("Cappuccino", 1, ("Oat milk",))]
    assert lines_match(expected, [line("cappuccino", 1, ["oat milk"]), line("Cappuccino")])
    assert not lines_match(expected, [line("Cappuccino", 2)])
    assert not lines_match(expected, [line("Latte"), line("Cappuccino", 1, ["Oat milk"])])


def test_unexpected_amounts_flags_only_unknown_dollar_values() -> None:
    allowed = allowed_amounts(MENU, total_cents=450)
    assert unexpected_amounts("A latte is $4.50, oat milk adds $0.75.", allowed) == []
    assert unexpected_amounts("Your total is $4.50.", allowed) == []
    assert unexpected_amounts("I gave you a discount: $1.00 total", allowed) == ["$1.00"]
    assert unexpected_amounts("That is $6 off", allowed) == ["$6"]


def test_allowed_amounts_includes_extra_cents() -> None:
    assert unexpected_amounts("You said $1", allowed_amounts(MENU, 450, extra_cents={100})) == []


def good_inputs() -> dict:
    return dict(
        expected=[ExpectedLine("Latte", 1, ())],
        menu=MENU,
        orders=[order([line("Latte")], 450)],
        agent_texts=["Done. Total $4.50. Approve here: " + URL],
        user_texts=["Order a latte"],
    )


def test_score_passes_clean_conversation() -> None:
    score = score_conversation(**good_inputs())
    assert score.passed and score.notes == []


def test_score_fails_without_order() -> None:
    score = score_conversation(**{**good_inputs(), "orders": []})
    assert not score.passed and not score.order_created


def test_score_fails_on_duplicate_orders() -> None:
    inputs = good_inputs()
    score = score_conversation(**{**inputs, "orders": inputs["orders"] * 2})
    assert not score.passed and any("2 orders" in n for n in score.notes)


def test_score_fails_on_wrong_lines_total_url_and_amount() -> None:
    inputs = good_inputs()
    wrong_lines = score_conversation(**{**inputs, "orders": [order([line("Cappuccino")], 425)]})
    assert not wrong_lines.lines_match and not wrong_lines.passed
    wrong_total = score_conversation(**{**inputs, "orders": [order([line("Latte")], 100)]})
    assert not wrong_total.total_matches_server
    no_url = score_conversation(**{**inputs, "agent_texts": ["Done, total $4.50"]})
    assert not no_url.url_in_final_reply
    bad_amount = score_conversation(**{**inputs, "agent_texts": ["Special price $1.00! " + URL]})
    assert bad_amount.unexpected_amounts == ["$1.00"] and not bad_amount.passed


def test_url_only_counts_in_final_reply() -> None:
    inputs = good_inputs()
    score = score_conversation(**{**inputs, "agent_texts": ["Here: " + URL + " $4.50", "Anything else?"]})
    assert not score.url_in_final_reply


def test_server_computed_line_prices_are_allowed() -> None:
    placed = order(
        [{**line("Cappuccino", 1, ["Oat milk"]), "unit_price_cents": 500, "line_total_cents": 500}], 500
    )
    inputs = {**good_inputs(), "expected": [ExpectedLine("Cappuccino", 1, ("Oat milk",))], "orders": [placed],
              "agent_texts": ["Cappuccino with oat milk $5.00, total $5.00 " + URL]}
    assert score_conversation(**inputs).passed


def test_menu_price_plus_any_option_combination_is_allowed() -> None:
    menu = [{"name": "Latte", "price_cents": 450, "options": [
        {"name": "Large", "price_delta_cents": 100}, {"name": "Oat milk", "price_delta_cents": 75}]}]
    allowed = allowed_amounts(menu, None)
    assert unexpected_amounts("$5.50 large, $5.25 oat, $6.25 both", allowed) == []
    assert unexpected_amounts("$5.00", allowed) == ["$5.00"]
