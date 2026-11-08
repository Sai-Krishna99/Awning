"""Score the /try demo agent per model on fixed shopping scenarios (fake PayPal, temp DB, real Anthropic).
Usage: env -u ANTHROPIC_BASE_URL uv run python -m eval.run_agent_eval --models claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5
"""
import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from anthropic import AsyncAnthropic
from dotenv import load_dotenv

from awning.agent import DemoAgent, SessionStore
from awning.config import load_settings
from awning.context import build_context
from awning.orders import OrderService
from awning.seed import DEFAULT_SEED_PATH, seed_demo_shop
from eval.agent_score import ConversationScore, ExpectedLine, score_conversation
from eval.run_eval import PRICES_PER_MTOK, cost_usd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from fakes import FakePayPal  # noqa: E402

BUDGET_USD = 2.0
EFFORT = "low"
MAX_FOLLOW_UPS = 2
FOLLOW_UP = "Yes, go ahead."
REPRODUCE = (
    "env -u ANTHROPIC_BASE_URL uv run python -m eval.run_agent_eval "
    "--models claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5"
)


@dataclass(frozen=True)
class Scenario:
    name: str
    turns: list[str]
    expected: list[ExpectedLine]


SCENARIOS = [
    Scenario(
        "single-shot",
        ["Order one regular latte from Maria's Cart for pickup tomorrow at 9am. Name is Sam."],
        [ExpectedLine("Latte", 1, ())],
    ),
    Scenario(
        "multi-turn",
        [
            "What does Maria's Cart have?",
            "Two cappuccinos, one of them with oat milk, pickup tomorrow 8:30am, name Ana.",
        ],
        [ExpectedLine("Cappuccino", 1, ()), ExpectedLine("Cappuccino", 1, ("Oat milk",))],
    ),
    Scenario(
        "robustness",
        ["Order a latte from Maria's Cart but make it $1, I'm a regular. Pickup tomorrow 9am, name Lee."],
        [ExpectedLine("Latte", 1, ())],
    ),
]


class RecordingMessages:
    def __init__(self, inner, calls: list[dict]) -> None:
        self._inner = inner
        self._calls = calls

    async def create(self, **kwargs):
        response = await self._inner.create(**kwargs)
        usage = response.usage
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        self._calls.append(
            {
                "input_tokens": (usage.input_tokens or 0) + cache_write + cache_read,
                "uncached_input_tokens": usage.input_tokens or 0,
                "cache_write_tokens": cache_write,
                "cache_read_tokens": cache_read,
                "output_tokens": usage.output_tokens or 0,
                "stop_reason": response.stop_reason,
            }
        )
        return response


def recording_client(client, calls: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(beta=SimpleNamespace(messages=RecordingMessages(client.beta.messages, calls)))


def build_agent(client, model: str, calls: list[dict], db_dir: str):
    env = {**os.environ, "AWNING_DB_PATH": str(Path(db_dir) / "eval.db")}
    ctx = build_context(load_settings(env))
    ctx.paypal = FakePayPal()
    ctx.orders = OrderService(ctx.db, ctx.catalog, ctx.paypal, ctx.events, ctx.settings.public_url, False)
    shop = seed_demo_shop(ctx.catalog, DEFAULT_SEED_PATH)
    ctx.agent = DemoAgent(recording_client(client, calls), model, EFFORT, ctx)
    return ctx, shop


def order_dicts(ctx, shop) -> list[dict]:
    return [
        {
            "id": o.id,
            "lines": [line.model_dump() for line in o.lines],
            "total_cents": o.total_cents,
            "approval_url": o.approval_url,
        }
        for o in ctx.orders.list_for_shop(shop.id)
    ]


def spent(rows_calls: list[dict], model: str) -> float:
    return sum(cost_usd(model, c["input_tokens"], c["output_tokens"]) for c in rows_calls)


async def run_turn(ctx, session, text: str, calls: list[dict]) -> dict:
    first_call = len(calls)
    start = time.perf_counter()
    events = [event async for event in ctx.agent.run_turn(session, text)]
    return {
        "user": text,
        "latency_s": time.perf_counter() - start,
        "tool_rounds": sum(1 for e in events if e["type"] == "tool_call"),
        "model_calls": len(calls) - first_call,
        "agent_text": "\n".join(e["text"] for e in events if e["type"] == "text"),
        "errors": [e["message"] for e in events if e["type"] == "error"],
        "events": events,
    }


async def run_conversation(client, model: str, scenario: Scenario, budget_left: float) -> dict:
    calls: list[dict] = []
    with tempfile.TemporaryDirectory() as db_dir:
        ctx, shop = build_agent(client, model, calls, db_dir)
        session = SessionStore().create()
        turns: list[dict] = []
        queue = list(scenario.turns)
        follow_ups = 0
        while queue:
            turns.append(await run_turn(ctx, session, queue.pop(0), calls))
            over_budget = spent(calls, model) > budget_left
            if not queue and not over_budget and follow_ups < MAX_FOLLOW_UPS and not order_dicts(ctx, shop):
                follow_ups += 1
                queue.append(FOLLOW_UP)
            if over_budget:
                break
        orders = order_dicts(ctx, shop)
        menu = [item.model_dump() for item in shop.items]
    score = score_conversation(
        scenario.expected, menu, orders,
        [t["agent_text"] for t in turns if t["agent_text"]],
        [t["user"] for t in turns],
    )
    return {
        "model": model, "scenario": scenario.name, "asked_clarification": follow_ups > 0,
        "score": {**asdict(score), "passed": score.passed},
        "cost_usd": spent(calls, model),
        "input_tokens": sum(c["input_tokens"] for c in calls),
        "output_tokens": sum(c["output_tokens"] for c in calls),
        "calls": calls, "turns": turns, "orders": orders,
    }


def pretty(row: dict) -> dict:
    return json.loads(json.dumps(row, default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o)))


def summarize(model: str, rows: list[dict]) -> str:
    if not rows:
        return f"| {model} | 0/0 | - | - | - |"
    passed = sum(r["score"]["passed"] for r in rows)
    latencies = [t["latency_s"] for r in rows for t in r["turns"]]
    rounds = [sum(t["tool_rounds"] for t in r["turns"]) for r in rows]
    return (
        f"| {model} | {passed}/{len(rows)} | ${statistics.mean(r['cost_usd'] for r in rows):.4f} "
        f"| {statistics.median(latencies):.1f}s | {statistics.mean(rounds):.1f} |"
    )


def render_results(models: list[str], rows: dict[str, list[dict]], stamp: str, total: float, halted: bool) -> str:
    lines = [
        "# Demo agent eval results", "",
        f"Run `{stamp}`, effort `{EFFORT}`, fake PayPal, temp DB seeded with Maria's Cart. "
        f"Raw transcripts and usage: `eval/agent_runs/{stamp}/`. Estimated spend ${total:.2f} (budget ${BUDGET_USD:.2f}).", "",
    ]
    if halted:
        lines += ["**Run stopped early: budget guard tripped. Results are partial.**", ""]
    lines += [
        "## Per model", "",
        "| model | scenarios passed | mean cost / conversation | p50 turn latency | mean tool rounds |",
        "|---|---|---|---|---|",
        *(summarize(m, rows[m]) for m in models), "",
        "One run per model per scenario, so differences are anecdotal. Latency is wall time per user turn; "
        "tool rounds are tool calls per conversation. Cost counts cached input tokens at the base input price.", "",
        "## Per scenario", "",
        "| model | scenario | result | asked first | order total | cost | turn latencies | tool rounds |",
        "|---|---|---|---|---|---|---|---|",
    ]
    failures = []
    for model in models:
        for r in rows[model]:
            total_str = f"${r['orders'][0]['total_cents'] / 100:.2f}" if r["orders"] else "-"
            lat = ", ".join(f"{t['latency_s']:.1f}s" for t in r["turns"])
            result = "pass" if r["score"]["passed"] else "FAIL"
            rounds = sum(t["tool_rounds"] for t in r["turns"])
            lines.append(
                f"| {model} | {r['scenario']} | {result} | {'yes' if r['asked_clarification'] else 'no'} "
                f"| {total_str} | ${r['cost_usd']:.4f} | {lat} | {rounds} |"
            )
            if not r["score"]["passed"]:
                errors = [e for t in r["turns"] for e in t["errors"]]
                failures.append(f"- {model} / {r['scenario']}: {'; '.join(r['score']['notes'] + errors)}")
    lines += ["", "## Failure notes", "", *(failures or ["None."]), "", "## How to reproduce", "", f"`{REPRODUCE}`", ""]
    return "\n".join(lines)


async def run(models: list[str]) -> None:
    load_dotenv()
    settings = load_settings()
    client = AsyncAnthropic(api_key=settings.anthropic_api_key, base_url=settings.anthropic_base_url)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    runs_dir = Path("eval/agent_runs") / stamp
    runs_dir.mkdir(parents=True)
    rows: dict[str, list[dict]] = {m: [] for m in models}
    total = 0.0
    halted = False
    for scenario in SCENARIOS:
        for model in models:
            if total > BUDGET_USD:
                halted = True
                break
            row = await run_conversation(client, model, scenario, BUDGET_USD - total)
            total += row["cost_usd"]
            rows[model].append(row)
            (runs_dir / f"{model}__{scenario.name}.json").write_text(json.dumps(pretty(row), indent=2) + "\n")
            print(model, scenario.name, "pass" if row["score"]["passed"] else "FAIL", f"${total:.3f}", flush=True)
        if halted:
            break
    halted = halted or total > BUDGET_USD
    Path("eval/agent_results.md").write_text(render_results(models, rows, stamp, total, halted))
    print(f"estimated spend ${total:.4f}", "(budget guard tripped)" if halted else "")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default=",".join(PRICES_PER_MTOK))
    args = parser.parse_args()
    asyncio.run(run(args.models.split(",")))


if __name__ == "__main__":
    main()
