from dataclasses import dataclass

from awning.catalog import Catalog
from awning.config import Settings
from awning.db import Database


@dataclass
class AppContext:
    settings: Settings
    db: Database
    catalog: Catalog


def build_context(settings: Settings) -> AppContext:
    db = Database(settings.db_path)
    db.init_schema()
    return AppContext(settings=settings, db=db, catalog=Catalog(db))
