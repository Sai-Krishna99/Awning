import json
import re
import secrets
import sqlite3
import string
from datetime import datetime, timezone
from typing import Literal

from awning.db import Database
from awning.models import MenuItem, Shop, ShopDetails, new_admin_token, new_id

MAX_ITEMS = 100
_SLUG_ALPHABET = string.ascii_lowercase + string.digits


class CatalogValidationError(Exception):
    def __init__(self, errors: list[dict]) -> None:
        super().__init__(f"{len(errors)} validation error(s)")
        self.errors = errors


class PublishError(Exception):
    def __init__(self, code: Literal["no_items", "needs_review", "missing_name", "already_published"]) -> None:
        super().__init__(code)
        self.code = code


def _assign_ids(items: list[MenuItem]) -> list[MenuItem]:
    assigned = []
    for item in items:
        options = [opt if opt.id else opt.model_copy(update={"id": new_id("option")}) for opt in item.options]
        update = {"options": options}
        if not item.id:
            update["id"] = new_id("item")
        assigned.append(item.model_copy(update=update))
    return assigned


def _validate_items(items: list[MenuItem]) -> None:
    errors: list[dict] = []

    def add(row: int, field: str, message: str) -> None:
        errors.append({"row": row, "field": field, "message": message})

    if len(items) > MAX_ITEMS:
        add(MAX_ITEMS, "items", f"at most {MAX_ITEMS} items allowed")
    for row, item in enumerate(items):
        if not 1 <= len(item.name) <= 80:
            add(row, "name", "name must be 1-80 characters")
        if not item.needs_review and (item.price_cents is None or not 1 <= item.price_cents <= 100000):
            add(row, "price_cents", "price must be between 1 and 100000 cents")
        for opt_index, option in enumerate(item.options):
            if not 0 <= option.price_delta_cents <= 50000:
                add(row, f"options[{opt_index}].price_delta_cents", "option price must be between 0 and 50000 cents")
    if errors:
        raise CatalogValidationError(errors)


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("'", "")).strip("-")[:40].strip("-")


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class Catalog:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create_draft(
        self, items: list[MenuItem], name_guess: str | None = None, hours_guess: str | None = None
    ) -> Shop:
        shop_id = new_id("shop")
        token = new_admin_token()
        now = datetime.now(timezone.utc).isoformat()
        items_json = json.dumps([i.model_dump() for i in _assign_ids(items)])
        self.db.conn.execute(
            "INSERT INTO shops (id, slug, admin_token, status, name, description, pickup_location, pickup_hours,"
            " items_json, created_at) VALUES (?, NULL, ?, 'draft', ?, '', '', ?, ?, ?)",
            (shop_id, token, name_guess or "", hours_guess or "", items_json, now),
        )
        self.db.conn.commit()
        return self._one("id = ?", (shop_id,))

    def get_by_admin_token(self, token: str) -> Shop | None:
        return self._one("admin_token = ?", (token,))

    def get_by_id(self, shop_id: str) -> Shop | None:
        return self._one("id = ?", (shop_id,))

    def get_published(self, slug: str) -> Shop | None:
        return self._one("slug = ? AND status = 'published'", (slug,))

    def update_items(self, admin_token: str, items: list[MenuItem]) -> Shop:
        shop = self._require(admin_token)
        _validate_items(items)
        items_json = json.dumps([i.model_dump() for i in _assign_ids(items)])
        self.db.conn.execute("UPDATE shops SET items_json = ? WHERE id = ?", (items_json, shop.id))
        self.db.conn.commit()
        return self._one("id = ?", (shop.id,))

    def publish(self, admin_token: str, details: ShopDetails) -> Shop:
        shop = self._require(admin_token)
        if shop.status == "published":
            raise PublishError("already_published")
        if not shop.items:
            raise PublishError("no_items")
        if any(i.needs_review for i in shop.items):
            raise PublishError("needs_review")
        base_slug = _slugify(details.name)
        if not base_slug:
            raise PublishError("missing_name")
        slug = base_slug
        while self._slug_taken(slug):
            slug = f"{base_slug}-{''.join(secrets.choice(_SLUG_ALPHABET) for _ in range(4))}"
        self.db.conn.execute(
            "UPDATE shops SET slug = ?, status = 'published', name = ?, description = ?, pickup_location = ?,"
            " pickup_hours = ?, payee_merchant_id = ?, payee_email = ?, published_at = ? WHERE id = ?",
            (
                slug,
                details.name.strip(),
                details.description,
                details.pickup_location,
                details.pickup_hours,
                details.payee_merchant_id,
                details.payee_email,
                datetime.now(timezone.utc).isoformat(),
                shop.id,
            ),
        )
        self.db.conn.commit()
        return self._one("id = ?", (shop.id,))

    def search(self, query: str, limit: int = 10) -> list[Shop]:
        query_tokens = set(_tokens(query))
        shops = self.list_published(limit=10_000)
        if not query_tokens:
            return shops[:limit]
        scored = []
        for shop in shops:
            haystack = set(
                _tokens(" ".join([shop.name, shop.description, shop.pickup_location, *(i.name for i in shop.items)]))
            )
            matched = len(query_tokens & haystack)
            if matched:
                scored.append((matched, shop))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [shop for _, shop in scored[:limit]]

    def list_published(self, limit: int = 50) -> list[Shop]:
        rows = self.db.conn.execute(
            "SELECT * FROM shops WHERE status = 'published' ORDER BY published_at DESC, id LIMIT ?", (limit,)
        ).fetchall()
        return [self._to_shop(row) for row in rows]

    def _require(self, admin_token: str) -> Shop:
        shop = self.get_by_admin_token(admin_token)
        if shop is None:
            raise KeyError("shop not found")
        return shop

    def _slug_taken(self, slug: str) -> bool:
        return self.db.conn.execute("SELECT 1 FROM shops WHERE slug = ?", (slug,)).fetchone() is not None

    def _one(self, where: str, params: tuple) -> Shop | None:
        row = self.db.conn.execute(f"SELECT * FROM shops WHERE {where}", params).fetchone()
        return self._to_shop(row) if row else None

    @staticmethod
    def _to_shop(row: sqlite3.Row) -> Shop:
        return Shop(
            id=row["id"],
            slug=row["slug"],
            admin_token=row["admin_token"],
            status=row["status"],
            name=row["name"],
            description=row["description"],
            pickup_location=row["pickup_location"],
            pickup_hours=row["pickup_hours"],
            payee_merchant_id=row["payee_merchant_id"],
            payee_email=row["payee_email"],
            items=[MenuItem(**i) for i in json.loads(row["items_json"])],
            created_at=row["created_at"],
            published_at=row["published_at"],
        )
