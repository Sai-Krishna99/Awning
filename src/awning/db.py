import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS shops (
    id TEXT PRIMARY KEY,
    slug TEXT UNIQUE,
    admin_token TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    pickup_location TEXT NOT NULL,
    pickup_hours TEXT NOT NULL,
    payee_merchant_id TEXT,
    payee_email TEXT,
    items_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    published_at TEXT,
    suspicious_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    shop_id TEXT NOT NULL,
    status TEXT NOT NULL,
    channel TEXT NOT NULL,
    lines_json TEXT NOT NULL,
    total_cents INTEGER NOT NULL,
    customer_name TEXT NOT NULL,
    pickup_time TEXT NOT NULL,
    note TEXT NOT NULL,
    paypal_order_id TEXT UNIQUE,
    approval_url TEXT,
    capture_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payment_held INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS extractions (
    id TEXT PRIMARY KEY,
    shop_id TEXT,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paypal_login_states (
    state TEXT PRIMARY KEY,
    shop_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);
CREATE TABLE IF NOT EXISTS paypal_connections (
    shop_id TEXT PRIMARY KEY,
    merchant_id TEXT NOT NULL,
    email TEXT,
    created_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str) -> None:
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(shops)")}
        if "suspicious_json" not in columns:
            self.conn.execute("ALTER TABLE shops ADD COLUMN suspicious_json TEXT NOT NULL DEFAULT '[]'")
        order_columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(orders)")}
        if "payment_held" not in order_columns:
            self.conn.execute("ALTER TABLE orders ADD COLUMN payment_held INTEGER NOT NULL DEFAULT 0")
        self.conn.commit()
