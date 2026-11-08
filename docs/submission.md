# Devpost submission draft

Deadline: Thu 2026-11-12, 12:00 PM PT. Submit well before. Items marked TODO(deploy) need values that exist only after the deploy.

## Text description (draft)

### Inspiration

AI assistants are starting to shop for people, and big platforms are making their merchants reachable by agents. PayPal's own agentic commerce tooling serves merchants who already have a catalog. About 1 in 6 small businesses still has no website ([Clutch, Aug 2025](https://clutch.co/press-releases/smb-websites-2025)), and a food truck or market stall with a chalkboard is invisible to agents. PayPal built the on-ramp to agentic commerce for merchants with a catalog. Awning is the on-ramp for merchants with a chalkboard.

### What it does

A merchant photographs a menu. Claude vision extracts items, prices and options, flagging anything uncertain. The merchant confirms flagged cells in an AG Grid review table and publishes. They get a mobile storefront with PayPal checkout, an MCP endpoint, a QR code, a directory listing and a live order board.

Buyers can order three ways: from their own assistant (for example a Claude.ai custom connector), from a built-in demo agent, or on the storefront. Money goes to the merchant's own PayPal account. Maria's Cart is a fictional demo merchant; every payment is a real PayPal sandbox transaction.

### How we built it

- FastAPI, SQLite, Jinja templates, AG Grid Community, deployed on Render (Starter plan with a persistent disk).
- PayPal Orders v2 with per-merchant `payee`, idempotent capture, webhooks with signature verification, refunds, and JS SDK v5 with a redirect fallback.
- Claude vision with structured outputs for extraction, an MCP server on the official Python SDK, and a tool-using demo agent on the same tool layer.
- Guardrails: server-side pricing, no payment-state tools for agents, merchant text treated as data, confidence gating, rate limits.
- An extraction eval harness with hand-labeled cases, 232 unit tests, and PayPal sandbox spike scripts that verified third-party capture, refund behavior and webhook delivery.

### Challenges

- Paying a different merchant from one app: we verified third-party payee capture in the sandbox, then found refunds of other merchants' captures return 403, so in-app refunds work only for shops that pay the Awning demo account.
- Webhooks for third-party captures arrived about 2 minutes late, so capture on the return URL is primary and webhooks are backup.
- PayPal validates merchant IDs but not emails, so we recommend IDs and require email confirmation.
- We evaluated PayPal's agent toolkit and did not use it: no `payee`, model-supplied prices, and it exposes capture to the agent.
- Keeping agents safe as buyers: prices and totals never come from the agent.

### Accomplishments

TODO(deploy): fill from the final live end-to-end run and eval (for example the real-photo eval numbers, once added). Until then, accurate statements: photo to published shop works; orders through the storefront API and the built-in demo agent created real sandbox PayPal orders and approval links; the eval's 2 synthetic cases pass for all three models (see [eval/results.md](../eval/results.md)); real-photo eval cases are still awaiting photos. TODO(deploy): verify an order via the Claude.ai connector. TODO(deploy): live end-to-end payment through the hosted app.

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
- [ ] Hosted app URL: TODO(deploy): Render URL
- [ ] Public repo URL: TODO(deploy): GitHub URL; license (MIT) shows in About
- [ ] Testing instructions with sandbox buyer login: TODO(deploy): paste README "Try it" section with credentials
- [ ] Screenshots: TODO(deploy): review grid, Claude.ai order, PayPal approval, board
- [ ] Architecture diagram image (optional): render docs/architecture.md
- [ ] Hosted demo stays up and free to use until judging ends (Dec 15, 08:00 PT)
- [ ] Submit before Thu 12:00 PM PT
