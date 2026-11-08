# Awning: demo video script

Target length: 2:45, hard limit under 3:00. Voiceover is recorded after the take, in English, no music. Numbers marked TODO(deploy) come from the final eval and must be filled in from [eval/results.md](../eval/results.md) before recording; say nothing that is not on screen or in the repo.

## Shot list and voiceover

### 0:00-0:15 Problem (title card, then Maria's chalkboard photo)

Shots: plain title card "Awning" (text only, no third-party logos). Cut to a photo of the Maria's Cart chalkboard. On-screen text: "About 1 in 6 small businesses still has no website (Clutch, Aug 2025)".

Voiceover:
1. "AI assistants are starting to shop for us. But about one in six small businesses still has no website, according to Clutch in August 2025."
2. "A food truck with a chalkboard is invisible to every agent. Awning fixes that from a photo."

### 0:15-1:15 One continuous take

Screen recording of the hosted app plus a phone for the merchant board. The take creates a NEW fictional shop, "Harbor Lane Sandwiches", from a chalkboard written with the menu in [eval/cases/chalkboard-sandwich-shop](../eval/cases/chalkboard-sandwich-shop/menu.txt). TODO(deploy): record/photograph the demo chalkboard. Maria's Cart stays published as the always-available demo for judges and is not the shop in this take. Waits are sped up and labeled on screen ("sped up 3x"); nothing is cut that hides a step.

| Time | Shot | Voiceover |
|---|---|---|
| 0:15-0:25 | `/start`: upload the Harbor Lane Sandwiches chalkboard photo. | "I photograph the menu and upload it." |
| 0:25-0:40 | Extraction runs (label "sped up 3x" if needed). Review grid opens in AG Grid; low-confidence cells highlighted. | "Claude reads it. Anything it is unsure about is highlighted, and I cannot publish until I confirm it." |
| 0:40-0:50 | Fix one cell, confirm. Enter pickup details; name the shop Harbor Lane Sandwiches; leave payee blank (Awning demo account). Publish. | "Prices stay in my control. I publish." |
| 0:50-0:55 | Published page: storefront URL, QR code, MCP URL, admin link. Open the board on the phone. | "I get a storefront, a QR code, an endpoint for AI agents, and a live order board on my phone." |
| 0:55-1:10 | Claude.ai with the Awning connector: "Order 2 grilled cheese sandwiches from Harbor Lane Sandwiches for 9am pickup, name Sam." Tool calls visible. Claude returns the PayPal link. | "A buyer asks their own assistant. It finds the shop, reads the menu, and builds the order. It only sends item IDs and quantities. The server sets the prices." |
| 1:10-1:15 | Open the link, log in with the sandbox buyer, approve in PayPal. | "The buyer approves in PayPal." |
| 1:15 | Return page shows paid; phone board pings with the new order. (Keep both in frame.) | "Paid. The merchant's board pings." |

Rehearse so the take lands at 1:15; if it runs long, trim speech, not steps, and label sped-up waits.

### 1:15-2:30 How it works

Shots: architecture diagram from [docs/architecture.md](architecture.md) rendered full screen (no third-party logos; plain boxes). Then short cuts of code or UI as named.

| Time | Shot | Voiceover |
|---|---|---|
| 1:15-1:35 | Diagram. | "Every channel goes through one tool layer: the Claude.ai connector over MCP, the built-in agent, and the human storefront. Orders are real PayPal Orders v2 transactions with the shop's own account as payee." |
| 1:35-1:55 | Guardrails slide (text list from README): price integrity, payment state is PayPal's, merchant text is data, confidence gating. | "Guardrails are part of the design. Agents never set prices. No tool can mark an order paid; only a PayPal capture can. Menu text is treated as data, and we test with an adversarial menu." |
| 1:55-2:10 | PayPal facts slide: third-party payee verified in sandbox; refunds for other merchants' captures return 403; webhooks arrive late so we capture on return. | "In the sandbox we verified that one app can pay many merchants. We also found refunds for other merchants are blocked, and webhooks run about two minutes late, so we capture on return and use webhooks as backup." |
| 2:10-2:25 | Eval table from [eval/results.md](../eval/results.md). | "We measured extraction across Haiku, Sonnet and Opus: TODO(deploy): recall, price accuracy and chosen model, stated as synthetic-only unless real photos have been added. One extraction costs TODO(deploy): measured cost of the chosen model." |
| 2:25-2:30 | Text: "Orders from a buyer's own assistant cost Awning $0 in AI." | "When the buyer's own assistant places the order, Awning pays nothing for the AI." |

### 2:30-2:45 Close

Shot: hosted URL and the sandbox buyer login on a plain card (text only).

Voiceover: "Maria's Cart is a fictional demo merchant; every payment is a real PayPal sandbox transaction. Try it at https://awning-o5t9.onrender.com."

## Rules checklist

- [ ] Under 3:00 (target 2:45)
- [ ] Public on YouTube
- [ ] English voiceover (or subtitles)
- [ ] No music and no copyrighted material
- [ ] No third-party logos on title cards or thumbnail (product footage only)
- [ ] Everything shown matches real behavior; no mocked screens, no staged payments
- [ ] Sped-up waits labeled on screen; no cuts that hide a step
- [ ] Stat is exactly "About 1 in 6 small businesses still has no website (Clutch, Aug 2025)"; no other statistics
- [ ] Eval numbers match [eval/results.md](../eval/results.md); synthetic-only is stated if that is still true
- [ ] "Maria's Cart is a fictional demo merchant" stated
- [ ] No secrets, tokens or the .env visible; blur the admin link before uploading
- [ ] Try-it URL on the closing card

Date and checklist items marked TODO(deploy) cannot be completed until after the Render deploy and the live end-to-end check.
