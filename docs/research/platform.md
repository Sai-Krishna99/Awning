# Awning: platform research

Date: 2026-11-07. Scope: MCP Python SDK, Claude.ai and ChatGPT connectors, Render, AG Grid, and SSE in FastAPI.
"Verified" means I ran it locally. Spike code is in `/tmp/claude-1000/-home-sai-projects-paypal-ai/b139f6a9-5881-4358-805d-fe7146d5cd03/scratchpad/mcp-spike/` and `.../scratchpad/aggrid-spike/`.

## TL;DR: findings that change the design or plan

1. **MCP SDK 2.x is a breaking rewrite.** `FastMCP` is now `MCPServer` (`from mcp.server import MCPServer`), and `mcp.server.fastmcp` no longer exists. Most tutorials and model memory show v1 code, which will not import. Pin `mcp>=2.3,<3`.
2. **Host-header protection is on by default.** The default `host` is `127.0.0.1`, so the mounted MCP app answers **421 Misdirected Request** for any other `Host`. That includes `awning.onrender.com` and FastAPI `TestClient`'s `testserver`. Pass `transport_security=TransportSecuritySettings(allowed_hosts=[...])` and build the list from `RENDER_EXTERNAL_HOSTNAME`. Verified.
3. **Render: free plan has no persistent disk and is slow to wake.** Persistent disks are only available on paid plans. A free instance also loses its local SQLite file on every deploy, restart and spin-down, and takes about 1 minute to wake after 15 idle minutes. That will likely break the Claude.ai connector spike. **Recommendation: use Starter (`0.5c-512mb`, $7/mo, billed by the second) plus a 1 GB disk from the first deploy.** It costs about $0.24/day. Nov 7 to Dec 21 comes to about $11, which is in line with §8.
4. **Render's default Python is now 3.14.10.** Pin 3.12 with a `.python-version` file.
5. **ChatGPT is unlikely to work for judges' orders.** Write-capable custom MCP tools appear to be limited to Business/Enterprise/Edu, Pro gets read/fetch only, and Plus is unclear. Keep Claude.ai as the headline path and the built-in chat as the fallback, as §12 already does.
6. **FastAPI now has built-in SSE** (`fastapi.sse`, since 0.135.0), with a 15 s keep-alive and disconnect cleanup. `sse-starlette` is not needed.
7. **Claude.ai Free plan allows one custom connector**, so judges on Free can still add Awning. The tool-call timeout is 240 s.

## 1. MCP Python SDK

**Versions.** PyPI `mcp` **2.3.0** was released 2026-10-02 (there is also a 1.x maintenance line, 1.30.0). It requires Python ≥3.10 and brings in `httpx2`, `starlette`, `sse-starlette` and `uvicorn`.
Verified with: mcp 2.3.0, fastapi 0.162.2, starlette 1.10.0, uvicorn 0.54.0 and Python 3.12. Note that fastapi 0.162.2 was released *today*; anything ≥0.135 has SSE.

**Facts (all verified):**
- **Where the transport options live.** `stateless_http`, `json_response`, `streamable_http_path` and `transport_security` are passed to `mcp.streamable_http_app(...)`. They are no longer constructor arguments: `MCPServer(..., port=)` raises `TypeError`, and `mount_path` was removed.
- **The endpoint path is the mount prefix plus `streamable_http_path`** (default `/mcp`).
  - `app.mount("/", mcp_app)` serves exactly `/mcp`. **Use this.**
  - `mount("/mcp")` with the default path serves `/mcp/mcp`, and a request to `/mcp` gets a 307.
  - `mount("/mcp")` with `streamable_http_path="/"` serves `/mcp/`, and `/mcp` gets a 307. Avoid both: Claude follows redirects, but don't rely on that.
- **The host app's lifespan must enter `mcp.session_manager.run()`.** A mounted sub-app's lifespan never runs. Without it you get `RuntimeError: Task group is not initialized. Make sure to use run().`
- **Mount order matters.** A `/` mount shadows every route registered *after* it (verified: such a route returns 404). Register all routers and `StaticFiles` mounts first, and the MCP mount **last**.
- **Protocol versions.** In stateless mode the server answered 2025-03-26, 2025-06-18, 2025-11-25 (legacy client) and 2026-07-28 (new sessionless revision). It also accepted `tools/call` without a prior `initialize`.
- **Why `stateless_http=True`.** There is no `Mcp-Session-Id` and no in-memory session, so a redeploy or restart does not break connected clients. A stateful server would return 404 to old session IDs. Docs: the flag only affects 2025-era clients, because 2026-07-28 is sessionless by design.
- **What `json_response=True` changes.** It returns `application/json` instead of an SSE stream for each POST. The trade-off is no mid-call progress notifications, which we don't need.
- **Return Pydantic models from tools** to get `structuredContent` plus a JSON text block. A plain `dict` annotation produced text only.
- **Errors.** `raise ToolError("...")` (`mcp.server.mcpserver.exceptions`) returns `isError: true` with the message. Bad argument types are rejected the same way, using Pydantic validation.
- **Origin checks.** A missing `Origin` header passes. A present `Origin` must be in `allowed_origins`, otherwise 403. Claude's docs warn that over-strict Origin checks break connections, so `https://claude.ai` is included.
- **Proxy headers on Render.** Behind Render's TLS proxy, run uvicorn with `--forwarded-allow-ips='*'`, or redirects and URLs come out as `http://`. Build PayPal return URLs from `RENDER_EXTERNAL_URL` or a `PUBLIC_BASE_URL` env var, not from `request.url`.

**Verified server pattern** (`server.py`):

```python
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError  # raise for clean isError results
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel


class MenuItem(BaseModel):
    id: str
    name: str
    price: str


class Menu(BaseModel):
    shop_slug: str
    items: list[MenuItem]


mcp = MCPServer("Awning", instructions="Find small local shops and place pickup orders paid via PayPal.")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def get_menu(shop_slug: str) -> Menu:
    """Return the menu for a published shop."""
    return Menu(shop_slug=shop_slug, items=[MenuItem(id="oat-latte", name="Oat latte", price="4.50")])


public_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "localhost")
mcp_app = mcp.streamable_http_app(
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=[public_host, f"{public_host}:*", "localhost:*", "127.0.0.1:*", "testserver"],
        allowed_origins=[f"https://{public_host}", "https://claude.ai"],
    ),
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():  # also open DB etc. here
        yield


app = FastAPI(lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


app.mount("/", mcp_app)  # keep LAST: a "/" mount shadows any route registered after it
```

**Verified client** (over HTTP; `Client(mcp)` also works in memory, which is useful for unit tests):

```python
from mcp import Client

async with Client("http://127.0.0.1:8766/mcp") as client:          # mode="legacy" forces the 2025-11-25 handshake
    tools = await client.list_tools()                                # tools.tools[0].name == "get_menu"
    result = await client.call_tool("get_menu", {"shop_slug": "marias-cart"})
    result.structured_content   # {'shop_slug': 'marias-cart', 'items': [...]}
```

Commands used:

```
uv run --python 3.12 --with mcp==2.3.0 --with fastapi==0.162.2 --with uvicorn==0.54.0 uvicorn server:app --port 8766
uv run --python 3.12 --with mcp==2.3.0 python client.py http://127.0.0.1:8766/mcp
```

**Probe results:**
- `Host: awning.onrender.com` → 200
- `Host: evil.example.com` → 421
- `Origin: https://evil.example` → 403
- default settings (no `transport_security`) with a public Host → 421

Sources:
- https://pypi.org/project/mcp/
- https://github.com/modelcontextprotocol/python-sdk
- https://py.sdk.modelcontextprotocol.io/whats-new/
- https://py.sdk.modelcontextprotocol.io/migration/
- https://py.sdk.modelcontextprotocol.io/run/asgi/
- https://py.sdk.modelcontextprotocol.io/run/deploy/

## 2. Claude.ai custom connectors

| Question | Answer | Source |
|---|---|---|
| Transport | Streamable HTTP is recommended. Legacy HTTP+SSE still works but is being deprecated. A URL ending in `/sse` selects SSE. | building, add-unlisted |
| Authless allowed? | **Yes.** `none` is "Supported by default", and "No sign-in" is an option in the add dialog. | authentication, add-unlisted |
| Plans | Free, Pro, Max, Ultra, Team and Enterprise. **Free: 1 custom connector.** On Team/Enterprise only Owners add connectors, then members click Connect. | add-unlisted, support article |
| How to add (individual) | Customize → Connectors → **Add custom connector** → enter the HTTPS URL (e.g. `https://awning.onrender.com/mcp`) → Authentication: **No sign-in** → Add. In a chat: **+** → Connectors → toggle on. The first call asks for approval (Allow once / Always allow). | add-unlisted, getting-started |
| Surfaces | Remote connectors work on web, desktop and mobile once added. | getting-started |
| Timeouts / limits | Tool call **240 s**. Max tool result **~150,000 chars**. OAuth endpoints get 10 s (not relevant when authless). | building, authentication |
| Network | Requests come from Anthropic's cloud (`160.79.104.0/21`). The host must resolve to a public **IPv4** address. A 3xx to another host drops headers. | authentication, troubleshooting |
| Protocol / features | No sampling and no resource subscriptions. Text and image tool results are supported. `clientInfo.name` varies ("claude-ai", "Anthropic"), so don't gate on it. | building, testing |
| URL in a tool result as a clickable link | **Not documented.** Claude writes its reply in Markdown, so it normally repeats the URL as a link. **Verify in the connector spike.** To help: put `approval_url` in both the structured output and the text, and say in the tool description "give the user this link to approve payment in PayPal". | none |
| Cold start | **Not documented** for `initialize`. Free Render takes about 60 s to wake, so expect "Couldn't reach the MCP server" on the first try. Starter avoids this. | troubleshooting (causes) |

Sources:
- https://claude.com/docs/connectors/building
- https://claude.com/docs/connectors/building/authentication
- https://claude.com/docs/connectors/building/testing
- https://claude.com/docs/connectors/building/troubleshooting
- https://claude.com/docs/connectors/custom/add-unlisted
- https://claude.com/docs/connectors/getting-started
- https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp

### ChatGPT (low confidence)

OpenAI's help article returned 403 to my fetcher, so the plan details below come from search snippets of it plus third-party guides.
- **How to add.** Custom MCP servers are now added from **Plugins → + → "Add custom MCP server"**, where you give a name and a public HTTPS URL with the `/mcp` path. The older route was Settings → Apps & Connectors → Advanced → Developer mode.
- **Transport and auth.** Streamable HTTP; OAuth (CIMD or DCR) is documented. A no-auth option is reported by third-party guides but not by OpenAI's developer pages.
- **Plans.** Full MCP, including write actions, is on Business and Enterprise/Edu (web). Pro gets read/fetch only. Plus is unclear.
- **Confirmation.** ChatGPT "requires manual confirmation in any conversation before write actions can be taken".

**Conclusion:** `create_order` is a write tool, so most judges can't run it from ChatGPT. Claude.ai is the right headline path.

Sources:
- https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt
- https://developers.openai.com/apps-sdk/deploy/connect-chatgpt
- https://developers.openai.com/api/docs/mcp

## 3. Render

**uv support.**
- uv is detected **when `uv.lock` is in the root directory**. Render then adds uv to the runtime: default 0.13.7 for services created on or after 2026-11-03, and you can pin it with `UV_VERSION`.
- The recommended build command is `uv sync`. Render does not detect uv from `pyproject.toml` alone.
- Python version: set it with a `.python-version` file (e.g. `3.12`; Render uses the latest patch) or with `PYTHON_VERSION`, which must be fully qualified. The default is **3.14.10**.

**Port and host.** Bind to `0.0.0.0:$PORT` (default 10000). Render terminates TLS and forwards plain HTTP. `RENDER_EXTERNAL_HOSTNAME` and `RENDER_EXTERNAL_URL` are set automatically.

**Health check.** `healthCheckPath` sends a GET with the service's `onrender.com` host (or a verified custom domain). Any 2xx or 3xx counts as healthy. Render uses it to gate deploys and to restart unhealthy instances.

**Persistent disk.**
- Paid instances only, $0.25/GB/month, with daily snapshots kept 7 days.
- Only data under `mountPath` persists. You can't mount at `/opt/render/project/src` itself, but you can mount at `/var/data`.
- The disk can't be used at build time or in `preDeployCommand`, which is paid-only anyway, so run migrations at startup.
- A disk **disables zero-downtime deploys**: there are a few seconds of downtime per deploy. SSE clients reconnect.
- A service with a disk can't scale beyond 1 instance.

**Plans.**
- Plan IDs were renamed in Aug 2026, and legacy names still work in Blueprints. `starter` = `0.5c-512mb` = $7/mo for 0.5 CPU / 512 MB. `free` = 0.1 CPU / 512 MB.
- Compute is billed to the second.

**Free tier.**
- Spins down after **15 min without inbound traffic** and takes **about 1 minute** to wake. Browsers see a loading page while it wakes.
- The filesystem is wiped on spin-down, restart or redeploy.
- 750 instance-hours per month.
- No disk, shell or one-off jobs.

**render.yaml** (field names from the Blueprint spec; not deployed yet):

```yaml
services:
  - type: web
    name: awning
    runtime: python
    plan: starter               # = 0.5c-512mb, $7/mo; a disk requires a paid plan
    region: oregon
    buildCommand: uv sync --frozen --no-dev
    startCommand: uv run --no-sync uvicorn awning.main:app --host 0.0.0.0 --port $PORT --forwarded-allow-ips='*'
    healthCheckPath: /healthz
    autoDeployTrigger: commit
    envVars:
      - key: DATABASE_PATH
        value: /var/data/awning.db
      - key: ANTHROPIC_API_KEY
        sync: false             # prompted in the Dashboard at Blueprint creation
      - key: PAYPAL_CLIENT_ID
        sync: false
      - key: PAYPAL_CLIENT_SECRET
        sync: false
      - key: PAYPAL_WEBHOOK_ID
        sync: false
      - key: ADMIN_TOKEN_SECRET
        generateValue: true
    disk:
      name: awning-data
      mountPath: /var/data
      sizeGB: 1                 # default 10; can grow, never shrink
```

Plus a `.python-version` file containing `3.12`.

If you insist on starting on Free, drop the `disk:` block and set `plan: free`. Then point `DATABASE_PATH` at an ephemeral path and seed the demo shops on startup.

Sources:
- https://render.com/docs/python-version
- https://render.com/docs/uv-version
- https://render.com/docs/troubleshooting-python-deploys
- https://render.com/docs/deploy-fastapi
- https://render.com/docs/blueprint-spec
- https://render.com/docs/disks
- https://render.com/docs/free
- https://render.com/docs/compute-plans
- https://render.com/pricing
- https://render.com/docs/web-services
- https://render.com/docs/environment-variables
- https://render.com/docs/health-checks

## 4. AG Grid Community

**License and versions.**
- **MIT** (`ag-grid-community` on npm; also stated on the community-vs-enterprise page).
- The current major is **36**. The latest release, 36.3.0 (2026-11-04), is too new, so pin **36.2.0** (2026-09-16).

**CDN.** `<script src="https://cdn.jsdelivr.net/npm/ag-grid-community@36.2.0/dist/ag-grid-community.min.js"></script>`. The cdnjs mirror `https://cdnjs.cloudflare.com/ajax/libs/ag-grid/36.2.0/ag-grid-community.min.js` also returns 200. The UMD bundle includes the JS and the default Quartz theme CSS, so no stylesheet is needed. The container needs an explicit height.

**Verified in headless Chromium** (390 px mobile viewport, touch enabled, no console warnings):
- `editable` cells with `singleClickEdit` (a tap opens the editor)
- the text, `agNumberCellEditor`, `agSelectCellEditor` and `agLargeTextCellEditor` editors
- `cellClassRules` highlighting low-confidence cells, with the highlight cleared in `onCellValueChanged` plus `refreshCells`
- `rowSelection: {mode: "multiRow"}` (adds a checkbox column)
- `applyTransaction({add})` and `applyTransaction({remove: getSelectedRows()})`
- reading data back with `forEachNode`

**Enterprise-only, so avoid:**
- Rich Select and Formula cell editors
- cell/range selection, fill handle, Excel-like clipboard
- Excel export
- row grouping and the set filter

Everything we need is Community.

**Touch.** Taps become clicks. Double-tap zooms on mobile, so use `singleClickEdit: true`. Header drag, resize and sort work with touch. At phone width, flex columns get cramped, so give columns a `minWidth` and let the grid scroll horizontally.

```js
const isLow = (p) => (p.data.low_confidence || []).includes(p.colDef.field);
const api = agGrid.createGrid(document.getElementById("grid"), {
  columnDefs: [
    { field: "name", editable: true, cellClassRules: { "cell-low-confidence": isLow } },
    { field: "price", editable: true, cellEditor: "agNumberCellEditor", cellEditorParams: { min: 0, precision: 2 },
      cellClassRules: { "cell-low-confidence": isLow } },
  ],
  rowData, getRowId: (p) => p.data.id, rowSelection: { mode: "multiRow" },
  singleClickEdit: true, stopEditingWhenCellsLoseFocus: true,
  onCellValueChanged: (e) => {
    e.data.low_confidence = e.data.low_confidence.filter((f) => f !== e.colDef.field);  // "confirmed by edit"
    e.api.refreshCells({ rowNodes: [e.node], columns: [e.colDef.field], force: true });
  },
});
api.applyTransaction({ add: [{ id: crypto.randomUUID(), name: "", price: null, low_confidence: [] }] });
api.applyTransaction({ remove: api.getSelectedRows() });
const rows = []; api.forEachNode((n) => rows.push(n.data));   // POST rows to the server on publish
```

Confirming a low-confidence cell *without* changing it (§3.1 step 3) needs an explicit "confirm" action, such as a button column or a header action. Editing a cell to the same value does not fire `cellValueChanged` (verified).

Sources:
- https://www.npmjs.com/package/ag-grid-community
- https://www.ag-grid.com/javascript-data-grid/community-vs-enterprise/
- https://www.ag-grid.com/javascript-data-grid/getting-started/
- https://www.ag-grid.com/javascript-data-grid/provided-cell-editors/
- https://www.ag-grid.com/javascript-data-grid/touch/

## 5. SSE in FastAPI

**Recommendation: use the built-in `fastapi.sse`** (FastAPI ≥0.135.0). It provides:
- `EventSourceResponse` plus `ServerSentEvent(data|raw_data, event, id, retry, comment)`
- automatic JSON encoding of `data`
- a `: ping` keep-alive every 15 s when idle (verified)
- `Cache-Control: no-cache` and `X-Accel-Buffering: no` headers (verified)
- generator cancellation on client disconnect, so `finally` runs (verified: the subscriber count dropped after curl exited)

`sse-starlette` (3.7.0) still works and is already a transitive dependency of `mcp`, but it adds nothing here.

**Per-shop fan-out.** This in-process pattern is fine for one Render instance. A disk forces a single instance anyway. Verified: 2 subscribers on shop `a` and 1 on `b`; publishing to `a` reached only the `a` streams.

```python
import asyncio
from collections import defaultdict
from collections.abc import AsyncIterable

from fastapi import FastAPI
from fastapi.sse import EventSourceResponse, ServerSentEvent

app = FastAPI()
subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)


def publish(shop_slug: str, event: str, data: dict) -> None:   # call after DB commit (same event loop)
    for queue in list(subscribers[shop_slug]):
        queue.put_nowait(ServerSentEvent(event=event, data=data))


@app.get("/shops/{shop_slug}/events", response_class=EventSourceResponse)
async def shop_events(shop_slug: str) -> AsyncIterable[ServerSentEvent]:
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    subscribers[shop_slug].add(queue)
    try:
        yield ServerSentEvent(comment="connected", retry=3000)
        while True:
            yield await queue.get()
    finally:
        subscribers[shop_slug].discard(queue)
```

**Notes.**
- Guard the board's stream with the admin token (query param, since `EventSource` can't set headers).
- On reconnect the client should re-fetch the order list. Don't build replay on top of `Last-Event-ID`.
- `publish` must run on the event loop. From a sync `def` endpoint (threadpool), use `anyio.from_thread.run_sync`, or make the endpoint `async`.
- Don't add `GZipMiddleware` in front of the stream.

Sources:
- https://fastapi.tiangolo.com/tutorial/server-sent-events/
- https://pypi.org/project/sse-starlette/

## Unverified, to check in the first deploy spike

- Claude.ai → Awning on Render, end to end, including whether the approval URL appears as a clickable link. Also which protocol version Claude.ai negotiates; the server handles all four.
- Claude.ai behaviour against a cold free instance (initialize timeout undocumented).
- SSE through Render's proxy without buffering. There is no official doc, and community threads only show buffering via Cloudflare and static-site rewrites. Test with `curl -N https://<host>/shops/x/events`.
- Whether `uv run` / `.venv` is present at Render runtime. The docs only cover `uv sync` for builds. Fallback start command: `.venv/bin/uvicorn ...`.
- ChatGPT plan matrix (the help center page could not be fetched).
