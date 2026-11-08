import re
from pathlib import Path

from mcp.server.apps import Apps

CARD_URI = "ui://awning/order-status.html"
_WEB = Path(__file__).parent / "web"
_BUNDLE = _WEB / "vendor" / "ext-apps" / "app-with-deps.js"
_EXPORTED = ("App", "applyDocumentTheme", "applyHostStyleVariables", "applyHostFonts")


def _bundle_as_global(source: str) -> str:
    match = re.search(r"export\s*\{([^}]*)\}\s*;?\s*$", source)
    if match is None:
        raise ValueError("ext-apps bundle has no trailing export clause")
    local_for = {}
    for part in match.group(1).split(","):
        local, _, exported = part.strip().partition(" as ")
        local_for[(exported or local).strip()] = local.strip()
    members = ",".join(f"{name}:{local_for[name]}" for name in _EXPORTED)
    return source[: match.start()] + f"const __extApps={{{members}}};"


def render_card_html() -> str:
    template = (_WEB / "mcp_card.html").read_text(encoding="utf-8")
    bundle = _bundle_as_global(_BUNDLE.read_text(encoding="utf-8"))
    return template.replace("/*__EXT_APPS_BUNDLE__*/", bundle)


def build_apps() -> Apps:
    apps = Apps()
    apps.add_html_resource(
        CARD_URI,
        render_card_html(),
        name="Awning order status card",
        description="Read-only order status card. Shows the order and its live status; no payment UI.",
        prefers_border=True,
    )
    return apps
