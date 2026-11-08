import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    paypal_client_id: str
    paypal_client_secret: str
    paypal_base_url: str
    paypal_webhook_id: str | None
    paypal_platform_merchant_id: str
    demo_payee_merchant_id: str | None
    demo_payee_email: str | None
    anthropic_api_key: str
    anthropic_base_url: str
    extraction_model: str
    extraction_effort: str
    agent_model: str
    agent_effort: str
    public_url: str
    allowed_hosts: list[str]
    db_path: str
    third_party_refunds: bool


def _derive_allowed_hosts(public_url: str, env: Mapping[str, str]) -> list[str]:
    candidates = [
        urlparse(public_url).netloc,
        env.get("RENDER_EXTERNAL_HOSTNAME", ""),
        "localhost",
        "127.0.0.1",
        "testserver",
    ]
    hosts: list[str] = []
    for host in candidates:
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    if env is None:
        load_dotenv(override=False)
        env = os.environ
    public_url = (
        env.get("AWNING_PUBLIC_URL") or env.get("RENDER_EXTERNAL_URL") or "http://localhost:8000"
    ).rstrip("/")
    return Settings(
        paypal_client_id=env.get("PAYPAL_CLIENT_ID", ""),
        paypal_client_secret=env.get("PAYPAL_CLIENT_SECRET", ""),
        paypal_base_url=env.get("PAYPAL_BASE_URL") or "https://api-m.sandbox.paypal.com",
        paypal_webhook_id=env.get("PAYPAL_WEBHOOK_ID") or None,
        paypal_platform_merchant_id=env.get("PAYPAL_PLATFORM_MERCHANT_ID", ""),
        demo_payee_merchant_id=env.get("PAYPAL_DEMO_PAYEE_MERCHANT_ID") or None,
        demo_payee_email=env.get("PAYPAL_DEMO_PAYEE_EMAIL") or None,
        anthropic_api_key=env.get("ANTHROPIC_API_KEY", ""),
        anthropic_base_url=env.get("AWNING_ANTHROPIC_BASE_URL") or "https://api.anthropic.com",
        extraction_model=env.get("AWNING_EXTRACTION_MODEL") or "claude-opus-5-5",
        extraction_effort=env.get("AWNING_EXTRACTION_EFFORT") or "low",
        agent_model=env.get("AWNING_AGENT_MODEL") or "claude-opus-5-5",
        agent_effort=env.get("AWNING_AGENT_EFFORT") or "low",
        public_url=public_url,
        allowed_hosts=_derive_allowed_hosts(public_url, env),
        db_path=env.get("AWNING_DB_PATH") or "awning.db",
        third_party_refunds=env.get("AWNING_THIRD_PARTY_REFUNDS", "").lower() in ("1", "true", "yes"),
    )
