"""Score MenuExtractor per model over eval/cases. Usage:
env -u ANTHROPIC_BASE_URL uv run python -m eval.run_eval --models claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5
"""
import argparse
import asyncio
import json
import statistics
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from anthropic import APIError, AsyncAnthropic
from pydantic import ValidationError

from awning.config import load_settings
from awning.extraction import ExtractionFailed, ExtractionResult, MenuExtractor
from awning.images import ImageRejected, PreparedImage, prepare_image
from eval.score import CaseScore, ExpectedItem, score_case

# USD per million tokens (input, output)
PRICES_PER_MTOK = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
}
REPRODUCE = (
    "env -u ANTHROPIC_BASE_URL uv run python -m eval.run_eval "
    "--models claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5 --cases eval/cases"
)


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    input_price, output_price = PRICES_PER_MTOK[model]
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


def find_image(case_dir: Path) -> Path | None:
    return next(iter(sorted(case_dir.glob("image.*"))), None)


def load_expected(case_dir: Path) -> tuple[list[ExpectedItem], bool]:
    data = json.loads((case_dir / "expected.json").read_text())
    items = [ExpectedItem(i["name"], i["price_cents"], i["options"]) for i in data["items"]]
    return items, bool(data.get("adversarial", False))


def describe_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:200]}".replace("|", "/").replace("\n", " ")


async def evaluate_case(extractor: MenuExtractor, case_dir: Path, image: PreparedImage | str) -> dict:
    row: dict = {"case": case_dir.name}
    if isinstance(image, str):
        row["error"] = image
        return row
    expected, adversarial = load_expected(case_dir)
    try:
        result: ExtractionResult = await extractor.extract(image)
    except (ExtractionFailed, APIError, ValidationError) as exc:
        row["error"] = describe_error(exc)
        return row
    row.update(
        score=asdict(score_case(expected, result.menu, adversarial)), latency_ms=result.latency_ms,
        input_tokens=result.input_tokens, output_tokens=result.output_tokens,
        cost_usd=cost_usd(extractor.model, result.input_tokens, result.output_tokens),
        menu=result.menu.model_dump(),
    )
    return row


ZERO_SCORE = {"item_recall": 0.0, "item_precision": 0.0, "price_accuracy": 0.0, "option_recall": 0.0}


def pct(values: list[float]) -> str:
    return f"{statistics.mean(values):.0%}" if values else "-"


def summarize(model: str, rows: list[dict]) -> str:
    ok = [r for r in rows if "score" in r]
    scores = [r.get("score", ZERO_SCORE) for r in rows]
    adversarial = [r["score"] for r in ok if r["score"]["injection_obeyed"] is not None]
    obeyed = f"{sum(s['injection_obeyed'] for s in adversarial)}/{len(adversarial)}" if adversarial else "-"
    flagged = f"{sum(s['injection_flagged'] for s in adversarial)}/{len(adversarial)}" if adversarial else "-"
    p50 = f"{statistics.median(r['latency_ms'] for r in ok) / 1000:.1f}s" if ok else "-"
    mean_cost = f"${statistics.mean(r['cost_usd'] for r in ok):.4f}" if ok else "-"
    return (
        f"| {model} | {len(ok)}/{len(rows)} | {pct([s['item_recall'] for s in scores])} "
        f"| {pct([s['item_precision'] for s in scores])} | {pct([s['price_accuracy'] for s in scores])} "
        f"| {pct([s['option_recall'] for s in scores])} | {obeyed} | {flagged} | {p50} | {mean_cost} |"
    )


def render_results(models: list[str], rows: dict[str, list[dict]], waiting: list[str], effort: str, stamp: str) -> str:
    lines = [
        "# Extraction eval results", "",
        f"Run `{stamp}`, effort `{effort}`. Raw outputs: `eval/runs/{stamp}/`.", "",
        "## Per model", "",
        "| model | cases ok | item recall | item precision | price accuracy | option recall "
        "| injection obeyed | injection flagged | p50 latency | mean cost / extraction |",
        "|---|---|---|---|---|---|---|---|---|---|",
        *(summarize(m, rows[m]) for m in models), "",
        "Accuracy columns are means over all cases; an errored case counts as 0% (so failures are not hidden). "
        "Latency and cost are over cases ok only. Injection columns count successful adversarial cases only "
        "(obeyed = every price came back 0 or null).", "",
        "## Per case", "",
        "| model | case | recall | precision | price acc | option recall | latency | cost | status |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for model in models:
        for row in rows[model]:
            if "score" in row:
                s = row["score"]
                lines.append(
                    f"| {model} | {row['case']} | {s['item_recall']:.0%} | {s['item_precision']:.0%} "
                    f"| {s['price_accuracy']:.0%} | {s['option_recall']:.0%} "
                    f"| {row['latency_ms'] / 1000:.1f}s | ${row['cost_usd']:.4f} | ok |"
                )
            else:
                lines.append(f"| {model} | {row['case']} | - | - | - | - | - | - | error: {row['error']} |")
    if waiting:
        lines += ["", "## Awaiting photo", "", *(f"- {name}" for name in waiting)]
    lines += ["", "## How to reproduce", "", f"`{REPRODUCE}`", ""]
    return "\n".join(lines)


async def run(models: list[str], cases_dir: Path) -> None:
    settings = load_settings()
    client = AsyncAnthropic(api_key=settings.anthropic_api_key, base_url=settings.anthropic_base_url)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    runs_dir = Path("eval/runs") / stamp
    runs_dir.mkdir(parents=True)
    waiting: list[str] = []
    cases = []
    for case_dir in sorted(p for p in cases_dir.iterdir() if p.is_dir()):
        image_path = find_image(case_dir)
        if image_path is None:
            waiting.append(case_dir.name)
            continue
        try:
            cases.append((case_dir, prepare_image(image_path.read_bytes())))
        except ImageRejected as exc:
            cases.append((case_dir, f"ImageRejected: {exc.reason}"))
    rows: dict[str, list[dict]] = {m: [] for m in models}
    for model in models:
        extractor = MenuExtractor(client, model, settings.extraction_effort)
        for case_dir, image in cases:
            row = await evaluate_case(extractor, case_dir, image)
            rows[model].append(row)
            (runs_dir / f"{model}__{case_dir.name}.json").write_text(json.dumps(row, indent=2) + "\n")
            print(model, case_dir.name, row.get("error", "ok"), flush=True)
    Path("eval/results.md").write_text(
        render_results(models, rows, waiting, settings.extraction_effort, stamp)
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default=",".join(PRICES_PER_MTOK))
    parser.add_argument("--cases", default="eval/cases")
    args = parser.parse_args()
    asyncio.run(run(args.models.split(","), Path(args.cases)))


if __name__ == "__main__":
    main()
