# Awning: demo video script

Target 2:40, hard limit under 3:00. Narration is generated with Kokoro TTS (voice `af_heart`, open-source, runs locally). Browser scenes are recorded by Playwright against the hosted app. The only hand-recorded clip is the Claude.ai scene (Claude.ai is never recreated or mocked). No music. Captions are burned in and uploaded as SRT.

Fictional shops: Maria's Cart (seeded demo) and Rosa's Taco Stand (onboarded on camera from an AI-generated image of a handwritten menu, labeled on screen; menu text in [eval/cases/taco-stand-lined-paper/menu.txt](../eval/cases/taco-stand-lined-paper/menu.txt)). Every payment is a real PayPal sandbox transaction.

## Scenes

### 1. Hook, 0:00-0:14 (hand-recorded Claude.ai clip)

Shot: Claude.ai chat with the Awning connector. Ask "Order a latte from Maria's Cart for 9am pickup, name Sam." The order card appears. Open the PayPal link, log in as the sandbox buyer, approve. Back in Claude, the card changes to Paid. Waits are sped up and labeled.

Narration: "I ask Claude for a latte from Maria's Cart, a coffee cart with no website. Claude places the order, I approve it in PayPal, and the money goes straight to Maria."

### 2. Problem, 0:14-0:28 (title card, stat card)

On screen: "Awning" title, then "About 1 in 6 small businesses still has no website (Clutch, Aug 2025)".

Narration: "About one in six small businesses still has no website. AI assistants are starting to shop for people, and those businesses are invisible to them. Awning changes that with one photo."

### 3. Who does what, 0:28-0:52 (landing page)

Shot: landing page hero, scroll to the three cards.

Narration: "Three people are involved. The shopper adds Awning to their AI once, and can order from every shop on it. The merchant needs a photo and the PayPal account they already have. And Awning is built for PayPal to run: reading a menu costs a few cents, the shopper's own AI does the talking, and PayPal earns its usual fee on every payment, so it can be free for merchants."

### 4. Merchant onboarding, 0:52-1:40 (Playwright)

| Shot | Narration |
|---|---|
| `/start`, upload Rosa's menu image. On-screen label: "Menu image: AI-generated for this demo". | "Rosa runs a taco stand. Here's her handwritten menu. For this demo the image is AI-generated. She uploads it." |
| Extraction, then the AG Grid review with highlighted rows; confirm one. | "Claude reads the items, prices and options. Anything it isn't sure about is highlighted, and she can't publish until she checks it." |
| Connect with PayPal, sandbox login (password field only, never the value), back to review showing Connected. | "To get paid, she connects PayPal by logging in. Awning never sees her password. PayPal tells us which account to pay." |
| Publish; published page with storefront link, QR code, assistant link and board link. | "She publishes, and gets a storefront, a QR code, a link for AI assistants, and a live order board." |

### 5. A shopper orders from Rosa, 1:40-2:10 (Playwright)

| Shot | Narration |
|---|---|
| `/try`: "Two carne asada tacos from Rosa's Taco Stand for noon, name Lee." Tool calls appear one by one. | "A shopper asks an AI assistant for two tacos. It finds Rosa's stand, reads the menu and creates the order. It only picks items. Awning sets the prices." |
| PayPal sandbox approval, then the order page showing Paid, with Rosa's board updating beside it (chime added in editing; the board really plays it). | "The shopper approves in PayPal, and Rosa's board chimes with the paid order." |

### 6. Safe by design, 2:10-2:28 (landing page section)

Narration: "Agents never set prices, and no tool can mark an order paid. Only a PayPal capture can. Words on a menu are treated as data, never as instructions. And the AI never pays on its own. The shopper always approves in PayPal."

### 7. Close, 2:28-2:40 (end card)

On screen: hosted URL, "Judge sandbox login in the README", "Maria's Cart and Rosa's Taco Stand are fictional. Every payment is a real PayPal sandbox transaction. Awning is independent; PayPal and Claude are trademarks of their owners."

Narration: "Maria's Cart and Rosa's Taco Stand are made up, but every payment you saw is a real PayPal sandbox transaction. Try it at the link below."

## Rules checklist

- [ ] Under 3:00
- [ ] Public on YouTube
- [ ] No music and no copyrighted material
- [ ] No third-party logos on our own cards or thumbnail; product screens only
- [ ] Everything shown is real behavior; no mocked screens, no staged payments
- [ ] Sped-up waits labeled; no cut hides a step
- [ ] Only statistic: "About 1 in 6 small businesses still has no website (Clutch, Aug 2025)"
- [ ] Fictional shops stated; AI-generated menu image labeled
- [ ] No secrets visible: no passwords, tokens, .env or admin links
- [ ] Try-it URL on the end card
