import json
from pathlib import Path

from awning.catalog import Catalog
from awning.models import ItemOption, MenuItem, Shop, ShopDetails

DEFAULT_SEED_PATH = Path(__file__).resolve().parents[2] / "seed" / "marias-cart.json"
DEMO_SLUG = "marias-cart"


def seed_demo_shop(
    catalog: Catalog,
    path: Path,
    payee_merchant_id: str | None = None,
    payee_email: str | None = None,
) -> Shop | None:
    if catalog.get_published(DEMO_SLUG) is not None:
        return None
    data = json.loads(path.read_text())
    items = [
        MenuItem(
            id="",
            name=raw["name"],
            description=raw.get("description", ""),
            category=raw.get("category", ""),
            price_cents=raw["price_cents"],
            options=[ItemOption(id="", **opt) for opt in raw.get("options", [])],
        )
        for raw in data["items"]
    ]
    draft = catalog.create_draft(items, name_guess=data["name"])
    details = ShopDetails(
        name=data["name"],
        description=data["description"],
        pickup_location=data["pickup_location"],
        pickup_hours=data["pickup_hours"],
        payee_merchant_id=payee_merchant_id,
        payee_email=None if payee_merchant_id else payee_email,
    )
    return catalog.publish(draft.admin_token, details)
