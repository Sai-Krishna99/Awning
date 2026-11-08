from dataclasses import dataclass, field

from anthropic import AsyncAnthropic

from awning.agent import DemoAgent, SessionStore
from awning.catalog import Catalog
from awning.config import Settings
from awning.db import Database
from awning.events import EventBus
from awning.extraction import MenuExtractor
from awning.orders import OrderService
from awning.paypal import PayPalClient
from awning.ratelimit import RateLimiter

ANTHROPIC_TIMEOUT_S = 90


@dataclass
class AppContext:
    settings: Settings
    db: Database
    catalog: Catalog
    paypal: PayPalClient
    events: EventBus
    orders: OrderService
    extractor: MenuExtractor
    upload_limiter_ip: RateLimiter = field(default_factory=lambda: RateLimiter(5, 3600))
    upload_limiter_global: RateLimiter = field(default_factory=lambda: RateLimiter(100, 86400))
    agent_sessions: SessionStore = field(default_factory=SessionStore)
    agent_limiter_ip: RateLimiter = field(default_factory=lambda: RateLimiter(30, 3600))
    agent_limiter_global: RateLimiter = field(default_factory=lambda: RateLimiter(300, 86400))
    agent_session_limiter_ip: RateLimiter = field(default_factory=lambda: RateLimiter(20, 3600))
    order_limiter_ip: RateLimiter = field(default_factory=lambda: RateLimiter(20, 3600))
    order_limiter_global: RateLimiter = field(default_factory=lambda: RateLimiter(500, 86400))
    agent: DemoAgent = field(init=False)

    def allow_order(self, client_ip: str | None) -> bool:
        """Per-IP plus global limit on unauthenticated order creation; global only when the IP is unknown."""
        if client_ip is not None and not self.order_limiter_ip.allow(client_ip):
            return False
        return self.order_limiter_global.allow("global")


def build_context(settings: Settings) -> AppContext:
    db = Database(settings.db_path)
    db.init_schema()
    paypal = PayPalClient(
        settings.paypal_client_id, settings.paypal_client_secret, settings.paypal_base_url
    )
    catalog = Catalog(db)
    events = EventBus()
    orders = OrderService(
        db, catalog, paypal, events, settings.public_url, settings.third_party_refunds
    )
    anthropic_client = AsyncAnthropic(
        api_key=settings.anthropic_api_key, base_url=settings.anthropic_base_url, timeout=ANTHROPIC_TIMEOUT_S
    )
    extractor = MenuExtractor(
        anthropic_client, settings.extraction_model, settings.extraction_effort
    )
    ctx = AppContext(
        settings=settings, db=db, catalog=catalog, paypal=paypal, events=events,
        orders=orders, extractor=extractor,
    )
    ctx.agent = DemoAgent(anthropic_client, settings.agent_model, settings.agent_effort, ctx)
    return ctx
