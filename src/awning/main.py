from awning.config import load_settings
from awning.context import build_context
from awning.web.app import create_app

app = create_app(build_context(load_settings()))
