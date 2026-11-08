# Devpost submission draft

Deadline: Thu 2026-11-12, 12:00 PM PT. Submit well before. Items marked TODO(deploy) need values that exist only after the deploy.

## Text description (draft)

### Inspiration

AI assistants are starting to shop for people, and big platforms are making their merchants reachable by agents. PayPal's own agentic commerce tooling serves merchants who already have a catalog. About 1 in 6 small businesses still has no website ([Clutch, Aug 2025](https://clutch.co/press-releases/smb-websites-2025)), and a food truck or market stall with a chalkboard is invisible to agents. PayPal built the on-ramp to agentic commerce for merchants with a catalog. Awning is the on-ramp for merchants with a chalkboard.

### What it does

**Shopper.** Adds the Awning connector to their AI once. No account, no login. Their AI finds the shop, picks the items and hands back a PayPal link, and they approve in PayPal like any other payment. Nothing to install per shop.

**Merchant.** Takes a photo of the menu, checks what the AI read, connects their PayPal account and publishes. No website, no developer. Orders show up on their board with a sound, and the money lands in their PayPal account.

**PayPal.** Awning is built to be a PayPal service. Its cost per merchant is one AI call to read the menu (about 3 cents on our largest model, on our test menus; real-photo numbers pending). The shopping conversation runs on the shopper's own AI. PayPal earns its usual fee on every payment, so Awning can be free for merchants.

Under the hood: Claude vision extracts items, prices and options, flagging anything uncertain. The merchant confirms flagged cells in an AG Grid review table and publishes. They get a mobile storefront with PayPal checkout, an MCP endpoint, a QR code, a directory listing and a live order board. Buyers can order from their own assistant (for example a Claude.ai custom connector), from a built-in demo agent, or on the storefront. Maria's Cart is a fictional demo merchant; every payment is a real PayPal sandbox transaction. The AI never pays on its own: the shopper always approves in PayPal.

### How we built it

- FastAPI, SQLite, Jinja templates, AG Grid Community, deployed on Render (Starter plan with a persistent disk).
- PayPal Orders v2 with per-merchant `payee`, idempotent capture, webhooks with signature verification, refunds, and JS SDK v5 with a redirect fallback.
- Claude vision with structured outputs for extraction, an MCP server on the official Python SDK, and a tool-using demo agent on the same tool layer.
- Guardrails: server-side pricing, no payment-state tools for agents, merchant text treated as data, confidence gating, rate limits.
- Log in with PayPal for merchants: an OpenID Connect code flow whose `payer_id` becomes the shop's payee merchant ID.
- An MCP App: a small read-only order status card in Claude.ai (items, total, pickup, live status). It has no payment controls; payment always happens in PayPal.
- A live end-to-end test: real Claude orders through the hosted MCP connector, and a headless browser approves the payment in the sandbox.
- An extraction eval harness with hand-labeled cases, 323 automated tests, and PayPal sandbox spike scripts that verified third-party capture, refund behavior and webhook delivery.

### Challenges

- Paying a different merchant from one app: we verified third-party payee capture in the sandbox, then found refunds of other merchants' captures return 403, so in-app refunds work only for shops that pay the Awning demo account.
- Webhooks for third-party captures arrived about 2 minutes late, so capture on the return URL is primary and webhooks are backup.
- PayPal validates merchant IDs but not emails, so we recommend IDs and require email confirmation.
- We evaluated PayPal's agent toolkit and did not use it: no `payee`, model-supplied prices, and it exposes capture to the agent.
- Keeping agents safe as buyers: prices and totals never come from the agent.

### Accomplishments

TODO(deploy): add the real-photo eval numbers once photos exist. Accurate statements today: photo to published shop works; orders through the storefront API and the built-in demo agent created real sandbox PayPal orders and approval links; the eval's 2 synthetic cases pass for all three models (see [eval/results.md](../eval/results.md)); real-photo eval cases are still awaiting photos. On the hosted app on 2026-11-08, live sandbox payments went through end to end: storefront checkout and an order placed from Claude.ai through the custom connector, each captured once and received in the Maria's Cart sandbox merchant account. Also verified on 2026-11-08: the order status card renders in Claude.ai, Connect with PayPal returns a `payer_id` equal to the Maria's Cart merchant ID, and the automated live order test passes.

### What we learned

Agents change what a checkout must guarantee: the server, not the model, owns prices and payment state.

### What's next

- Measure extraction on real photos and finalize the model choice.
- PayPal Partner onboarding so refunds and validation work for all merchants.
- Agent payment within a buyer-set limit (vaulting), WebMCP tools on the storefront, merchant onboarding through the merchant's own assistant, menu translation for buyers.

## Judging criteria mapping

Paste or adapt the README section "How Awning meets the judging criteria" (Technological Implementation, Design, Potential Impact, Innovation, Presentation).

## Form checklist

- [ ] Project name: Awning
- [ ] Tagline: Turn a menu photo into a shop AI agents can buy from, paid through PayPal.
- [ ] Text description (above)
- [ ] Built with: Python, FastAPI, SQLite, PayPal Orders API, PayPal JS SDK, MCP, Anthropic Claude, AG Grid, Render
- [ ] Sponsor tools: AG Grid (Best Use of AG Grid), Render
- [ ] Category/prizes to select: Grand; Best Use of Agentic Commerce; Most Impactful; Best Demo Delivery; Best Use of PayPal + AI
- [ ] Video URL: TODO(deploy): public YouTube link (under 3 min)
- [x] Hosted app URL: https://awning-o5t9.onrender.com
- [x] Public repo URL: https://github.com/Sai-Krishna99/Awning (MIT license shows in About)
- [ ] Testing instructions with sandbox buyer login: TODO(deploy): paste README "Try it" section with credentials
- [x] Screenshots: [docs/screenshots/](screenshots/) (Claude.ai order card before and after payment, review grid, published page, merchant board)
- [ ] Architecture diagram image (optional): render docs/architecture.md
- [ ] Hosted demo stays up and free to use until judging ends (Dec 15, 08:00 PT)
- [ ] Submit before Thu 12:00 PM PT
