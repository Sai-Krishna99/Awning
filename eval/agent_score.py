import re
from collections import Counter
from dataclasses import dataclass, field

_LINE_CENT_KEYS = ("unit_price_cents", "line_total_cents")
AMOUNT_RE = re.compile(r"\$\s?(\d+(?:\.\d{1,2})?)")


@dataclass(frozen=True)
class ExpectedLine:
    name: str
    quantity: int
    options: tuple[str, ...]


@dataclass(frozen=True)
class ConversationScore:
    order_created: bool
    lines_match: bool
    total_matches_server: bool
    url_in_final_reply: bool
    unexpected_amounts: list[str]
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            self.order_created and self.lines_match and self.total_matches_server
            and self.url_in_final_reply and not self.unexpected_amounts
        )


def _units(lines: list[tuple[str, int, tuple[str, ...]]]) -> Counter:
    units: Counter = Counter()
    for name, quantity, options in lines:
        units[(name.strip().lower(), tuple(sorted(o.strip().lower() for o in options)))] += quantity
    return units


def lines_match(expected: list[ExpectedLine], got: list[dict]) -> bool:
    want = _units([(e.name, e.quantity, e.options) for e in expected])
    have = _units([(g["name"], g["quantity"], tuple(g["option_names"])) for g in got])
    return want == have


def expected_total_cents(menu: list[dict], lines: list[dict]) -> int:
    by_name = {item["name"].lower(): item for item in menu}
    total = 0
    for got in lines:
        item = by_name[got["name"].lower()]
        deltas = {o["name"].lower(): o["price_delta_cents"] for o in item["options"]}
        unit = item["price_cents"] + sum(deltas[o.lower()] for o in got["option_names"])
        total += unit * got["quantity"]
    return total


def allowed_amounts(menu: list[dict], total_cents: int | None, extra_cents: set[int] = frozenset()) -> set[int]:
    cents = {opt["price_delta_cents"] for item in menu for opt in item["options"]}
    for item in menu:
        sums = {0}
        for opt in item["options"]:
            sums |= {s + opt["price_delta_cents"] for s in sums}
        cents |= {item["price_cents"] + s for s in sums}
    cents |= set(extra_cents)
    if total_cents is not None:
        cents.add(total_cents)
    return cents


def _to_cents(amount: str) -> int:
    dollars, _, fraction = amount.partition(".")
    return int(dollars) * 100 + int((fraction + "00")[:2])


def unexpected_amounts(text: str, allowed_cents: set[int]) -> list[str]:
    return [m.group(0) for m in AMOUNT_RE.finditer(text) if _to_cents(m.group(1)) not in allowed_cents]


def score_conversation(
    expected: list[ExpectedLine],
    menu: list[dict],
    orders: list[dict],
    agent_texts: list[str],
    user_texts: list[str],
) -> ConversationScore:
    notes: list[str] = []
    final_reply = agent_texts[-1] if agent_texts else ""
    user_cents = {_to_cents(m.group(1)) for text in user_texts for m in AMOUNT_RE.finditer(text)}
    if len(orders) != 1:
        notes.append("no order created" if not orders else f"{len(orders)} orders created, expected 1")
        # Amounts are still checked against menu prices when no single order exists.
        stray = [a for text in agent_texts for a in unexpected_amounts(text, allowed_amounts(menu, None, user_cents))]
        return ConversationScore(len(orders) > 0, False, False, False, stray, notes)
    placed = orders[0]
    matches = lines_match(expected, placed["lines"])
    server_total = expected_total_cents(menu, placed["lines"])
    total_ok = placed["total_cents"] == server_total
    url_ok = bool(placed["approval_url"]) and placed["approval_url"] in final_reply
    line_cents = {placed_line[key] for placed_line in placed["lines"] for key in _LINE_CENT_KEYS if key in placed_line}
    allowed = allowed_amounts(menu, placed["total_cents"], user_cents | line_cents)
    stray = [a for text in agent_texts for a in unexpected_amounts(text, allowed)]
    if not matches:
        notes.append("order lines differ from expected")
    if not total_ok:
        notes.append(f"order total {placed['total_cents']} != server-computed {server_total}")
    if not url_ok:
        notes.append("approval URL missing from final reply")
    if stray:
        notes.append("unexpected amounts in replies: " + ", ".join(stray))
    return ConversationScore(True, matches, total_ok, url_ok, stray, notes)
