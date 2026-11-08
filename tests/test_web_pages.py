from fastapi.testclient import TestClient

from awning.web.app import create_app
from test_mcp import HEADERS, INIT
from test_web_shop import FOOTER, make_ctx, publish


def make_client(tmp_path, **extra) -> tuple:
    ctx = make_ctx(tmp_path, **extra)
    return ctx, TestClient(create_app(ctx))


def test_index_shows_mcp_and_ctas(tmp_path):
    ctx, client = make_client(tmp_path, AWNING_PUBLIC_URL="https://awning.example")
    html = client.get("/").text
    assert "any AI assistant can order from you" in html and "Who does what" in html
    for href in ('href="/start"', 'href="/try"', 'href="/connect"', 'href="/directory"'):
        assert href in html
    assert "Turn your menu into a shop" in html and "Watch an agent order" in html
    assert FOOTER in html


def test_index_previews_published_shops_escaped(tmp_path):
    ctx, client = make_client(tmp_path)
    publish(ctx, name="<script>x</script> Cart")
    html = client.get("/").text
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;x&lt;/script&gt; Cart" in html


def test_directory_lists_only_published(tmp_path):
    ctx, client = make_client(tmp_path)
    publish(ctx, name="Pub Cart")
    ctx.catalog.create_draft([])
    html = client.get("/directory").text
    assert "Pub Cart" in html and "Corner of 5th" in html
    assert html.count('class="card shop-card') == 1


def test_directory_empty_state(tmp_path):
    _, client = make_client(tmp_path)
    assert "No shops yet" in client.get("/directory").text


def test_connect_page_has_mcp_url(tmp_path):
    _, client = make_client(tmp_path, AWNING_PUBLIC_URL="https://awning.example/")
    html = client.get("/connect").text
    assert "https://awning.example/mcp" in html
    for text in ("Add custom connector", "No sign-in", "Free", "ChatGPT", "find_shops", "get_menu", "create_order", "get_order_status"):
        assert text in html


def test_mcp_still_served_alongside_pages(tmp_path):
    _, client = make_client(tmp_path)
    with client:
        assert client.get("/").status_code == 200
        assert client.post("/mcp", json=INIT, headers=HEADERS).status_code == 200


BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


def test_browser_get_mcp_shows_explainer(tmp_path):
    _, client = make_client(tmp_path)
    with client:
        response = client.get("/mcp", headers={"Accept": BROWSER_ACCEPT})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "This is an MCP endpoint" in response.text
        assert 'href="/connect"' in response.text
        assert FOOTER in response.text
        assert client.post("/mcp", json=INIT, headers=HEADERS).status_code == 200


def test_sse_get_mcp_is_405(tmp_path):
    _, client = make_client(tmp_path)
    with client:
        response = client.get("/mcp", headers={"Accept": "text/event-stream"})
        assert response.status_code == 405
        assert response.headers["allow"] == "POST"


def test_favicon_is_svg_and_linked(tmp_path):
    _, client = make_client(tmp_path)
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in response.text
    assert 'rel="icon" href="/favicon.ico"' in client.get("/").text


def test_unknown_get_path_renders_404_page(tmp_path):
    _, client = make_client(tmp_path)
    with client:
        response = client.get("/no-such-page")
        assert response.status_code == 404
        assert "We couldn't find that page" in response.text and FOOTER in response.text
        assert client.get("/static/app.css").status_code == 200
        assert client.post("/mcp", json=INIT, headers=HEADERS).status_code == 200


def test_connect_lists_real_order_statuses(tmp_path):
    _, client = make_client(tmp_path)
    html = client.get("/connect").text
    assert "cancelled" not in html
    assert "awaiting payment, paid, ready, picked up or refunded" in html


def test_connect_chatgpt_note_is_soft(tmp_path):
    _, client = make_client(tmp_path)
    html = client.get("/connect").text
    assert "write tools are limited on most ChatGPT plans; Claude.ai is the recommended path" in html
    assert "Business, Enterprise and Edu" not in html
