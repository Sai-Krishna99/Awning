from dataclasses import dataclass

from awning.catalog import Catalog
from awning.config import Settings
from awning.db import Database
from awning.paypal import PayPalClient


@dataclass
class AppContext:
    settings: Settings
    db: Database
    catalog: Catalog
    paypal: PayPalClient


def build_context(settings: Settings) -> AppContext:
    db = Database(settings.db_path)
    db.init_schema()
    paypal = PayPalClient(
        settings.paypal_client_id, settings.paypal_client_secret, settings.paypal_base_url
    )
    return AppContext(settings=settings, db=db, catalog=Catalog(db), paypal=paypal)
