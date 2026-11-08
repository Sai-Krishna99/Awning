import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from anthropic.types.beta import BetaTextBlock, BetaThinkingBlock, BetaToolUseBlock
from fastapi.testclient import TestClient

from awning.agent import MAX_HISTORY_MESSAGES, SYSTEM_PROMPT, AgentSession, DemoAgent, SessionStore
from awning.orders import OrderService
from awning.ratelimit import RateLimiter
from awning.seed import DEFAULT_SEED_PATH, seed_demo_shop
from awning.web.app import create_app
from fakes import FakePayPal

REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


class ScriptedMessages:
    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def reply(stop_reason: str, *blocks) -> SimpleNamespace:
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks))


def thinking() -> BetaThinkingBlock:
    return BetaThinkingBlock(type="thinking", thinking="", signature="sig-abc")


def text(value: str) -> BetaTextBlock:
    return BetaTextBlock(type="text", text=value)


def tool_use(name: str, args: dict, tool_id: str = "toolu_1") -> BetaToolUseBlock:
    return BetaToolUseBlock(type="tool_use", id=tool_id, name=name, input=args)


@pytest.fixture
def paypal(ctx) -> FakePayPal:
    fake = FakePayPal()
    ctx.paypal = fake
    ctx.orders = OrderService(ctx.db, ctx.catalog, fake, ctx.events, ctx.settings.public_url, False)
    return fake


@pytest.fixture
def shop(ctx, paypal):
    return seed_demo_shop(ctx.catalog, DEFAULT_SEED_PATH)


def scripted_agent(ctx, script: list, model: str = "claude-opus-5-5") -> tuple[DemoAgent, ScriptedMessages]:
    messages = ScriptedMessages(script)
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return DemoAgent(client, model, "low", ctx), messages


async def run(agent: DemoAgent, session: AgentSession, user_text: str) -> list[dict]:
    return [event async for event in agent.run_turn(session, user_text)]


def new_session() -> AgentSession:
    return SessionStore().create()


async def test_tool_round_trip_events(ctx, shop) -> None:
    agent, messages = scripted_agent(ctx, [
        reply("tool_use", thinking(), tool_use("get_menu", {"shop_slug": shop.slug})),
        reply("end_turn", text("A latte is $4.50.")),
    ])
    events = await run(agent, new_session(), "What's on the menu at Maria's Cart?")

    assert [e["type"] for e in events] == ["tool_call", "tool_result", "text", "done"]
    assert events[0] == {"type": "tool_call", "name": "get_menu", "args": {"shop_slug": shop.slug}}
    assert events[1]["name"] == "get_menu"
    assert events[1]["result"]["shop"]["name"] == "Maria's Cart"
    assert events[2] == {"type": "text", "text": "A latte is $4.50."}
    tool_result = messages.calls[1]["messages"][2]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "toolu_1"
    assert json.loads(tool_result["content"]) == events[1]["result"]


async def test_request_shape(ctx, shop) -> None:
    agent, messages = scripted_agent(ctx, [reply("end_turn", text("Hi!"))])
    await run(agent, new_session(), "hello")
    call = messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["system"] == SYSTEM_PROMPT
    assert call["output_config"] == {"effort": "low"}
    assert call["betas"] == ["server-side-fallback-2026-06-01"]
    assert call["fallbacks"] == [{"model": "claude-opus-4-8"}]
    assert [t["name"] for t in call["tools"]] == ["find_shops", "get_menu", "create_order", "get_order_status"]
    assert "thinking" not in call and "tool_choice" not in call
    assert call["messages"] == [{"role": "user", "content": "hello"}]


async def test_no_fallbacks_for_other_models(ctx) -> None:
    agent, messages = scripted_agent(ctx, [reply("end_turn", text("Hi!"))], model="claude-sonnet-5-5")
    await run(agent, new_session(), "hello")
    assert "fallbacks" not in messages.calls[0] and "betas" not in messages.calls[0]


async def test_history_is_append_only_with_blocks_unchanged(ctx, shop) -> None:
    first = reply("tool_use", thinking(), text("Checking."), tool_use("find_shops", {"query": "latte"}))
    final = reply("end_turn", thinking(), text("Maria's Cart has lattes."))
    agent, messages = scripted_agent(ctx, [first, final, reply("end_turn", text("You're welcome."))])
    session = new_session()

    events = await run(agent, session, "Where can I get a latte?")
    assert [e["type"] for e in events] == ["text", "tool_call", "tool_result", "text", "done"]
    assert [m["role"] for m in session.messages] == ["user", "assistant", "user", "assistant"]
    assert session.messages[1]["content"] is first.content
    assert session.messages[3]["content"] is final.content
    assert session.messages[1]["content"][0].signature == "sig-abc"

    snapshot = list(session.messages)
    await run(agent, session, "Thanks")
    assert messages.calls[2]["messages"][:4] == snapshot
    assert messages.calls[2]["messages"][4] == {"role": "user", "content": "Thanks"}


async def test_create_order_returns_approval_url_with_demo_agent_channel(ctx, shop, paypal) -> None:
    latte = next(i for i in shop.items if i.name == "Latte")
    large, oat = latte.options[0], latte.options[1]
    args = {
        "shop_slug": shop.slug,
        "items": [{"item_id": latte.id, "option_ids": [large.id, oat.id], "quantity": 1}],
        "pickup_time": "9am",
        "customer_name": "Sai",
    }
    agent, _ = scripted_agent(ctx, [
        reply("tool_use", tool_use("create_order", args)),
        reply("end_turn", text("Here is your PayPal link.")),
    ])
    events = await run(agent, new_session(), "Order a large oat latte for 9am, name Sai")
    result = events[1]["result"]
    assert result["total"] == "6.25"
    assert result["approval_url"].startswith("https://www.sandbox.paypal.com/checkoutnow?token=")
    assert ctx.orders.get(result["order_id"]).channel == "demo_agent"


async def test_tool_error_is_marked(ctx, shop) -> None:
    agent, messages = scripted_agent(ctx, [
        reply("tool_use", tool_use("get_menu", {"shop_slug": "nope"})),
        reply("end_turn", text("I couldn't find that shop.")),
    ])
    events = await run(agent, new_session(), "menu for nope")
    assert events[1]["result"]["error"]["code"] == "shop_not_found"
    assert messages.calls[1]["messages"][2]["content"][0]["is_error"] is True


async def test_tool_round_cap(ctx, shop) -> None:
    script = [reply("tool_use", tool_use("find_shops", {"query": "coffee"}, f"toolu_{i}")) for i in range(12)]
    agent, messages = scripted_agent(ctx, script)
    events = await run(agent, new_session(), "loop forever")
    assert len(messages.calls) == 8
    assert [e["type"] for e in events] == ["tool_call", "tool_result"] * 8 + ["error", "done"]
    assert events[-2]["message"]


@pytest.mark.parametrize("error", [
    anthropic.APIConnectionError(request=REQUEST),
    anthropic.RateLimitError("rate limited", response=httpx.Response(429, request=REQUEST), body=None),
])
async def test_api_error_yields_error_event(ctx, error) -> None:
    agent, _ = scripted_agent(ctx, [error])
    events = await run(agent, new_session(), "hello")
    assert [e["type"] for e in events] == ["error", "done"]
    assert events[0]["message"]


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
async def test_refusal_or_max_tokens_yields_error_event(ctx, stop_reason) -> None:
    agent, _ = scripted_agent(ctx, [reply(stop_reason, text("partial"))])
    session = new_session()
    events = await run(agent, session, "hello")
    assert [e["type"] for e in events] == ["error", "done"]
    assert session.messages == [{"role": "user", "content": "hello"}]


async def test_history_cap_refuses_new_turns(ctx) -> None:
    agent, messages = scripted_agent(ctx, [reply("end_turn", text("unused"))])
    session = new_session()
    session.messages.extend({"role": "user", "content": str(i)} for i in range(MAX_HISTORY_MESSAGES))
    events = await run(agent, session, "one more")
    assert [e["type"] for e in events] == ["error", "done"]
    assert "new chat" in events[0]["message"].lower()
    assert messages.calls == [] and len(session.messages) == MAX_HISTORY_MESSAGES


def test_session_store_expiry_and_cap() -> None:
    now = [0.0]
    store = SessionStore(ttl_s=60, max_sessions=2, clock=lambda: now[0])
    first = store.create()
    assert store.get(first.id) is first and first.messages == []
    second, third = store.create(), store.create()
    assert store.get(first.id) is None and store.get(second.id) is second
    now[0] = 61
    assert store.get(third.id) is None
    assert store.get("missing") is None


def parse_sse(body: str) -> list[dict]:
    events = []
    for chunk in body.split("\n\n"):
        data = [line[len("data: "):] for line in chunk.split("\n") if line.startswith("data: ")]
        if data:
            events.append(json.loads("\n".join(data)))
    return events


@pytest.fixture
def web(ctx) -> TestClient:
    return TestClient(create_app(ctx))


def test_try_page(web) -> None:
    response = web.get("/try")
    assert response.status_code == 200
    assert "What can I get at Maria&#39;s Cart?" in response.text
    assert "/static/agent.js" in response.text
    assert "test-key" not in response.text


def test_session_and_message_stream(ctx, web, shop) -> None:
    ctx.agent, _ = scripted_agent(ctx, [
        reply("tool_use", tool_use("get_menu", {"shop_slug": shop.slug})),
        reply("end_turn", text("Lattes are $4.50.")),
    ])
    created = web.post("/api/agent/sessions")
    assert created.status_code == 200
    session_id = created.json()["session_id"]
    response = web.post(f"/api/agent/sessions/{session_id}/messages", json={"text": "menu?"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    assert [e["type"] for e in events] == ["tool_call", "tool_result", "text", "done"]


def test_unknown_session_and_empty_text(web) -> None:
    missing = web.post("/api/agent/sessions/nope/messages", json={"text": "hi"})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "session_not_found"
    session_id = web.post("/api/agent/sessions").json()["session_id"]
    assert web.post(f"/api/agent/sessions/{session_id}/messages", json={"text": "  "}).status_code == 422


def test_rate_limited_returns_429(ctx, web) -> None:
    assert (ctx.agent_limiter_ip.max_events, ctx.agent_limiter_ip.window_s) == (30, 3600)
    assert (ctx.agent_limiter_global.max_events, ctx.agent_limiter_global.window_s) == (1000, 86400)
    ctx.agent, messages = scripted_agent(ctx, [reply("end_turn", text("Hi!"))])
    ctx.agent_limiter_ip = RateLimiter(1, 3600)
    session_id = web.post("/api/agent/sessions").json()["session_id"]
    url = f"/api/agent/sessions/{session_id}/messages"
    assert web.post(url, json={"text": "hi"}).status_code == 200
    limited = web.post(url, json={"text": "hi again"})
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert len(messages.calls) == 1

    ctx.agent_limiter_ip = RateLimiter(30, 3600)
    ctx.agent_limiter_global = RateLimiter(0, 86400)
    assert web.post(url, json={"text": "hi"}).status_code == 429
