"""Live end-to-end check: real Claude orders through the hosted MCP connector,
a headless browser approves the PayPal sandbox payment, and Claude confirms it.

Needs ANTHROPIC_API_KEY, PAYPAL_SANDBOX_BUYER_EMAIL and PAYPAL_SANDBOX_BUYER_PASSWORD in the repo .env.
Secrets are read from .env and never printed. Costs about a quarter of a cent per run with Haiku,
and creates one real sandbox order on Maria's Cart.

Usage:
  uv run --with playwright playwright install chromium   # once
  uv run --with playwright python scripts/live_e2e.py [--no-pay] [--model M] [--base URL]
Set CHROMIUM_PATH to use an existing Chromium binary instead.
"""

import argparse
import asyncio
import json
import os
import re
import tempfile
import sys
import time
from pathlib import Path

import anthropic
import httpx

REPO = Path(__file__).resolve().parents[1]
OUT = Path(tempfile.gettempdir()) / "awning-e2e"
BASE = "https://awning-o5t9.onrender.com"
PROMPT = (
    "Order one plain latte from Maria's Cart for pickup tomorrow at 8:00 AM under the name "
    "'E2E Test'. I confirm the order, so place it without asking me again."
)


def load_env() -> dict:
    env = {}
    for line in (REPO / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def ask(client, model: str, messages: list) -> tuple[list, float]:
    response = client.beta.messages.create(
        model=model,
        max_tokens=2048,
        betas=["mcp-client-2025-11-20"],
        mcp_servers=[{"type": "url", "url": f"{BASE}/mcp", "name": "awning"}],
        tools=[{"type": "mcp_toolset", "mcp_server_name": "awning"}],
        messages=messages,
    )
    u = response.usage
    print(f"  stop={response.stop_reason} in={u.input_tokens} out={u.output_tokens}")
    return response.content, u.input_tokens, u.output_tokens


def summarize(blocks) -> dict:
    found = {}
    for block in blocks:
        if block.type == "mcp_tool_use":
            print(f"  tool call: {block.name} {json.dumps(block.input)[:160]}")
        elif block.type == "mcp_tool_result":
            text = " ".join(getattr(c, "text", "") for c in block.content or [])
            print(f"  tool result ({'error' if block.is_error else 'ok'}): {text[:160]}")
            for key in ("order_id", "approval_url", "status"):
                m = re.search(rf'"{key}"\s*:\s*"([^"]+)"', text)
                if m:
                    found[key] = m.group(1)
        elif block.type == "text":
            print(f"  claude: {block.text.strip()[:400]}")
    return found


async def pay(url: str, email: str, password: str) -> None:
    from playwright.async_api import async_playwright

    OUT.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded")
            await page.locator("#email").fill(email, timeout=30000)
            next_btn = page.locator("#btnNext")
            if await next_btn.is_visible():
                await next_btn.click()
            await page.locator("#password").fill(password, timeout=30000)
            await page.locator("#btnLogin").click()
            submit = page.locator(
                "#payment-submit-btn, [data-testid='submit-button-initial'], button:has-text('Complete Purchase'), button:has-text('Pay')"
            ).first
            await submit.click(timeout=45000)
            await page.wait_for_url(re.compile(re.escape(BASE)), timeout=60000)
            print(f"  returned to {page.url.split('?')[0]}")
        finally:
            await page.screenshot(path=str(OUT / "pay-last.png"))
            await browser.close()


def wait_paid(order_id: str) -> str:
    status = ""
    for _ in range(30):
        status = httpx.get(f"{BASE}/api/orders/{order_id}", timeout=20).json().get("status", "")
        if status != "awaiting_payment":
            break
        time.sleep(3)
    return status


def main() -> int:
    global BASE
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-pay", action="store_true")
    parser.add_argument("--model", default="claude-haiku-5-5")
    parser.add_argument("--base", default=BASE, help="hosted app URL")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    env = load_env()
    client = anthropic.Anthropic(api_key=env["ANTHROPIC_API_KEY"])
    tokens = [0, 0]

    print(f"1. Claude ({args.model}) orders through the Awning connector")
    messages = [{"role": "user", "content": PROMPT}]
    blocks, i, o = ask(client, args.model, messages)
    tokens[0] += i
    tokens[1] += o
    found = summarize(blocks)
    if "order_id" not in found or "approval_url" not in found:
        print("FAIL: no order created")
        return 1
    order_id = found["order_id"]
    print(f"  order {order_id}")

    if args.no_pay:
        print("Skipping payment (--no-pay)")
        return 0
    email, password = env.get("PAYPAL_SANDBOX_BUYER_EMAIL"), env.get("PAYPAL_SANDBOX_BUYER_PASSWORD")
    if not (email and password):
        print("FAIL: PAYPAL_SANDBOX_BUYER_EMAIL / PAYPAL_SANDBOX_BUYER_PASSWORD missing from .env")
        return 1

    print("2. Headless browser approves the payment as the sandbox buyer")
    asyncio.run(pay(found["approval_url"], email, password))
    status = wait_paid(order_id)
    print(f"  server status: {status}")

    print("3. Claude checks the order")
    messages += [{"role": "assistant", "content": blocks}, {"role": "user", "content": "I paid in PayPal. Did it go through?"}]
    blocks, i, o = ask(client, args.model, messages)
    tokens[0] += i
    tokens[1] += o
    summarize(blocks)
    print(f"tokens in={tokens[0]} out={tokens[1]}")
    ok = status == "paid"
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
