from awning.models import MenuItem, OrderLineRequest, PricedLine, PricedOrder, Shop

MAX_LINES = 20
MAX_QUANTITY = 20


class PricingError(Exception):
    def __init__(self, code: str, message: str, valid_choices: list[dict] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.valid_choices = valid_choices


def format_cents(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def _price_line(items_by_id: dict[str, MenuItem], request: OrderLineRequest) -> PricedLine:
    if not 1 <= request.quantity <= MAX_QUANTITY:
        raise PricingError("bad_quantity", f"quantity must be between 1 and {MAX_QUANTITY}")
    item = items_by_id.get(request.item_id)
    if item is None:
        choices = [{"id": i.id, "name": i.name} for i in items_by_id.values() if i.available]
        raise PricingError(
            "unknown_item",
            f"No item with id '{request.item_id}'. Use an item id, not a name.",
            choices,
        )
    if not item.available or item.price_cents is None:
        raise PricingError("item_unavailable", f"'{item.name}' is not available")
    if len(set(request.option_ids)) != len(request.option_ids):
        raise PricingError("duplicate_option", f"Duplicate option for '{item.name}'")
    options_by_id = {o.id: o for o in item.options}
    unit_price = item.price_cents
    option_names = []
    for option_id in request.option_ids:
        option = options_by_id.get(option_id)
        if option is None:
            raise PricingError(
                "unknown_option",
                f"'{option_id}' is not an option of '{item.name}'",
                [{"id": o.id, "name": o.name} for o in item.options],
            )
        unit_price += option.price_delta_cents
        option_names.append(option.name)
    return PricedLine(
        item_id=item.id,
        name=item.name,
        option_names=option_names,
        unit_price_cents=unit_price,
        quantity=request.quantity,
        line_total_cents=unit_price * request.quantity,
    )


def price_order(shop: Shop, lines: list[OrderLineRequest]) -> PricedOrder:
    if not lines:
        raise PricingError("empty_order", "Order has no lines")
    if len(lines) > MAX_LINES:
        raise PricingError("too_many_lines", f"Order has more than {MAX_LINES} lines")
    items_by_id = {item.id: item for item in shop.items}
    priced = [_price_line(items_by_id, line) for line in lines]
    return PricedOrder(lines=priced, total_cents=sum(p.line_total_cents for p in priced))
