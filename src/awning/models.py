import secrets
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

_ID_PREFIXES = {"shop": ("shp_", 10), "item": ("itm_", 8), "option": ("opt_", 8), "order": ("ord_", 12)}


def new_id(kind: Literal["shop", "item", "option", "order"]) -> str:
    prefix, length = _ID_PREFIXES[kind]
    return prefix + secrets.token_urlsafe(length)[:length]


def new_admin_token() -> str:
    return secrets.token_urlsafe(24)


class ItemOption(BaseModel):
    id: str
    name: str
    price_delta_cents: int = 0


class MenuItem(BaseModel):
    id: str
    name: str
    description: str = ""
    category: str = ""
    price_cents: int | None
    options: list[ItemOption] = []
    available: bool = True
    needs_review: bool = False
    review_note: str = ""


ShopStatus = Literal["draft", "published"]


class Shop(BaseModel):
    id: str
    slug: str | None
    admin_token: str
    status: ShopStatus
    name: str
    description: str
    pickup_location: str
    pickup_hours: str
    payee_merchant_id: str | None
    payee_email: str | None
    items: list[MenuItem]
    created_at: datetime
    published_at: datetime | None

    @property
    def is_demo_payee(self) -> bool:
        return self.payee_merchant_id is None and self.payee_email is None


class ShopDetails(BaseModel):
    name: str
    description: str
    pickup_location: str
    pickup_hours: str
    payee_merchant_id: str | None
    payee_email: str | None
    payee_email_confirmed: bool = False


class OrderLineRequest(BaseModel):
    item_id: str
    option_ids: list[str] = []
    quantity: int


class PricedLine(BaseModel):
    item_id: str
    name: str
    option_names: list[str]
    unit_price_cents: int
    quantity: int
    line_total_cents: int


class PricedOrder(BaseModel):
    lines: list[PricedLine]
    total_cents: int


OrderStatus = Literal["awaiting_payment", "paid", "ready", "picked_up", "refunded"]
Channel = Literal["agent", "web", "demo_agent"]


class Order(BaseModel):
    id: str
    shop_id: str
    status: OrderStatus
    channel: Channel
    lines: list[PricedLine]
    total_cents: int
    customer_name: str
    pickup_time: str
    note: str
    paypal_order_id: str | None
    approval_url: str | None
    capture_id: str | None
    created_at: datetime
    updated_at: datetime
