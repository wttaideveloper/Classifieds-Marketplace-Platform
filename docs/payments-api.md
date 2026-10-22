# Payments, Multi-Currency and Tax API

For the web and mobile developers, and for finance. Base path `/api/v1`. All times are UTC. Every amount is a **string** in JSON ("1180.00") with the number of decimals of its currency (INR 2, JPY 0, KWD 3), so nothing is lost to floating point. Send amounts as strings too.

Who is calling:

- **Customer**: anyone signed in who is paying. Sees and acts on their own payments, refunds, invoices only.
- **Merchant**: the business's Enterprise Admin. Sees the payments made to their business, refunds them, sets their own settlement schedule.
- **Super Admin**: the platform's finance side. Sees everything and manages currencies, rates, tax rules, commission and payouts.

Someone else's payment, refund or invoice always answers 404, exactly as if it did not exist.

## 1. How a payment works

1. **Quote** (optional but recommended): `POST /payments/quote` shows the items, the tax per component, the exchange rate and the final amount in the currency the customer will pay in. Show this before the customer confirms.
2. **Create**: `POST /payments` with what is being bought. The amount is never sent: it is read from the order. The answer has the `payment` and `checkout`, which is what the gateway's SDK needs.
3. **Pay** in the gateway's SDK (Razorpay Checkout, Stripe.js / mobile SDK). 3-D Secure / OTP is handled there.
4. **Verify**: `POST /payments/{id}/verify` with what the SDK returned. The gateway's own answer decides; a forged signature is refused. The gateway also calls our webhook, so the payment completes even if the app is closed.
5. When the payment is `succeeded`: the invoice exists, the order is updated (a shop order becomes `confirmed`, an event order `confirmed`), the customer, the business and the invoice e-mail are notified.

### Payment statuses

| Status | Meaning | What to do |
|---|---|---|
| `created` | made at the gateway, not paid yet | show the checkout, then verify |
| `requires_action` | 3-D Secure / OTP still to be done (`next_action`) | do the step, then verify again |
| `processing` | sent; no final answer yet (also after a gateway timeout) | wait, it is checked automatically |
| `authorized` | approved, money held, not taken (`capture_method: manual`) | the business captures or cancels |
| `succeeded` | paid | invoice, refund |
| `partially_refunded`, `refunded` | some or all returned | |
| `failed` | refused (`failure_code`, `failure_message`) | `retry` |
| `cancelled`, `expired` | closed without payment | `retry` for expired |
| `review` | held for a Super Admin (risk check, or money that arrived late or in the wrong amount) | wait |

`actions` on every payment lists what the caller can do now: `verify`, `checkout`, `cancel`, `retry`, `capture`, `resolve`, `refund`, `download_invoice`. Use it to show or hide buttons.

## 2. Payments (`/payments`)

| Method and path | What it does |
|---|---|
| `POST /payments/quote` | the price before confirming. Nothing is saved |
| `POST /payments` | create a payment (201). Header `Idempotency-Key` makes a repeat return the same payment |
| `GET /payments` | payment history. Filters: `status`, `currency`, `purchase_type`, `purchase_id`, `date_from`, `date_to`, `page`, `page_size`; Super Admin also `tenant_id`, `customer_user_id`; business/platform `needs_reconciliation` |
| `GET /payments/{id}` | details |
| `GET /payments/{id}/checkout` | checkout parameters of an unpaid payment (the payer only) |
| `POST /payments/{id}/verify` | verify with the gateway (body: what the SDK returned) |
| `POST /payments/{id}/retry` | a new attempt after a failure |
| `POST /payments/{id}/cancel` | cancel an unpaid or authorized payment |
| `POST /payments/{id}/capture` | business/platform: take the money of an authorized payment |
| `POST /payments/{id}/resolve` | Super Admin: settle a payment under review (`approve` / `reject`) |
| `GET /payments/{id}/transactions` | business/platform: every exchange with the gateway |
| `POST /payments/webhook/{provider}` | called by the gateway (signature checked) |

### What can be paid for (`purchase_type`)

- `cart_order`: a shop order, `purchase_id` = the order id. Must be `pending`. Paying confirms it.
- `event_order`: an event registration order. Must have `payment_status` `pending` or `failed` (the registration flow must create the order that way to use payments). Paying sets it `confirmed`.
- `custom`: a business (or the platform) asks one customer to pay. The business sends `items`, `item_currency`, `customer_user_id` (and `tenant_id` if it is the Super Admin). The customer finds it in their payments and opens `GET /payments/{id}/checkout`. `capture_method: "manual"` holds the money first.

```json
{ "purchase_type": "cart_order", "purchase_id": "3f2a0b6e-…", "currency": "USD",
  "saved_method_id": null, "return_url": "https://app.example.com/pay/done",
  "billing": { "name": "Priya Sharma", "country": "IN", "state": "Karnataka", "city": "Bengaluru", "postal_code": "560001", "address_line1": "12 MG Road", "is_business": false } }
```

`billing.country` decides the tax. It is taken from the shipping address of a shop order when not sent.

### The payment object (short)

```json
{
  "id": "…", "status": "succeeded", "status_label": "Paid", "purchase_type": "cart_order", "purchase_id": "…", "attempt_no": 1,
  "item_currency": "INR", "subtotal_amount": "1000.00", "tax_amount": "180.00", "total_amount": "1180.00",
  "charge_currency": "USD", "charge_subtotal": "12.00", "charge_tax": "2.16", "charge_total": "14.16",
  "exchange_rate": "0.0120000000", "base_currency": "INR", "base_total": "1180.00",
  "refunded_amount": "0.00", "refundable_amount": "14.16", "method_brand": "visa", "method_last4": "4242",
  "lines": [ { "description": "Yoga mat", "quantity": 1, "unit_amount": "1000.00", "net_amount": "1000.00", "tax_amount": "180.00", "gross_amount": "1180.00",
               "charge_net_amount": "12.00", "charge_tax_amount": "2.16", "charge_gross_amount": "14.16", "taxes": [ … ] } ],
  "tax_breakdown": [ { "name": "CGST", "rate": "9.000000", "taxable_amount": "12.00", "tax_amount": "1.08" }, { "name": "SGST", … } ],
  "reverse_charge": false, "is_tax_exempt": false, "invoice_id": "…", "actions": ["download_invoice", "refund"]
}
```

`item_*` amounts are in the currency the item is priced in. `charge_*` amounts are what the customer pays, in the currency they chose. `exchange_rate` is locked when the payment is created, so it never changes later. `base_total` is the value in the organisation's base currency, for the books.

### Things to know

- **Price shown = price charged.** The quote and the created payment use the same rules; the payment keeps the rate and the tax rules it used.
- **Coming back.** If the customer opens checkout again for an order that already has an unpaid payment in the same currency, they get that payment back, not a new one. A different currency starts a new payment and cancels the old one.
- **Failures and retries.** `POST /payments/{id}/retry` starts attempt 2, 3 … for the same order (5 at most, then 429). The price is worked out again with the current rate. A day after a failure with no retry, one reminder is sent.
- **Gateway timeout.** If the gateway does not answer, the payment becomes `processing` with `needs_reconciliation: true` and is checked against the gateway automatically. Money found there marks it paid; if nobody paid it expires.
- **Risk check.** Before the gateway is asked, a score is worked out (large amount, repeated failures, many payments in a few minutes). High scores are held (`review`, a Super Admin decides with `/resolve`) or refused (403 "declined by our security check").
- **Money that should not have come** (arrived after the payment was cancelled or expired, in the wrong amount, or for an order already paid by another payment) is held in `review`. `resolve approve` accepts it, `resolve reject` refunds it.
- **Limits.** Each currency can have a smallest and largest single payment (422 "The smallest payment in USD is 5").
- **Stale rates.** A payment in a currency other than the item's needs a rate newer than `EXCHANGE_RATE_MAX_AGE_HOURS` (or a pinned manual rate): otherwise 503 "The exchange rate for USD is out of date".

### Verify bodies by gateway

| Gateway | Send to `/verify` |
|---|---|
| Razorpay | `razorpay_order_id`, `razorpay_payment_id`, `razorpay_signature` |
| Stripe | `payment_intent_id` (or nothing) |
| test | `test_token`: `tok_success`, `tok_fail`, `tok_insufficient`, `tok_3ds` (then again with `"authenticated": true` or `false`), `tok_timeout` |

## 3. Saved payment methods (`/payment-methods`)

`GET` lists mine (brand, last four digits, expiry, `is_default`, `is_expired`). `POST {"token": "...", "set_default": true}` saves one: send the **token the gateway's SDK returned** (Stripe `pm_…`, Razorpay token id; test gateway `tok_saved_visa_4242`). A card number or CVV is refused (422); nothing like that is stored. `PUT /{id}/default`, `DELETE /{id}`. Pay with one by sending `saved_method_id` to `POST /payments`.

## 4. Currencies and exchange rates (`/currencies`)

| Method and path | Who | What |
|---|---|---|
| `GET /currencies` | public | supported currencies with decimals and limits; `base_currency` |
| `GET /currencies/base` | public | the base (accounting) currency |
| `GET /currencies/rates` | public | current rates: 1 base currency = this many of each |
| `GET /currencies/convert?amount=&from=&to=` | public | convert, rounded to the target's decimals |
| `GET` / `PUT /currencies/preference` | signed in | my preferred currency |
| `POST /currencies`, `PATCH /currencies/{code}`, `GET /currencies/admin/all` | Super Admin | add, switch on/off, set min/max, list all |
| `POST /currencies/rates` | Super Admin | set a rate by hand (`pinned: true` keeps the automatic refresh away) |
| `POST /currencies/rates/refresh` | Super Admin | fetch the latest rates now |
| `GET /currencies/rates/history` | Super Admin | every rate ever set |

Rates are stored against the base currency and only added, never changed. Converting A to B goes through the base. The automatic refresh runs on a schedule and reads the Frankfurter (ECB) service; a failure changes nothing.

## 5. Tax (`/tax`)

Rules live in the database, so a rate changes with an API call and no release.

| Method and path | Who | What |
|---|---|---|
| `GET /tax/rules` | signed in | rules in force (`current_only=false` includes scheduled and replaced versions) |
| `POST /tax/rules` | Super Admin | create (`tax_type` gst / vat / sales_tax / service_tax, `name`, `country`, `state`, `rate`, `applies_to`, `condition`, `rounding_mode`, `reverse_charge_allowed`, `valid_from`) |
| `PUT /tax/rules/{id}` | Super Admin | change: adds the **next version**, valid from a date; the old one keeps applying before it |
| `DELETE /tax/rules/{id}` | Super Admin | switch off |
| `GET /tax/rules/{id}/versions` | Super Admin | all versions |
| `POST /tax/calculate` | signed in | tax for a transaction |
| `GET /tax/history` | signed in | tax of completed payments (refunds appear as negative rows) |
| `GET /tax/reports?group_by=tax_type\|country\|state\|name\|month\|day&format=csv` | merchant, Super Admin | tax collected, net of refunds |
| `POST /tax/validate-exemption` | signed in | check a GSTIN / VAT number / certificate format |
| `POST /tax/exemptions`, `GET /tax/exemptions`, `PATCH /tax/exemptions/{id}` | signed in / Super Admin | register, list, approve / reject / revoke |
| `GET` / `PUT /tax/merchant-profile` | merchant, Super Admin | where the merchant supplies from, and their tax number |

How the tax is worked out: the customer's country and state decide which rules apply. `condition: intra_state` applies when the merchant and the customer are in the same state, `inter_state` otherwise, which is how India's CGST + SGST (same state) and IGST (different state) are both described. Each rule rounds its own tax with its own mode. Prices that include tax (`prices_include_tax`) are split back into net and tax so the parts add up exactly.

Example rules for India: CGST 9 and SGST 9 with `condition: intra_state`, IGST 18 with `condition: inter_state`, all `country: IN`. **The rates in this system are whatever the Super Admin enters; nothing is pre-filled. Finance must confirm every rate before launch.**

- **Exemption certificate**: once a Super Admin approves it, that customer pays no tax in that country (and state, if the certificate has one) until it expires or is revoked.
- **Reverse charge**: for a business customer **abroad** (the merchant is in another country) with an **approved** GSTIN / VAT number, rules with `reverse_charge_allowed` charge nothing and the invoice says so.
- A GSTIN is checked for its format and check character, a VAT number for its country's format. A number that passes is still `pending` until a Super Admin approves it, because a format check does not prove the business is registered (`TAX_ID_AUTO_APPROVE_VALID_FORMAT` changes that).

## 6. Invoices (`/invoices`)

An invoice is issued automatically when a payment succeeds, in the currency the customer paid in, with the tax by component, the exchange rate used and the base-currency value. Numbers run in an unbroken series per business and year (`INV-2026-000001`); credit notes are `CN-…`, debit notes `DN-…`. Nothing is edited after issue: a correction is a credit or debit note.

| Method and path | What |
|---|---|
| `GET /invoices` | history (invoices, credit and debit notes). `doc_type`, `payment_id`, `search` (number), dates |
| `GET /invoices/{id}` | details |
| `GET /invoices/{id}/download` | the PDF |
| `POST /invoices/{id}/email` | send it to the customer (their own account address, through the notification service) |
| `POST /invoices/generate` `{payment_id}` | returns the invoice of a paid payment (creates it if missing) |
| `POST /invoices/{id}/debit-notes` | business/platform: a correction upward, with a reason |
| `GET` / `PUT /invoices/templates` | wording, logo, colour, number prefix, locale of a business's invoices; the Super Admin sets the platform default |

The PDF shows currency codes (INR 1,180.00), not symbols, and groups numbers the Indian way (12,34,567.89) for the `en-IN` locale. A credit note is raised automatically by every completed refund.

## 7. Refunds (`/refunds`)

| Method and path | What |
|---|---|
| `POST /refunds` `{payment_id, amount?, reason?, destination?}` | ask for (customer) or give (business/platform) a refund. No `amount` = everything left |
| `GET /refunds`, `GET /refunds/{id}` | history and status (`overdue=true` for the late ones) |
| `POST /refunds/{id}/decision` `{action: approve\|reject, reason}` | business/platform. A rejection needs a reason the customer reads |
| `POST /refunds/{id}/retry` | send a failed or stuck refund to the gateway again (safe: same idempotency key) |
| `POST /refunds/{id}/complete-manual` `{bank_reference}` | Super Admin: record a bank-transfer refund as paid |

A customer's request waits for approval; a refund raised by the business or platform is approved and sent at once. Statuses: `requested`, `approved`, `processing`, `completed`, `failed`, `rejected`. When one completes: the payment becomes `partially_refunded` or `refunded`, the credit note is issued, the tax is reversed (negative tax rows), a fully refunded shop/event order becomes `refunded`, and the customer is notified.

Where the money goes: `original` (default; through the gateway, for 180 days), `bank_transfer` (finance pays and records the reference), `wallet` (not available). A refund still open after `REFUND_SLA_HOURS` (default 72) from its request, or from its approval, is escalated once to the Super Admins (`refund_escalated`).

## 8. Settlement (`/settlements`)

For each business, on its schedule, all unsettled paid payments and completed refunds are gathered into one settlement in the business's payout currency:

`net = gross − refunds − commission − gateway fees − fixed fees`

Commission is the platform's share of the **pre-tax** amount (the business's own rate, else `PLATFORM_COMMISSION_PERCENT`); a refund gives its share back. The gateway fee is the real one when the gateway reported it, else an estimate (`GATEWAY_FEE_PERCENT`). A payment belongs to at most one settlement. If the net is below the business's minimum payout, or not positive, nothing is settled and the items wait for the next run.

| Method and path | Who | What |
|---|---|---|
| `GET` / `PUT /settlements/config` | merchant, Super Admin | schedule (`daily`, `weekly` + `weekday`, `monthly` + `month_day`), payout `currency`, `min_payout`, masked `payout_account`. Only the Super Admin sets `commission_percent`, `fixed_fee`, `hold` |
| `POST /settlements/run` | Super Admin | settle everyone due, or one business (`tenant_id`, `force`, `dry_run`) |
| `GET /settlements`, `GET /settlements/{id}` | merchant, Super Admin | history; one settlement with every item |
| `GET /settlements/{id}/verify` | merchant, Super Admin | checks the settlement against its payments and refunds: `reconciled: true` is a 1:1 match |
| `GET /settlements/{id}/export` | merchant, Super Admin | CSV |
| `POST /settlements/{id}/pay` `{payout_reference}` | Super Admin | record the payout (the business is notified) |
| `POST /settlements/{id}/fail`, `/cancel` | Super Admin | a failed payout; cancelling returns the items to the next settlement |

**The money itself is not moved by this system.** It creates the settlement and finance records the transfer reference (`/pay`). Connecting an automatic payout (RazorpayX, Stripe Connect) is a separate piece of work.

## 9. Reports and finance (`/payment-reports`, `/finance`)

All reports take `date_from`, `date_to`, `format=json|csv`; a business sees its own figures, the Super Admin all (`tenant_id` narrows).

| Report | Path |
|---|---|
| Payments | `GET /payment-reports/payments` |
| Refunds (turnaround, escalations) | `GET /payment-reports/refunds` |
| Settlements (totals by currency) | `GET /payment-reports/settlements` |
| By currency (value in the base currency) | `GET /payment-reports/currency` |
| Exchange rates (low, high, average) | `GET /payment-reports/exchange-rates` (Super Admin) |
| Tax | `GET /tax/reports` |
| Reconciliation | `GET /payment-reports/reconciliation` (Super Admin) |

Finance tools (Super Admin unless noted):

- `POST /finance/reconciliation` sends the rows of the gateway's own report. Each is matched to our payment or refund and compared on currency, amount, status and fee. Differences (`missing_internal`, `missing_gateway`, `amount_mismatch`, `status_mismatch`, `fee_mismatch`, `currency_mismatch`, `duplicate`) are saved, listed with `GET /finance/reconciliation[/{id}]`, and resolved with a note.
- `GET /finance/audit` is the audit log: who changed a tax rule, an exchange rate, a settlement setting, a refund decision, and when, with before and after. Entries cannot be edited or removed.
- `POST /finance/jobs/run` runs the scheduled jobs now.
- `GET /finance/export?kind=payments|refunds|invoices|tax&format=csv|json` is the file for the accounts team or an ERP import (merchants get their own business only). There is no direct ERP connector.

## 10. Notifications

Through the existing notification feed (and push). Sent after the change is saved; a failure never breaks the call.

| Category | To |
|---|---|
| `payment_succeeded`, `payment_failed`, `payment_retry_reminder` | the payer |
| `payment_received` | the business's admins |
| `payment_review_required` | Super Admins |
| `invoice_generated` (also e-mail) | the payer |
| `refund_requested` | the business's admins |
| `refund_approved`, `refund_rejected`, `refund_completed` | the payer |
| `refund_escalated` | Super Admins |
| `settlement_completed` | the business's admins |
| `tax_rule_updated` | Super Admins |

## 11. Settings (environment)

| Setting | Meaning |
|---|---|
| `PAYMENT_GATEWAY` | `test` (default, moves no money), `razorpay` or `stripe`. `test` is refused when `ENVIRONMENT=production` unless `PAYMENT_ALLOW_TEST_GATEWAY=true` |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` | Razorpay |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PUBLISHABLE_KEY` | Stripe |
| `PAYMENT_BASE_CURRENCY` | the books' currency (default INR). Change only with finance sign-off |
| `PAYMENT_STALE_MINUTES`, `PAYMENT_MAX_ATTEMPTS` | when a waiting payment is checked; attempts per order |
| `PAYMENT_RISK_REVIEW_SCORE`, `PAYMENT_RISK_DENY_SCORE`, `PAYMENT_RISK_HIGH_AMOUNT_BASE`, `FRAUD_SERVICE_URL` | the risk check and an optional outside scoring service |
| `REFUND_SLA_HOURS` | refund target time |
| `PLATFORM_COMMISSION_PERCENT`, `GATEWAY_FEE_PERCENT` | commission and fee estimate for settlements |
| `EXCHANGE_RATE_API_URL`, `EXCHANGE_RATE_MAX_AGE_HOURS` | rate source and how old a rate may be for a payment |
| `PAYMENT_JOBS_ENABLED`, `PAYMENT_JOBS_INTERVAL_SECONDS` | the scheduled jobs (on in production by default, every 10 minutes) |
| `TAX_ID_AUTO_APPROVE_VALID_FORMAT` | approve a GSTIN / VAT number with a valid format at once (default off) |

Webhook URL to give the gateway: `POST https://<api>/api/v1/payments/webhook/razorpay` (or `/stripe`). Events used — Razorpay: `payment.authorized`, `payment.captured`, `payment.failed`, `refund.processed`, `refund.failed`. Stripe: `payment_intent.succeeded`, `payment_intent.payment_failed`, `payment_intent.amount_capturable_updated`, `payment_intent.canceled`, `refund.updated`.

## 12. Before this goes live

1. Run the migration `b7e3d9a1c5f2` (it needs `a6d2c8e4b1f7` before it). It creates the tables and the usual currencies (INR, USD, EUR, GBP, AED, SGD on; AUD, CAD, JPY, KWD off). **No exchange rates and no tax rules are pre-filled**: add rates with `POST /currencies/rates/refresh` (or by hand) and tax rules with `POST /tax/rules`.
2. The Razorpay and Stripe adapters were written from the vendors' documentation and are tested for their signature checks and for how they read responses, but **have not been run against a live account**. Run a full payment, a 3-D Secure payment, a refund and a webhook with the vendor's test keys first.
3. Event registrations create their order as `confirmed` today. To charge through this module, the registration flow has to create the order as `pending` and send the customer to `POST /payments`.
4. Settlements are calculated here; the transfer to the merchant's bank is done by finance and recorded with `/settlements/{id}/pay`.
5. Tax numbers (GSTIN, VAT) are checked for format only. Checking them against the GST portal or VIES is not connected.
6. There is no rate limit on these endpoints yet; put one in front of `POST /payments` and the webhook at the gateway or proxy.
7. Data residency: everything is stored in this system's database. If a rule requires financial data to stay in a country, host the database there.
