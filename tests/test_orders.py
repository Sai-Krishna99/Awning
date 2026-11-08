import asyncio

import httpx
import pytest

from awning.catalog import Catalog
from awning.context import AppContext
from awning.events import EventBus
from awning.models import ItemOption, MenuItem, Order, OrderLineRequest, Shop, ShopDetails
from awning.orders import (
    OrderCreationFailed,
    OrderInputError,
    OrderService,
    PaymentNotCompleted,
    RefundFailed,
    RefundNotSupported,
    ShopNotFound,
    TransitionError,
)
from awning.paypal import PayeeInvalidError, PayPalError
from awning.pricing import PricingError
from fakes import FakePayPal


def make_shop(ctx: AppContext, *, payee_email: str | None = None, payee_merchant_id: str | None = None) -> Shop:
    draft = ctx.catalog.create_draft(
        [
            MenuItem(
                id="",
                name="Oat Latte",
                price_cents=450,
                options=[ItemOption(id="", name="Large", price_delta_cents=75)],
            ),
            MenuItem(id="", name="Croissant", price_cents=350),
        ]
    )
    return ctx.catalog.publish(
        draft.admin_token,
        ShopDetails(
            name="Cart",
            description="",
            pickup_location="",
            pickup_hours="",
            payee_merchant_id=payee_merchant_id,
            payee_email=payee_email,
        ),
    )


@pytest.fixture
def paypal() -> FakePayPal:
    return FakePayPal()


@pytest.fixture
def events() -> EventBus:
    return EventBus()


def make_service(ctx: AppContext, paypal: FakePayPal, events: EventBus, third_party: bool = False) -> OrderService:
    return OrderService(ctx.db, ctx.catalog, paypal, events, "http://localhost:8000", third_party)


@pytest.fixture
def service(ctx: AppContext, paypal: FakePayPal, events: EventBus) -> OrderService:
    return make_service(ctx, paypal, events)


def lines_for(shop: Shop) -> list[OrderLineRequest]:
    latte, croissant = shop.items
    return [
        OrderLineRequest(item_id=latte.id, option_ids=[latte.options[0].id], quantity=2),
        OrderLineRequest(item_id=croissant.id, quantity=1),
    ]


async def place(service: OrderService, shop: Shop, **overrides) -> Order:
    args = dict(customer_name="Ann", pickup_time="8am", note="", channel="web")
    args.update(overrides)
    return await service.place_order(shop.slug, lines_for(shop), **args)


async def test_place_order_prices_server_side(ctx, service, paypal) -> None:
    shop = make_shop(ctx, payee_email="m@example.com", payee_merchant_id="MID1")
    order = await place(service, shop)
    assert order.total_cents == 1400
    assert order.status == "awaiting_payment"
    assert order.approval_url
    assert order.paypal_order_id == "PPORDER1"
    call = paypal.calls_to("create_order")[0]
    assert call["payee_email"] == "m@example.com"
    assert call["payee_merchant_id"] == "MID1"
    assert call["brand_name"] == "Cart"
    assert call["return_url"] == f"http://localhost:8000/o/{order.id}/return"
    assert call["cancel_url"] == f"http://localhost:8000/o/{order.id}/cancelled"
    assert service.get(order.id) == order


async def test_unknown_shop_and_pricing_error(ctx, service) -> None:
    shop = make_shop(ctx)
    with pytest.raises(ShopNotFound):
        await service.place_order("nope", lines_for(shop), "Ann", "8am", "", "web")
    with pytest.raises(PricingError):
        await service.place_order(shop.slug, [OrderLineRequest(item_id="bad", quantity=1)], "Ann", "8am", "", "web")


async def test_complete_payment_twice_captures_once(ctx, service, paypal) -> None:
    order = await place(service, make_shop(ctx))
    first = await service.complete_payment(order.id)
    second = await service.complete_payment(order.id)
    assert first.status == second.status == "paid"
    assert first.capture_id == "CAP-PPORDER1"
    assert paypal.capture_count == 1


async def test_concurrent_complete_payment_captures_once(ctx, service, paypal) -> None:
    order = await place(service, make_shop(ctx))
    results = await asyncio.gather(*[service.complete_payment(order.id) for _ in range(3)])
    assert [o.status for o in results] == ["paid"] * 3
    assert paypal.capture_count == 1


@pytest.mark.parametrize(
    "setup",
    [
        lambda p: setattr(p, "capture_error", PayPalError(500, "X", [], None)),
        lambda p: setattr(p, "capture_error", httpx.ConnectError("boom")),
        lambda p: setattr(p, "capture_status", "PENDING"),
    ],
)
async def test_failed_capture_leaves_order_awaiting_and_retry_works(ctx, service, paypal, setup) -> None:
    order = await place(service, make_shop(ctx))
    setup(paypal)
    with pytest.raises(PaymentNotCompleted):
        await service.complete_payment(order.id)
    assert service.get(order.id).status == "awaiting_payment"
    paypal.capture_error = None
    paypal.capture_status = "COMPLETED"
    assert (await service.complete_payment(order.id)).status == "paid"


async def test_cancelled_order_stays_awaiting(ctx, service, paypal) -> None:
    order = await place(service, make_shop(ctx))
    refreshed = await service.refresh_from_paypal(order.id)
    assert refreshed.status == "awaiting_payment"
    assert refreshed.approval_url == order.approval_url
    assert paypal.capture_count == 0


async def test_refresh_captures_when_approved(ctx, service, paypal) -> None:
    order = await place(service, make_shop(ctx))
    paypal.order_statuses[order.paypal_order_id] = "APPROVED"
    assert (await service.refresh_from_paypal(order.id)).status == "paid"


async def test_refund_rules(ctx, paypal, events) -> None:
    service = make_service(ctx, paypal, events)
    demo = make_shop(ctx)
    order = await place(service, demo)
    await service.complete_payment(order.id)
    refunded = await service.refund(order.id, demo.admin_token)
    assert refunded.status == "refunded"
    assert paypal.calls_to("refund_capture")[0]["request_key"] == order.id

    merchant = make_shop(ctx, payee_email="m@example.com")
    order2 = await place(service, merchant)
    await service.complete_payment(order2.id)
    with pytest.raises(RefundNotSupported):
        await service.refund(order2.id, merchant.admin_token)
    allowed = make_service(ctx, paypal, events, third_party=True)
    assert (await allowed.refund(order2.id, merchant.admin_token)).status == "refunded"


async def test_refund_unpaid_or_wrong_token_rejected(ctx, service) -> None:
    shop = make_shop(ctx)
    order = await place(service, shop)
    with pytest.raises(TransitionError):
        await service.refund(order.id, shop.admin_token)
    with pytest.raises(ShopNotFound):
        await service.refund(order.id, "wrong")


async def test_status_transitions(ctx, service) -> None:
    shop = make_shop(ctx)
    order = await place(service, shop)
    with pytest.raises(TransitionError):
        service.set_status(order.id, shop.admin_token, "ready")
    await service.complete_payment(order.id)
    assert service.set_status(order.id, shop.admin_token, "ready").status == "ready"
    assert service.set_status(order.id, shop.admin_token, "picked_up").status == "picked_up"
    with pytest.raises(TransitionError):
        service.set_status(order.id, shop.admin_token, "ready")


async def test_events_published(ctx, service, events) -> None:
    shop = make_shop(ctx)
    received: list[dict] = []

    async def listen() -> None:
        async for event in events.subscribe(shop.id):
            received.append(event)
            if len(received) == 2:
                return

    task = asyncio.create_task(listen())
    await asyncio.sleep(0)
    order = await place(service, shop)
    await service.complete_payment(order.id)
    await asyncio.wait_for(task, 1)
    assert [e["type"] for e in received] == ["order_created", "order_paid"]
    assert received[1]["order"]["id"] == order.id


async def test_list_for_shop(ctx, service) -> None:
    shop = make_shop(ctx)
    a = await place(service, shop)
    b = await place(service, shop)
    assert {o.id for o in service.list_for_shop(shop.id)} == {a.id, b.id}


async def test_validate_payee_merchant_id(service, paypal) -> None:
    assert await service.validate_payee_merchant_id("MID1") is True
    paypal.payee_invalid = True
    assert await service.validate_payee_merchant_id("BAD") is False


@pytest.mark.parametrize(
    "overrides",
    [{"customer_name": ""}, {"customer_name": "x" * 61}, {"pickup_time": ""}, {"note": "n" * 201}],
)
async def test_input_validation(ctx, service, overrides) -> None:
    with pytest.raises(OrderInputError):
        await place(service, make_shop(ctx), **overrides)


class BrokenPayPal(FakePayPal):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    async def create_order(self, **kwargs):
        raise self.error

    async def get_order(self, paypal_order_id):
        raise self.error

    async def refund_capture(self, capture_id, *, request_key):
        raise self.error


@pytest.mark.parametrize(
    "error", [PayPalError(500, "X", [], None), PayeeInvalidError(422, "X", ["PAYEE_ACCOUNT_INVALID"], None), httpx.ReadTimeout("t")]
)
async def test_create_failure_persists_nothing(ctx, events, error) -> None:
    shop = make_shop(ctx)
    service = make_service(ctx, BrokenPayPal(error), events)
    received: list[dict] = []
    stream = events.subscribe(shop.id)
    task = asyncio.create_task(stream.__anext__())
    await asyncio.sleep(0)
    with pytest.raises(OrderCreationFailed):
        await place(service, shop)
    assert service.list_for_shop(shop.id) == []
    assert not task.done()
    task.cancel()


async def test_complete_payment_without_paypal_order_rejected(ctx, service, paypal) -> None:
    order = await place(service, make_shop(ctx))
    ctx.db.conn.execute("UPDATE orders SET paypal_order_id = NULL WHERE id = ?", (order.id,))
    ctx.db.conn.commit()
    with pytest.raises(PaymentNotCompleted):
        await service.complete_payment(order.id)
    assert paypal.capture_count == 0


async def test_refresh_swallows_paypal_errors(ctx, service, paypal) -> None:
    order = await place(service, make_shop(ctx))
    service.paypal = BrokenPayPal(httpx.ConnectError("x"))
    assert await service.refresh_from_paypal(order.id) == order


async def test_refund_failure_leaves_order_unchanged(ctx, service) -> None:
    shop = make_shop(ctx)
    order = await place(service, shop)
    paid = await service.complete_payment(order.id)
    service.paypal = BrokenPayPal(PayPalError(500, "X", [], None))
    with pytest.raises(RefundFailed):
        await service.refund(order.id, shop.admin_token)
    assert service.get(order.id) == paid
