from awning.config import load_settings
from awning.db import Database

REQUIRED = {"PAYPAL_CLIENT_ID": "x", "PAYPAL_CLIENT_SECRET": "y", "ANTHROPIC_API_KEY": "z"}


def test_schema_creates_tables(tmp_path) -> None:
    db = Database(str(tmp_path / "t.db"))
    db.init_schema()
    names = {r["name"] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"shops", "orders", "extractions"} <= names


def test_settings_defaults() -> None:
    s = load_settings(REQUIRED)
    assert s.anthropic_base_url == "https://api.anthropic.com"
    assert s.extraction_model == "claude-opus-5-5"
    assert s.public_url == "http://localhost:8000"
    assert "localhost" in s.allowed_hosts


def test_settings_ignores_anthropic_base_url_env() -> None:
    s = load_settings({**REQUIRED, "ANTHROPIC_BASE_URL": "https://elsewhere"})
    assert s.anthropic_base_url == "https://api.anthropic.com"


def test_public_url_falls_back_to_render_and_derives_hosts() -> None:
    s = load_settings({**REQUIRED, "RENDER_EXTERNAL_URL": "https://awning.onrender.com/"})
    assert s.public_url == "https://awning.onrender.com"
    assert "awning.onrender.com" in s.allowed_hosts
