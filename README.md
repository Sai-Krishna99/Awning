# Awning

Snap your menu, and any AI assistant can order from you, paid through PayPal.

> PayPal built the on-ramp to agentic commerce for merchants with a catalog. Awning is the on-ramp for merchants with a chalkboard.

Entry for the PayPal AI Hackathon 2026.

| | |
|---|---|
| Hosted demo | https://awning-o5t9.onrender.com |
| Demo video | TODO(deploy): public YouTube URL |
| Judge sandbox buyer login | sb-sefe253794913@personal.example.com / S1Tn3*kK (see [Try it](#try-it-for-judges)) |

## The problem

About 1 in 6 small businesses still has no website ([Clutch, Aug 2025](https://clutch.co/press-releases/smb-websites-2025); [BusinessWire copy](https://www.businesswire.com/news/home/20250821848152/en)). AI assistants are starting to shop for people, and big platforms are making their merchants reachable by agents. A food truck, home baker or market stall with a chalkboard and no catalog is invisible to all of it.

Awning is for those merchants. They photograph their menu and, in minutes, have a mobile storefront with PayPal checkout, an MCP endpoint any assistant can use, and a listing in a public directory. They need no catalog, no website and no developer.

## Who it's for

Hackathon prototype on PayPal sandbox. Maria's Cart is a fictional merchant; every payment is a real sandbox transaction. The AI never pays on its own: the shopper always approves in PayPal (Claude does not allow purchases inside third-party connectors, and we think that is right).

**Shopper.** Adds the Awning connector to their AI once. No account, no login. They ask for what they want; their AI finds the shop, picks the items and hands back a PayPal link. They approve in PayPal like any other payment. Nothing to install per shop.

**Merchant.** Takes a photo of the menu, checks what the AI read, connects their PayPal account, publishes. No website, no developer, no AI account. Orders show up on their board with a sound, and the money lands in their PayPal account.

**PayPal.** Awning is built to be a PayPal service. Its cost per merchant is one AI call to read the menu (about 3 cents on our largest model for our test menus, under a tenth of a cent on the smallest; real-photo numbers pending) plus hosting. The shopping conversation runs on the shopper's own AI, so PayPal pays nothing for it. PayPal earns its usual fee on every payment, so Awning can be free for merchants. Every shop that joins makes the one connector more useful to every shopper.

**Why one shared connector.** One connector reaches every shop, so a shopper sets up once. One connector per shop would mean adding a connector for every food truck, which nobody would do. The shared directory is the part that grows in value with each shop, and it is the part PayPal would own.

## How it works

**Merchant**

1. Upload a photo of a menu, chalkboard or flyer at `/start`.
2. Claude vision extracts shop name, categories, items, prices, options and hours. Every uncertain field is flagged with a reason.
3. Review in an editable [AG Grid](https://www.ag-grid.com/) table. Low-confidence rows are highlighted and publishing is blocked until each is confirmed or edited.
4. Enter pickup details. Choose where to get paid: **Connect with PayPal** (log in with a PayPal Business account; Awning receives the account's merchant ID), or paste a PayPal merchant ID or email. Leave it blank to send payments to the Awning demo account.
5. Publish. The merchant gets a storefront URL and QR code, the MCP URL, and a private admin link to the live order board.

**Buyer, three ways**

- **Their own assistant (Claude.ai) over MCP.** Tools: `find_shops`, `get_menu`, `create_order`, `get_order_status`. The assistant sends item IDs and quantities; the server prices the order and returns a PayPal approval link.
- **Their own assistant: order card.** When Claude places an order, Claude.ai shows a small read-only status card (an MCP App) with the items, total, pickup and a live status that changes from Awaiting payment to Paid to Ready. It has no payment controls: Claude does not support purchases inside third-party connectors, so payment always happens in PayPal. Verified rendering in Claude.ai on 2026-11-08. After a deploy that changes tools, Claude.ai may need the connector's "Refresh tools list".
- **Built-in demo agent** at `/try`. Claude with the same tool layer as MCP, with its tool calls shown in the UI. For judges who do not have a connector.
- **Human storefront** at `/s/{slug}`. Mobile-first menu, cart, pickup time and PayPal buttons.

**Merchant live board.** Orders appear over server-sent events with item list, pickup time and status. Merchants mark orders ready or picked up, and refund (only shops that pay the Awning demo account, see [Limitations](#limitations)).

Details and a request walk-through: [docs/architecture.md](docs/architecture.md).

## Try it (for judges)

No setup is needed for the first two options.

1. **Shop as a human.** Open the hosted demo, choose Maria's Cart in the directory, add items, pay with PayPal.
2. **Watch an agent buy.** Open `/try` and ask for something from Maria's Cart. The agent searches, reads the menu and returns an approval link.
3. **Use your own Claude.ai.** In Claude.ai: Customize, Connectors, Add custom connector, enter `https://awning-o5t9.onrender.com/mcp`, set Authentication to "No sign-in", Add. In a chat, click **+**, Connectors, toggle Awning on. The first tool call asks for approval (Allow once or Always allow). The Free plan allows one custom connector. Then ask: "Order 2 lattes with oat milk from Maria's Cart for 9am pickup, name Sam."

Also try making your own shop: upload any menu photo at `/start`. Leaving the payee blank sends money to the Awning demo account, so in-app refunds work from that shop's board.

**Paying.** Approve payments with the sandbox buyer account: sb-sefe253794913@personal.example.com with password S1Tn3*kK. These are PayPal sandbox credentials only.

**What to expect.**

1. The assistant (or storefront) gives you a PayPal approval link.
2. Open it, log in with the sandbox buyer, approve.
3. PayPal redirects to an Awning order page showing the order as paid (the server captures on return).
4. The merchant board for that shop shows the order arrive and change status live. For Maria's Cart, TODO(deploy): public link or screenshot of its board.

If the app has been idle, give the first request a few seconds. Cost-protection rate limits apply to uploads, order creation and the demo agent and may show a "try again later" message.

## How Awning meets the judging criteria

### Technological Implementation

PayPal:

| Capability | Where |
|---|---|
| Orders v2, intent `CAPTURE`, itemized amounts, per-merchant `payee` (merchant ID or email), `experience_context` return/cancel URLs | [paypal.py](src/awning/paypal.py) |
| Idempotent capture and refund (`PayPal-Request-Id`; one lock per order; paid only when PayPal says `COMPLETED`) | [paypal.py](src/awning/paypal.py), [orders.py](src/awning/orders.py) |
| Webhooks with signature verification via PayPal's verify-webhook-signature API (`CHECKOUT.ORDER.APPROVED`, `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.REFUNDED`) | [routes_webhooks.py](src/awning/web/routes_webhooks.py) |
| Refunds from the merchant board | [orders.py](src/awning/orders.py), [routes_board.py](src/awning/web/routes_board.py) |
| JS SDK v5 buttons with `merchant-id`, redirect fallback for email-only merchants | [routes_shop.py](src/awning/web/routes_shop.py) |
| Payee merchant ID validated at publish by creating a throwaway order | [orders.py](src/awning/orders.py) |

AI:

- Claude vision with structured outputs (a JSON schema), a per-item confidence flag with the uncertain fields named, and prices bounded server-side: [extraction.py](src/awning/extraction.py).
- A remote MCP server (streamable HTTP at `/mcp`) built on the official Python SDK: [mcp_server.py](src/awning/mcp_server.py).
- A tool-using demo agent over the same tool layer, with bounded tool rounds and history: [agent.py](src/awning/agent.py), [tools.py](src/awning/tools.py).
- An extraction eval harness with hand-labeled ground truth, scoring item recall and precision, price accuracy, option recall, prompt-injection behavior, latency and cost: [eval/](eval/), results in [eval/results.md](eval/results.md).
- A demo-agent eval: scripted conversations (single-shot order, multi-turn with options, a request to change the price) run on Haiku, Sonnet and Opus with a fake PayPal, scored on the order lines, the server total, the approval link and invented prices: [eval/run_agent_eval.py](eval/run_agent_eval.py), results in [eval/agent_results.md](eval/agent_results.md).

Engineering: 274 automated tests ([tests/](tests/)) using fakes for PayPal and Anthropic; during development the app itself created real PayPal sandbox orders and approval links, through the storefront API and through the built-in demo agent (real Claude API calls: `find_shops`, `get_menu`, `create_order`). Capture to a different merchant account, third-party refund behavior and webhook delivery were verified live with spike scripts (see PayPal integration details). On the hosted app on 2026-11-08, live sandbox payments went through end to end: storefront checkout and an order placed from Claude.ai through the custom connector, each captured once and received in the Maria's Cart sandbox merchant account.

### Design

- Merchant path is one screen per step: photo, review grid, shop details, publish. Mobile-first pages with server-rendered templates ([templates](src/awning/web/templates/)).
- AI uncertainty is shown, not hidden: highlighted low-confidence cells, and publish is blocked until the merchant confirms them ([review.js](src/awning/web/static/review.js), [catalog.py](src/awning/catalog.py)).
- Agent work is visible: the demo agent shows each tool call ([agent.js](src/awning/web/static/agent.js)).
- Complete loop: upload, publish, order via three channels, pay, live board, refund (Awning demo account), directory, QR code.

### Potential Impact

About 1 in 6 small businesses still has no website ([Clutch, Aug 2025](https://clutch.co/press-releases/smb-websites-2025)). Awning gives that kind of merchant a path into agent-driven commerce from a photo alone, with money going straight to the merchant's own PayPal account. The marginal cost is low: one extraction call per merchant onboarding (measured below) and $0 platform AI cost for orders placed through a buyer's own assistant. See [docs/research/impact.md](docs/research/impact.md).

### Innovation

- The long tail is the target, not merchants who already have a catalog on a platform.
- The input is a photograph; the output is a storefront, an MCP endpoint and a directory listing.
- One shared tool layer serves an external agent, a built-in agent and the human storefront, so an agent can never do something the storefront cannot.
- Guardrails designed for agents as buyers: server-side pricing, no payment-state tools, merchant text treated as data (next section).

### Presentation

- Video script and shot list: [docs/video-script.md](docs/video-script.md). Video: TODO(deploy): public YouTube URL.
- Judge path with zero setup, and stated limitations below.
- Honest framing: what is real and what is demo ([next sections](#whats-real-vs-demo)).

## Guardrails

| Guardrail | Enforced in |
|---|---|
| **Price integrity.** Agents send item IDs, option IDs and quantities only. Prices and totals are computed server-side from the stored catalog; unknown IDs, unavailable options, duplicate options, empty orders and quantities outside 1-20 are rejected with a clear tool error. | [pricing.py](src/awning/pricing.py), [tools.py](src/awning/tools.py), [tests/test_pricing.py](tests/test_pricing.py) |
| **Payment state is PayPal's.** An order becomes `paid` only after a capture returns `COMPLETED`. No tool exposes capture or status changes; the agent toolkit's `pay_order` pattern was deliberately avoided. | [orders.py](src/awning/orders.py), [tools.py](src/awning/tools.py) |
| **Merchant text is data.** Extraction treats the photo as untrusted and records injected text in `suspicious_text`. Menu text is returned to agents with a "data, not instructions" note, and the demo agent's system prompt says the same. The eval includes an adversarial menu. | [extraction.py](src/awning/extraction.py), [tools.py](src/awning/tools.py), [agent.py](src/awning/agent.py), [eval/cases/synthetic-adversarial](eval/cases/synthetic-adversarial) |
| **Confidence gating.** Low-confidence items must be confirmed or edited before a shop can publish. | [catalog.py](src/awning/catalog.py), [routes_merchant.py](src/awning/web/routes_merchant.py) |
| **Cost guardrails.** Per-IP and global rate limits on uploads (5 per IP per hour, 100 per day) and the demo agent (30 per IP per hour, 300 per day); capped tool rounds and history; image size limits. | [context.py](src/awning/context.py), [ratelimit.py](src/awning/ratelimit.py), [images.py](src/awning/images.py) |
| **Webhook authenticity.** Webhooks are rejected unless PayPal verifies the signature. | [routes_webhooks.py](src/awning/web/routes_webhooks.py) |
| **No merchant accounts to steal.** Each shop's board is behind an unguessable admin token checked on every action. | [orders.py](src/awning/orders.py) |

## PayPal integration details

APIs used: Orders v2 (create, get, capture), Payments v2 (capture refund), Notifications (verify-webhook-signature), and the JS SDK v5 for storefront buttons.

Verified live against the PayPal sandbox ([docs/research/paypal.md](docs/research/paypal.md)):

- **Third-party payee works.** One REST app creates and captures an order whose `payee` is a different sandbox business account (a second account, "Maria's Cart"). The capture completed and the fee landed on the payee, so the money reached that merchant's account.
- **Merchant IDs are validated, emails are not.** A fake `payee.merchant_id` fails at create with `PAYEE_ACCOUNT_INVALID`; a made-up email is accepted. Awning therefore asks merchants to confirm an email explicitly and recommends the merchant ID.
- **Refunds of other merchants' captures return 403.** Our app is not their partner, so in-app refunds work only for shops that pay the Awning demo account. Other merchants refund in their own PayPal account.
- **Webhooks arrive late.** For third-party captures, `PAYMENT.CAPTURE.COMPLETED` arrived about 2 minutes after capture. Return-URL capture plus polling is therefore the primary path and webhooks are the backup.
- **Log in with PayPal for merchant onboarding.** OpenID Connect code flow in the sandbox. The `payer_id` from userinfo is used as the shop's `payee.merchant_id`. On 2026-11-08 we confirmed on the hosted app that the `payer_id` returned for the Maria's Cart business account equals its merchant ID, and that a connected draft shows the account on the review page. PayPal's Partner Referrals onboarding API returned 403 for our app (it needs partner approval), so it is the production path, not part of this demo.
- **JS SDK.** v5 with `merchant-id` when the shop has a merchant ID; email-only merchants use the same redirect approval link the agent path uses.

**Why not PayPal's agent toolkit.** We evaluated `paypal-agent-toolkit` (Python 1.11.0) and chose a thin httpx client instead. For this design it had four gaps: order creation has no `payee` field, so money cannot be routed to a merchant's account; it computes totals from model-supplied item costs rather than server-side prices; it exposes capture to the agent; and it has no Anthropic adapter and pulls in 162 packages. It is a good fit for single-merchant setups. Details are in the research note.

## AI details

- **Models.** Extraction and the demo agent use Claude models configured by environment variable (`AWNING_EXTRACTION_MODEL`, `AWNING_AGENT_MODEL`, with `*_EFFORT`). The demo agent (`/try`) uses `claude-haiku-5-5`, chosen from the agent eval in [eval/agent_results.md](eval/agent_results.md): Haiku passed 9/9 at about $0.0016 per conversation. Extraction model: TODO(deploy): model chosen from the eval table. The code default is `claude-opus-5-5`.
- **Eval.** Run `20261108T071645Z` at low effort. It currently covers two synthetic cases only (one clean, one adversarial). Six real-photo cases are listed as "awaiting photos" in [eval/results.md](eval/results.md), so these numbers are not yet evidence about messy real-world photos.

| model | cases ok | item recall | price accuracy | injection obeyed | p50 latency | mean cost / extraction |
|---|---|---|---|---|---|---|
| claude-haiku-5-5 | 2/2 | 100% | 100% | 0/1 | 2.7s | $0.0007 |
| claude-sonnet-5-5 | 2/2 | 100% | 100% | 0/1 | 4.1s | $0.0138 |
| claude-opus-5-5 | 2/2 | 100% | 100% | 0/1 | 5.5s | $0.0279 |

- **Unit cost.** One extraction per merchant onboarding. Measured on the synthetic cases: $0.0007 (Haiku 5.5), $0.0138 (Sonnet 5.5), $0.0279 (Opus 5.5). TODO(deploy): re-measure after real photos are added to the eval.
- **Agent orders cost the platform $0 in AI.** When a buyer's own assistant (for example Claude.ai) places an order, that assistant's reasoning runs on the buyer's account. Awning only serves tool calls. Only the built-in demo agent spends Awning's Anthropic credits, and it is rate limited.

## What's real vs demo

Maria's Cart is a fictional demo merchant; every payment is a real PayPal sandbox transaction.

Two sandbox accounts receive money. Shops published with no payee pay the Awning demo account, and in-app refunds work for them. Maria's Cart is a fictional shop that pays a separate sandbox merchant account, so its refunds happen in that merchant's PayPal account.

The site footer says: "Sandbox demo: every payment is a real PayPal sandbox transaction. No real money moves."

- Real: PayPal sandbox order creation and approval links produced by the app itself (storefront API and built-in demo agent, using the real Claude API); capture to a different merchant account, the 403 on third-party refunds and webhook delivery, verified with spike scripts ([docs/research/paypal.md](docs/research/paypal.md)); the Claude extraction, MCP server and SSE board code. On the hosted app on 2026-11-08, live sandbox payments went through end to end: storefront checkout and an order placed from Claude.ai through the custom connector, each captured once and received in the Maria's Cart sandbox merchant account.
- Demo: Maria's Cart and its menu ([seed/marias-cart.json](seed/marias-cart.json)), and the sandbox accounts. No real money moves and no real business or user is represented. Awning has no users beyond the hackathon.
- Not claimed: PayPal Store Sync, Agent Ready or other PayPal merchant products. Awning uses the public Orders API only.

## Limitations

- Sandbox only. There is no merchant KYC or PayPal Partner onboarding; merchants enter a merchant ID or email.
- PayPal cannot validate emails in advance. A wrong email fails at approval or capture. Merchant IDs are validated.
- In-app refunds work only for shops that pay the Awning demo account (the 403 above). Other merchants refund from their PayPal account.
- Rate limits, agent sessions and the event bus are in memory, and the app runs as a single instance on one SQLite file. Restarting resets limits and live sessions.
- Real-photo extraction accuracy has not been measured yet (see AI details).
- USD only; no shipping, tax, tips or stock tracking. Shops are managed through an unguessable admin link rather than accounts.

## Architecture

See [docs/architecture.md](docs/architecture.md) for the diagram and an agent-order walk-through.

## Run locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```
uv sync
cp .env.example .env     # fill in PayPal sandbox and Anthropic values
uv run uvicorn awning.main:app --reload
uv run pytest            # 274 tests, no network or keys needed
```

`.env.example` lists every setting. At minimum: `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_PLATFORM_MERCHANT_ID`, `ANTHROPIC_API_KEY`. `PAYPAL_WEBHOOK_ID` is needed only to accept webhooks, and `PAYPAL_DEMO_PAYEE_*` only to send Maria's Cart's payments to its separate sandbox merchant account. Run the extraction eval with `uv run python -m eval.run_eval --models claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5 --cases eval/cases` (prefix with `env -u ANTHROPIC_BASE_URL` only if that variable is set in your shell).

## Project structure

```
src/awning/
  config.py        settings from environment
  models.py        pydantic models
  catalog.py       shops, items, publish rules
  pricing.py       server-side pricing
  orders.py        order lifecycle, capture, refund
  paypal.py        PayPal REST client
  extraction.py    Claude vision extraction
  tools.py         shared tool layer (MCP and demo agent)
  mcp_server.py    MCP server at /mcp
  agent.py         built-in demo agent
  web/             routes, Jinja templates, static JS
eval/              extraction eval harness, cases, results
tests/             pytest suite
docs/              architecture, video script, submission, research
seed/              fictional demo merchant
render.yaml        Render deployment
```

## License

MIT. See [LICENSE](LICENSE).
