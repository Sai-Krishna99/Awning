import secrets
from datetime import datetime, timedelta, timezone

from awning.db import Database

STATE_TTL = timedelta(minutes=15)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PayPalConnections:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create_state(self, shop_id: str) -> str:
        state = secrets.token_urlsafe(24)
        self.db.conn.execute(
            "INSERT INTO paypal_login_states (state, shop_id, expires_at) VALUES (?, ?, ?)",
            (state, shop_id, (_now() + STATE_TTL).isoformat()),
        )
        self.db.conn.commit()
        return state

    def consume_state(self, state: str) -> str | None:
        """Returns the shop id the state was issued for, or None if unknown, expired or already used."""
        now = _now().isoformat()
        cursor = self.db.conn.execute(
            "UPDATE paypal_login_states SET used_at = ? WHERE state = ? AND used_at IS NULL AND expires_at > ?",
            (now, state, now),
        )
        self.db.conn.commit()
        if cursor.rowcount != 1:
            return None
        return self.db.conn.execute("SELECT shop_id FROM paypal_login_states WHERE state = ?", (state,)).fetchone()["shop_id"]

    def save(self, shop_id: str, merchant_id: str, email: str | None) -> None:
        self.db.conn.execute(
            "INSERT OR REPLACE INTO paypal_connections (shop_id, merchant_id, email, created_at) VALUES (?, ?, ?, ?)",
            (shop_id, merchant_id, email, _now().isoformat()),
        )
        self.db.conn.commit()

    def get(self, shop_id: str) -> dict | None:
        row = self.db.conn.execute(
            "SELECT merchant_id, email FROM paypal_connections WHERE shop_id = ?", (shop_id,)
        ).fetchone()
        return {"merchant_id": row["merchant_id"], "email": row["email"]} if row else None

    def clear(self, shop_id: str) -> None:
        self.db.conn.execute("DELETE FROM paypal_connections WHERE shop_id = ?", (shop_id,))
        self.db.conn.commit()
