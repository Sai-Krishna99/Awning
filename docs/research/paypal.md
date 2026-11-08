# Awning: PayPal research and sandbox spike

Date: 2026-11-07. Scope: design §3 and §5 (Orders v2 `payee`, refunds, webhooks, return URLs, vaulting, Agent Toolkit, JS SDK).
"Verified" means I ran it against `api-m.sandbox.paypal.com` with the app in `.env`. Spike code and saved responses are in `/tmp/claude-1000/-home-sai-projects-paypal-ai/b139f6a9-5881-4358-805d-fe7146d5cd03/scratchpad/paypal-spike/` (`out/` holds the JSON responses; no credentials or tokens are in any file).

The app's own sandbox business account (the default payee): **`sb-aehpx53782411@business.example.com`, merchant_id `T32ZFMDK8N93N`**.

## TL;DR

1. **Paying the app's own account and refunding work with no browser.** A card order with `payment_source.card` completes at create time (one call, `status: COMPLETED`). Refunds return 201 COMPLETED. Verified.
2. **The true third-party case is still unverified.** It needs a second sandbox business account; `spike_payee.py` is ready for it. Setting `payee` explicitly to the app's own email or merchant_id works, and the docs list no consent requirement for "pay another account".
3. **`payee.email_address` is not validated at order create.** A made-up email gets a normal `PAYER_ACTION_REQUIRED` order with an approval link. **`payee.merchant_id` is validated**: a fake one gets 422 `PAYEE_ACCOUNT_INVALID`. So the publish step can only validate merchants who give a merchant ID.
4. **Use `payment_source.paypal.experience_context`, not `application_context`.** The spec marks `application_context.return_url` "DEPRECATED". With `experience_context` the order comes back `PAYER_ACTION_REQUIRED` with link rel **`payer-action`**; with `application_context` it comes back `CREATED` with rel **`approve`**. Both point to `https://www.sandbox.paypal.com/checkoutnow?token=<ORDER_ID>`. Verified.
5. **Webhooks can be created by API, and the events PayPal records can be listed by API, even if your endpoint never answers.** Sandbox events took about 60–140 s to appear. In one run the refund event still hadn't appeared after 120 s. That confirms §5: capture on the return URL first, webhooks as backup.
6. **Don't build on the PayPal Agent Toolkit.** The Python package (`paypal-agent-toolkit` 1.11.0) has **no `payee` field** and no refund tool. It also pulls in 162 packages (chromadb, lancedb, crewai-tools, langchain, docker), and its design hands the agent a `pay_order` (capture) tool, which goes against §4 ("no tool can mark an order paid").
7. **The JS SDK and third-party payees are a risk.** The v5 docs say partners and marketplaces "must pass the merchant ID" (`merchant-id=`, emails deprecated) when acting for another merchant. Email-only onboarding doesn't give us that ID. Unverified in a browser. The safe fallback is the same redirect approval link the agent path uses.

## Question by question

### a. Orders v2 `payee` ("pay another account")
- **Answer.** Any REST app can set `purchase_units[].payee.email_address` or `.merchant_id`. "By default, money is paid to the application owner in their own account… Include the `email_address` or `merchant_id` of the account to receive the payment." The pay-another-account docs mention no consent, onboarding or limits. The errors reference does list payee-side failures:
  - `PAYEE_ACCOUNT_INVALID`, `PAYEE_ACCOUNT_RESTRICTED`, `PAYEE_ACCOUNT_LOCKED_OR_CLOSED`
  - `PAYEE_NOT_CONSENTED` ("Payee does not have appropriate consent to allow the API caller to process this type of transaction on their behalf")
  - `PAYER_CANNOT_PAY`
- **Verified in sandbox:**
  - Explicit payee = own email, wallet order: 200 `PAYER_ACTION_REQUIRED`.
  - Explicit payee = own merchant_id, card order: 201 COMPLETED, then refunded.
  - Payee = `nobody-…@example.com`, wallet order: **200, accepted.** A GET of that order still shows only the email; capture before approval gives `ORDER_NOT_APPROVED`.
  - Same fake email, card order: 422 `PAYEE_NOT_ENABLED_FOR_CARD_PROCESSING`.
  - Fake merchant_id: 422 `PAYEE_ACCOUNT_INVALID` at create.
  - Badly formed email: 400 `INVALID_PARAMETER_SYNTAX`.
- **Not verified:** create + approve + capture to a *different* real sandbox business account. Also note that a second account in the same developer sandbox is not the same as an unrelated production merchant.
- **Evidence:**
  - https://developer.paypal.com/docs/checkout/standard/customize/pay-another-account/
  - https://developer.paypal.com/api/rest/reference/orders/v2/errors/
  - `out/3_wallet_order_payee.json`, `out/4_fake_payee_wallet.json`, `out/4b_fake_payee_card.json`, `out/4c_fake_merchant_id.json`, `out/p1_card_own_merchant_id.json`
- **Confidence:** high that the field works for the app's own account and that email isn't checked at create; medium that it works for third parties (documented, not yet run).
- **Design:** keep the design, pending the `spike_payee.py` run. Publish-time validation:
  - An email can't be checked by API. Collect it, and tell the merchant that a wrong email means approval or capture fails later.
  - Optionally also take the **merchant ID**, which create does validate (create a throwaway order and don't approve it). It is also what the JS SDK wants (see h).

### b. Refunding a capture paid to another account
- **Answer.** `POST /v2/payments/captures/{id}/refund` accepts a `PayPal-Auth-Assertion` header, which the spec describes as "JWT assertion that identifies the merchant. Establishing the consent to act-on-behalf of a merchant is a prerequisite for using this header." PayPal's multiparty refund guide requires that "You must be an approved partner" and "onboard sellers before starting this integration" (Partner Referrals). The JWT itself is `{"alg":"none"}` header, `{"iss": <client_id>, "payer_id": <seller merchant id>}` payload, or `"email"` in place of `payer_id`, and an empty signature. Nothing in the docs suggests a plain app can refund another account's capture without that consent.
- **Verified:** refunds of captures paid to the app's own account work, both plain and with a self-targeted auth assertion (payer_id or email). Partial refunds work.
- **Not verified:** a refund of a third-party capture. `spike_payee.py` tries it plain and then with an assertion, and prints the exact error.
- **Evidence:**
  - https://developer.paypal.com/docs/api/payments/v2/ (OpenAPI: https://github.com/paypal/paypal-rest-api-specifications/blob/main/openapi/payments_payment_v2.json)
  - https://developer.paypal.com/docs/multiparty/issue-refund/
  - `out/2_refund.json`, `probe_assertion.py` output
- **Confidence:** high that third-party refunds need partner consent; the exact error is unverified.
- **Design:** keep §5. In-app refund only for the Awning demo merchant; "Refund in PayPal" link for other shops. Enable in-app refunds for everyone only if the spike shows a plain refund succeeds.

### c. Webhooks for orders paid to another account
- **Answer.** The docs only say events are app-scoped: "Payments on one REST app do not trigger webhooks subscribed to a different REST app." They don't say whether the *calling* app gets events when the payee is someone else.
- **Verified for the app's own account (card orders):** our app's temporary webhook recorded `PAYMENT.CAPTURE.COMPLETED` and `PAYMENT.CAPTURE.REFUNDED`. A one-step card order sent no `CHECKOUT.ORDER.*` events, which is expected because nothing was approved.
- **Not verified:** the third-party case. `spike_payee.py` creates a temporary webhook, lists what PayPal recorded for that order, capture and refund, then deletes the webhook.
- **Evidence:**
  - https://developer.paypal.com/api/rest/webhooks/
  - `out/run_self_card.txt`, `spike_webhook_events.py`
- **Confidence:** low for the third-party case until the spike runs.
- **Design:** keep the design. Treat webhooks as a bonus and have the return-URL capture plus polling `GET /v2/checkout/orders/{id}` be the source of truth. GET of our own orders and captures works; for the third-party case the spike prints whether it does.

### d. Return/cancel URLs and the approval link
- **Answer.** Send `payment_source.paypal.experience_context` with `return_url`, `cancel_url`, `user_action: "PAY_NOW"`, `shipping_preference: "NO_SHIPPING"` and `brand_name`. Other fields are `landing_page`, `locale`, `contact_preference` and `payment_method_preference`. The spec says of `application_context`: "DEPRECATED… Please specify this field in the `experience_context` object instead."
- **Verified:**
  - `experience_context` gives `status: PAYER_ACTION_REQUIRED` and links `self` + **`payer-action`**: `https://www.sandbox.paypal.com/checkoutnow?token=<ORDER_ID>`.
  - Legacy `application_context` gives `CREATED` and links `approve`, `update`, `capture`, with the same URL format.
- **Not verified:** PayPal redirects to `return_url?token=<ORDER_ID>&PayerID=…`, so the return handler can read the order ID from `token`. This is standard behavior but wasn't exercised, because it needs a human to approve.
- **Evidence:**
  - Orders OpenAPI: https://github.com/paypal/paypal-rest-api-specifications/blob/main/openapi/checkout_orders_v2.json (`order_application_context`, `paypal_wallet_experience_context`)
  - `out/3_wallet_order_payee.json`, `out/3b_wallet_order_application_context.json`
- **Confidence:** high.
- **Design:** change §5 wording: use `experience_context` only, and read the link with `rel == "payer-action"` (fall back to `approve`). Agent `create_order` returns that URL.

### e. Vaulting PayPal for buyer-not-present payments (stretch)
- **Answer.** Available in sandbox for this app. The token has scope `vault/payment-tokens/readwrite`.
  - `POST /v3/vault/setup-tokens` with `payment_source.paypal.usage_type: MERCHANT` returned 201 `PAYER_ACTION_REQUIRED` and an `approve` link (`/agreements/approve`).
  - An order with `payment_source.paypal.attributes.vault.store_in_vault: ON_SUCCESS` returned 200 `PAYER_ACTION_REQUIRED` and a `payer-action` link.
  - Flow: setup token → buyer approves once → `POST /v3/vault/payment-tokens` → later orders use `payment_source.paypal.vault_id`, and "Payers don't need to be present when charged."
- **Catch for Awning.** `usage_type` `MERCHANT` is for "future transaction directly with a merchant". `PLATFORM` is for "a marketplace or a channel that a payer can purchase goods and services from multiple merchants", which is what Awning is, and it most likely needs partner status. Unverified whether a MERCHANT token can pay a different `payee`.
- **Evidence:**
  - https://developer.paypal.com/docs/checkout/save-payment-methods/purchase-later/payment-tokens-api/paypal/
  - Vault OpenAPI: https://github.com/paypal/paypal-rest-api-specifications/blob/main/openapi/vault_payment_tokens_v3.json
  - `out/v1_setup_token_paypal.json`, `out/v2_order_store_in_vault.json`
- **Confidence:** high that it's available; low that it works across payees.
- **Design:** the stretch is feasible for demo-merchant shops. For other shops it hinges on vault_id + a third-party payee. Test only if the core is done.

### f. PayPal Agent Toolkit (Python)
- **Answer.** PyPI `paypal-agent-toolkit` 1.11.0 (2026-09-01), Python ≥3.11. It has adapters for the OpenAI Agents SDK / Assistants, LangChain, CrewAI and Bedrock, and **no Anthropic adapter**.
  - Order tools: `create_order`, `get_order`, `pay_order` (= capture). There is no refund tool in Python; the TypeScript version has `create_refund`.
  - `CreateOrderParameters` has only currency, items, discount, shipping, notes, return_url and cancel_url. **There is no `payee`, in Python or TypeScript**, so it cannot send money to a merchant's account.
  - It computes totals from LLM-supplied `item_cost`, the opposite of our server-side pricing.
  - You *can* call it from your own code without a framework: `PayPalAPI(client_id, secret, Context(sandbox=True)).run("get_order_details", {...})`.
  - It pins `openai-agents==0.0.2`, `langchain==0.3.23` and `crewai-tools==0.13.2`, which resolves to 162 packages (chromadb, lancedb, embedchain, docker, pyright).
- **Evidence:**
  - https://github.com/paypal/agent-toolkit/tree/main/python (`shared/orders/parameters.py`, `payload_util.py`, `pyproject.toml`)
  - https://pypi.org/project/paypal-agent-toolkit/
  - `uv pip compile` run locally
- **Confidence:** high.
- **Design:** don't depend on it. Use a thin httpx Orders client of our own. In the README, say we evaluated it and why it doesn't fit (no `payee`, and it lets the agent capture). That shows judges deliberate PayPal engineering rather than ignorance of the toolkit.

### g. Webhook signature verification and subscribing by API
- **Verify.** `POST /v1/notifications/verify-webhook-signature` with:
  - `auth_algo`, `cert_url`, `transmission_id`, `transmission_sig` and `transmission_time`, from the `paypal-auth-algo`, `paypal-cert-url`, `paypal-transmission-id`, `paypal-transmission-sig` and `paypal-transmission-time` headers
  - our `webhook_id`
  - the `webhook_event` exactly as received
  
  The response is `{"verification_status": "SUCCESS"|"FAILURE"}`. The alternative is to verify locally: CRC32 of the raw body, then check `transmissionId|time|webhookId|crc32` against the cert. Simulator (mock) events **cannot** be verified by postback. Non-2xx responses are retried up to 25 times over 3 days. Max 10 webhook URLs per app.
- **Subscribe.** `POST /v1/notifications/webhooks {"url": "https://…", "event_types": [{"name": "PAYMENT.CAPTURE.COMPLETED"}, …]}`. Verified: 201 with an `id`; `DELETE /v1/notifications/webhooks/{id}` gives 204.
  - `GET /v1/notifications/webhooks-events` lists the events PayPal generated for the app's webhooks even when delivery to the URL fails. That is handy for debugging.
  - The app currently has **0 webhooks**. The spikes created and deleted temporary ones pointing at example.com.
  - Event names in sandbox include `CHECKOUT.ORDER.APPROVED`, `CHECKOUT.ORDER.COMPLETED`, `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.PENDING`, `PAYMENT.CAPTURE.DENIED` and `PAYMENT.CAPTURE.REFUNDED`.
- **Evidence:**
  - https://developer.paypal.com/api/rest/webhooks/rest/
  - https://developer.paypal.com/api/rest/webhooks/
  - Webhooks OpenAPI: https://github.com/paypal/paypal-rest-api-specifications/blob/main/openapi/notifications_webhooks_v1.json
  - `out/w1_list_webhooks.json`, `out/w2_event_types.json`
- **Confidence:** high.
- **Design:** keep the design. Create the webhook once against the Render URL (script or dashboard) and store `PAYPAL_WEBHOOK_ID` in env, since verification needs it. Capture handling must be idempotent, because the return URL and the webhook can both fire.

### h. JS SDK buttons for an order whose payee is another account
- **Answer (docs).** v5: `https://www.paypal.com/sdk/js?client-id=<OUR_CLIENT_ID>&components=buttons`. About `merchant-id`, the docs say: "Use this parameter only for partner, marketplaces, and cart solutions when you are acting on behalf of another merchant… You must pass the merchant ID of the merchant for whom you are facilitating payments". They also say "PayPal email addresses are now DEPRECATED as merchant IDs and can no longer be used". For standalone integrations: "Don't pass a merchant ID, because it is automatically derived". The client-id "determines where any transactions are paid to".
  - The mismatch error people hit is "Payee(s) passed in transaction does not match expected merchant id. Please ensure you are passing merchant-id=…" (quoted in third-party troubleshooting docs, not on PayPal's own pages).
- **Versions.** The v5→v6 upgrade page labels v5 "deprecated", but the Standard Checkout guide (updated 2026-10-14) still uses v5 and says it "remains supported", while recommending v6.
  - v6 script: `https://www.sandbox.paypal.com/web-sdk/v6/core` (live: `www.paypal.com/web-sdk/v6/core`), then `window.paypal.createInstance({clientId | clientToken, components: ["paypal-payments"], pageType})`.
  - In v6, `createOrder` must resolve to `{ orderId }`.
  - The documented v6 options include **no `merchantId`**. A PayPal v6 sample comments that a `clientToken` lets the SDK "derive the merchantId it needs internally from the token itself".
- **Not verified.** Whether v5 or v6 buttons with our client-id complete an order whose payee is another account without `merchant-id`. This needs a browser and a second account.
- **Evidence:**
  - https://developer.paypal.com/sdk/js/configuration/
  - https://developer.paypal.com/docs/checkout/standard/integrate/
  - https://developer.paypal.com/v5-v6
  - https://developer.paypal.com/sdk/js/set-up
  - https://developer.paypal.com/docsai/developer/how-to/sdk/js/v6/configuration
  - https://github.com/paypal-examples/v6-web-sdk-sample-integration (`client/components/applepayPayments/basicOneTimePayment/html/src/app.js`)
  - https://docs.adyen.com/payment-methods/paypal/paypal-troubleshooting
- **Confidence:** medium (docs only).
- **Design:** change §3.3/§5 slightly. For the hackathon, use **v5** (stable, documented server create + capture) and pass `merchant-id=<shop merchant ID>` on a shop's storefront page when we have one. The decided fallback, if buttons fail for third-party payees, is a storefront "Pay with PayPal" button that POSTs to our server and redirects to the same `payer-action` URL as the agent path. That fallback has no SDK payee check and is one code path. Asking merchants for their merchant ID (optional field next to the email) helps both here and in (a).

## What `spike_payee.py` closes
It runs: card order to `PAYEE_EMAIL` (or `PAYEE_MERCHANT_ID`) → capture → GET order and capture → refund (plain, then with `PayPal-Auth-Assertion`) → webhook events recorded for our app (temporary webhook, then deleted). If card create fails, which happened for a fake payee with `PAYEE_NOT_ENABLED_FOR_CARD_PROCESSING`, it creates a wallet order instead and prints the `payer-action` URL plus the exact capture command for after a human approves.

```
PAYEE_EMAIL=<second sandbox business email> uv run --no-project --with httpx python -I /tmp/claude-1000/-home-sai-projects-paypal-ai/b139f6a9-5881-4358-805d-fe7146d5cd03/scratchpad/paypal-spike/spike_payee.py
```
Tested end to end against the app's own account; output is in `out/run_self_card.txt`. Read the result like this:
- Capture status `COMPLETED` and the second account's email/merchant_id on the capture mean §5's core assumption holds.
- The refund lines settle (b).
- The webhook line settles (c).
