import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from typing import Literal

import httpx

from awning.catalog import Catalog
from awning.db import Database
from awning.events import EventBus
from awning.models import Channel, Order, OrderLineRequest, PricedLine, PricedOrder, Shop, new_id
from awning.paypal import PayeeInvalidError, PayPalClient, PayPalError

_PAYPAL_ERRORS = (PayPalError, httpx.HTTPError)
from awning.pricing import format_cents, price_order

_POST_PAYMENT = ("paid", "ready", "picked_up")
_CAPTURABLE_PAYPAL_STATUSES = ("APPROVED", "COMPLETED")
PAYMENT_HELD_MESSAGE = "PayPal is holding this payment for the merchant to accept."
_TRANSITIONS = {"ready": "paid", "picked_up": "ready"}


class OrderInputError(Exception):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


class ShopNotFound(Exception):
    pass


class OrderNotFound(Exception):
    pass


class TransitionError(Exception):
    pass


class RefundNotSupported(Exception):
    pass


class OrderCreationFailed(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RefundFailed(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PaymentNotCompleted(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _validate_text(field: str, value: str, min_len: int, max_len: int) -> None:
    if not min_len <= len(value.strip() if min_len else value) <= max_len:
        bound = f"{min_len}-{max_len}" if min_len else f"at most {max_len}"
        raise OrderInputError(field, f"must be {bound} characters")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrderService:
    def __init__(
        self,
        db: Database,
        catalog: Catalog,
        paypal: PayPalClient,
        events: EventBus,
        public_url: str,
        third_party_refunds: bool,
    ) -> None:
        self.db = db
        self.catalog = catalog
        self.paypal = paypal
        self.events = events
        self.public_url = public_url.rstrip("/")
        self.third_party_refunds = third_party_refunds
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, order_id: str) -> asyncio.Lock:
        return self._locks.setdefault(order_id, asyncio.Lock())

    async def place_order(
        self,
        shop_slug: str,
        lines: list[OrderLineRequest],
        customer_name: str,
        pickup_time: str,
        note: str,
        channel: Channel,
    ) -> Order:
        _validate_text("customer_name", customer_name, 1, 60)
        _validate_text("pickup_time", pickup_time, 1, 40)
        _validate_text("note", note, 0, 200)
        shop = self.catalog.get_published(shop_slug)
        if shop is None:
            raise ShopNotFound(shop_slug)
        priced = price_order(shop, lines)
        order_id = new_id("order")
        try:
            paypal_order = await self.paypal.create_order(
                reference_id=order_id,
                priced=priced,
                payee_merchant_id=shop.payee_merchant_id,
                payee_email=shop.payee_email,
                brand_name=shop.name,
                description=f"{shop.name} pickup order",
                return_url=f"{self.public_url}/o/{order_id}/return",
                cancel_url=f"{self.public_url}/o/{order_id}/cancelled",
            )
        except PayeeInvalidError as exc:
            raise OrderCreationFailed("the merchant's PayPal setup needs attention") from exc
        except _PAYPAL_ERRORS as exc:
            raise OrderCreationFailed(f"could not start payment: {type(exc).__name__}") from exc
        now = _now()
        self.db.conn.execute(
            "INSERT INTO orders (id, shop_id, status, channel, lines_json, total_cents, customer_name,"
            " pickup_time, note, paypal_order_id, approval_url, created_at, updated_at)"
            " VALUES (?, ?, 'awaiting_payment', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                order_id,
                shop.id,
                channel,
                json.dumps([line.model_dump() for line in priced.lines]),
                priced.total_cents,
                customer_name.strip(),
                pickup_time.strip(),
                note,
                paypal_order.id,
                paypal_order.approval_url,
                now,
                now,
            ),
        )
        self.db.conn.commit()
        order = self.get(order_id)
        self._publish(order, "order_created")
        return order

    async def complete_payment(self, order_id: str) -> Order:
        async with self._lock(order_id):
            order = self._require(order_id)
            if order.status != "awaiting_payment":
                return order
            if order.paypal_order_id is None:
                raise PaymentNotCompleted("order has no PayPal order")
            try:
                result = await self.paypal.capture_order(order.paypal_order_id, request_key=order_id)
            except (PayPalError, httpx.HTTPError) as exc:
                raise PaymentNotCompleted(f"capture failed: {type(exc).__name__}") from exc
            if result.status != "COMPLETED":
                held = result.status == "PENDING"
                if held != order.payment_held:
                    self._update(order_id, payment_held=int(held))
                raise PaymentNotCompleted(f"capture status {result.status}")
            self._update(order_id, status="paid", capture_id=result.capture_id, payment_held=0)
            order = self.get(order_id)
            self._publish(order, "order_paid")
            return order

    async def refresh_from_paypal(self, order_id: str) -> Order:
        order = self._require(order_id)
        if order.status != "awaiting_payment" or order.paypal_order_id is None:
            return order
        try:
            paypal_order = await self.paypal.get_order(order.paypal_order_id)
        except _PAYPAL_ERRORS:
            return order
        # COMPLETED means a capture already exists (a lost capture response or a pending capture);
        # complete_payment recovers it through the ORDER_ALREADY_CAPTURED path.
        if paypal_order.get("status") in _CAPTURABLE_PAYPAL_STATUSES:
            return await self.complete_payment(order_id)
        return order

    async def validate_payee_merchant_id(self, merchant_id: str) -> bool:
        probe = PricedOrder(
            lines=[PricedLine(item_id="probe", name="Payee check", option_names=[], unit_price_cents=1, quantity=1, line_total_cents=1)],
            total_cents=1,
        )
        try:
            await self.paypal.create_order(
                reference_id=new_id("order"),
                priced=probe,
                payee_merchant_id=merchant_id,
                payee_email=None,
                brand_name="Awning",
                description=f"Payee check ${format_cents(1)}",
                return_url=f"{self.public_url}/",
                cancel_url=f"{self.public_url}/",
            )
        except PayeeInvalidError:
            return False
        return True

    def set_status(self, order_id: str, admin_token: str, status: Literal["ready", "picked_up"]) -> Order:
        order = self._authorize(order_id, admin_token)
        if order.status != _TRANSITIONS[status]:
            raise TransitionError(f"cannot move order from {order.status} to {status}")
        self._update(order_id, status=status)
        order = self.get(order_id)
        self._publish(order, "order_updated")
        return order

    async def refund(self, order_id: str, admin_token: str) -> Order:
        shop = self._shop_for(self._require(order_id), admin_token)
        if not (shop.is_demo_payee or self.third_party_refunds):
            raise RefundNotSupported("refunds are only available for demo-payee shops")
        async with self._lock(order_id):
            order = self._require(order_id)
            if order.status not in _POST_PAYMENT or order.capture_id is None:
                raise TransitionError(f"cannot refund order in status {order.status}")
            try:
                await self.paypal.refund_capture(order.capture_id, request_key=order_id)
            except _PAYPAL_ERRORS as exc:
                raise RefundFailed(f"refund failed: {type(exc).__name__}") from exc
            self._update(order_id, status="refunded")
            order = self.get(order_id)
            self._publish(order, "order_updated")
            return order

    def get(self, order_id: str) -> Order | None:
        row = self.db.conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return self._to_order(row) if row else None

    def get_by_paypal_order_id(self, paypal_order_id: str) -> Order | None:
        row = self.db.conn.execute(
            "SELECT * FROM orders WHERE paypal_order_id = ?", (paypal_order_id,)
        ).fetchone()
        return self._to_order(row) if row else None

    def get_by_capture_id(self, capture_id: str) -> Order | None:
        row = self.db.conn.execute("SELECT * FROM orders WHERE capture_id = ?", (capture_id,)).fetchone()
        return self._to_order(row) if row else None

    async def mark_refunded(self, order_id: str) -> Order:
        async with self._lock(order_id):
            order = self._require(order_id)
            if order.status not in _POST_PAYMENT:
                return order
            self._update(order_id, status="refunded")
            order = self.get(order_id)
            self._publish(order, "order_updated")
            return order

    def list_for_shop(self, shop_id: str, limit: int = 100) -> list[Order]:
        rows = self.db.conn.execute(
            "SELECT * FROM orders WHERE shop_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (shop_id, limit),
        ).fetchall()
        return [self._to_order(row) for row in rows]

    def _require(self, order_id: str) -> Order:
        order = self.get(order_id)
        if order is None:
            raise OrderNotFound(order_id)
        return order

    def _shop_for(self, order: Order, admin_token: str) -> Shop:
        shop = self.catalog.get_by_admin_token(admin_token)
        if shop is None or shop.id != order.shop_id:
            raise ShopNotFound("admin token does not match this order's shop")
        return shop

    def _authorize(self, order_id: str, admin_token: str) -> Order:
        order = self._require(order_id)
        self._shop_for(order, admin_token)
        return order

    def _update(self, order_id: str, **fields: str | int | None) -> None:
        assignments = ", ".join(f"{name} = ?" for name in fields)
        self.db.conn.execute(
            f"UPDATE orders SET {assignments}, updated_at = ? WHERE id = ?",
            (*fields.values(), _now(), order_id),
        )
        self.db.conn.commit()

    def _publish(self, order: Order, event_type: str) -> None:
        self.events.publish(order.shop_id, {"type": event_type, "order": order.model_dump(mode="json")})

    @staticmethod
    def _to_order(row: sqlite3.Row) -> Order:
        return Order(
            id=row["id"],
            shop_id=row["shop_id"],
            status=row["status"],
            channel=row["channel"],
            lines=[PricedLine(**line) for line in json.loads(row["lines_json"])],
            total_cents=row["total_cents"],
            customer_name=row["customer_name"],
            pickup_time=row["pickup_time"],
            note=row["note"],
            paypal_order_id=row["paypal_order_id"],
            approval_url=row["approval_url"],
            capture_id=row["capture_id"],
            payment_held=bool(row["payment_held"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
