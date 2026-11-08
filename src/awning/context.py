from dataclasses import dataclass

from awning.config import Settings
from awning.db import Database


@dataclass
class AppContext:
    settings: Settings
    db: Database


def build_context(settings: Settings) -> AppContext:
    db = Database(settings.db_path)
    db.init_schema()
    return AppContext(settings=settings, db=db)
