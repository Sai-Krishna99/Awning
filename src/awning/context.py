from dataclasses import dataclass

from anthropic import AsyncAnthropic

from awning.catalog import Catalog
from awning.config import Settings
from awning.db import Database
from awning.events import EventBus
from awning.extraction import MenuExtractor
from awning.orders import OrderService
from awning.paypal import PayPalClient


@dataclass
class AppContext:
    settings: Settings
    db: Database
    catalog: Catalog
    paypal: PayPalClient
    events: EventBus
    orders: OrderService
    extractor: MenuExtractor


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
        api_key=settings.anthropic_api_key, base_url=settings.anthropic_base_url
    )
    extractor = MenuExtractor(
        anthropic_client, settings.extraction_model, settings.extraction_effort
    )
    return AppContext(
        settings=settings, db=db, catalog=catalog, paypal=paypal, events=events,
        orders=orders, extractor=extractor,
    )
