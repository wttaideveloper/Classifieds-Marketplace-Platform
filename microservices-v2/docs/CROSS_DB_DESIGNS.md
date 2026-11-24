# Cross-database designs (approved 2026-11-19, implemented)

With one database per service, every place where the monolith changed two domains' data in **one
transaction** needs a replacement. Below is each case, the proposal, and **exactly what changes for
users**. D1, D2, D3 and D5 change consistency guarantees; they were **approved** and are **built**.
D4, D6, D7, D8 keep today's behaviour and are built too.

| # | Status | Where | Verified by |
|---|---|---|---|
| D1 | implemented | catalog `stock_api.py` + `StockReservation`; commerce `cart_service` checkout | integration `test_commerce_checkout.py` |
| D2 | implemented | events `event_order_api.py`; commerce `payment_payables` via outbox | integration `test_payment_event_orders.py` |
| D3 | implemented | `kit/mskit/outbox.py` (outbox + processed_messages per DB) | `kit/tests/test_outbox.py`, commerce/events flows |
| D4 | implemented | booking `catalog_reads.py`, replicas of services/enterprises/locations | integration `test_booking_on_catalog.py` |
| D5 | implemented | communication subscribes to events/learning replicas | integration `test_groups_reviews_approvals.py` |
| D6 | implemented | `kit/mskit/review_queue.py`; owners in catalog/learning/events | integration `test_groups_reviews_approvals.py` |
| D7 | implemented | meetings `owners.py`, events/learning `meetings_api.py` | integration `test_meetings_with_owners.py` |
| D8 | implemented | `POST /internal/v1/names` on owners | communication sweep |

Already in place (built and tested in identity, notifications, content, catalog, events, learning):
reference data replicated read-only (enterprises, locations), synchronous internal APIs with errors
re-raised exactly, notification de-duplication over HTTP, side writes after the caller's commit.

---

## D1. Checkout reduces product stock (commerce ↔ catalog) — approved, implemented

**Today** (`cart_service.checkout_service`): one transaction locks the cart and the products
(`SELECT … FOR UPDATE`), re-checks stock, decrements `products.stock_quantity`, creates the order and
clears the cart. Cancellations/refunds put stock back in the same way (`order_service`, `refund_service`).
Stock can never be oversold; an order exists only if its stock was taken.

**Proposal — stock reservations owned by catalog**
1. Commerce reads products (name, price, stock for display) from a read-only **replica** of `products`
   (same mechanism as enterprises).
2. At checkout commerce calls catalog `POST /internal/v1/stock/reservations` (idempotency key = cart id +
   cart version). Catalog checks and decrements stock **in its own transaction with the same row locks**
   and records a reservation. Not enough stock → the same 409/400 message as today.
3. Commerce creates the order and clears the cart in its transaction, marking the reservation used.
4. If step 3 fails, commerce releases the reservation (`DELETE …/reservations/{id}`, retried through the
   outbox, D3). A reservation never confirmed by an order expires after **15 minutes** (sweeper in catalog).
5. Cancellation/refund restock = `POST …/stock/releases` with an idempotency key, sent through the outbox.

**What changes for users**
* Stock is still never oversold.
* If commerce crashes between steps 2 and 3, those units are unavailable to other buyers for up to 15
  minutes (today: impossible).
* A restock after cancellation reaches the shop within seconds instead of instantly (minutes if catalog is
  down).
* Adds a `stock_reservations` table to **catalog's new database** (not the monolith schema).

Alternative: move `stock_quantity` ownership to commerce (catalog would show stock from a replica). Fewer
moving parts at checkout, but product editing in catalog then needs commerce's API — more API change.

## D2. Payment confirms an event order (commerce ↔ events) — approved, implemented

**Today** (`payment_payables`): to create a payment commerce reads the `event_orders` row (price lines,
registrant e-mail, status); when the payment succeeds or is refunded, `on_paid` / `on_refunded` set
`event_orders.payment_status` (and `status` on refund) **in the same transaction** as the payment record.

**Proposal**
1. Creating a payment: commerce calls events `GET /internal/v1/event-orders/{id}/payable` (lines, payer
   e-mail, status, title). Same checks, same errors (404, 403, 409 "not waiting for payment").
2. Payment succeeded: commerce commits the payment **and** an outbox row in one transaction; the relay
   calls events `POST /internal/v1/event-orders/{id}/payment-confirmed {payment_id}` (idempotent) until it
   succeeds. Refund: `…/refunded {fully}` the same way.
3. Cart orders stay inside commerce (no change).

**What changes for users**
* Between the payment succeeding and events applying the confirmation there is a window — normally
  milliseconds, but **minutes if the events service is down** — in which the payment shows "succeeded"
  while the registration still shows "pending payment". Nothing is lost: the confirmation is retried until
  delivered.
* Today these could never disagree.

## D3. Durable outbox per service — approved, implemented (prerequisite of D1, D2, D5)

Each service that must tell another service something **after** its own commit gets an `outbox` table in
**its own new database**, written in the same transaction as the business change; a relay thread delivers
each row to the target internal API with retries (backoff up to 1 h) and an idempotency key, and the target
ignores duplicates. Replaces today's non-durable `mskit.after_commit` for money and stock flows (and later
for notifications and the review history).
Effect: adds one table per service database (new databases only; the monolith schema is unchanged).

## D4. Service bookings (booking ↔ catalog, commerce) — no behaviour change, implemented

Booking needs service details (price, duration, provider, enterprise): read-only **replica** of `services`
(+ enterprises). Orders reuse booking's `actor_from` (role/tenant resolution, no data) → copied into
commerce. The booking "payment" is the demo flag today (no gateway, no cross-database transaction).

## D5. Group chats created by registrations and enrolments (events/learning → communication) — approved, implemented

**Today** event registration / training enrolment approval and their group-chat membership (conversation,
participants, consents) are written in one request. Communication also reads registrations, enrolments,
trainings and events to decide who may join a group (28 + 12 import edges).

**Proposal**: events/learning publish `registration.confirmed`, `registration.cancelled`,
`enrolment.approved`, `enrolment.withdrawn` through the outbox; communication creates/updates the group
membership when it receives them. Eligibility checks call `GET /internal/v1/events/{id}/participants` /
`…/trainings/{id}/participants`.
**What changes for users**: after registering, the member appears in the event's group chat a moment later
(normally sub-second; minutes if communication is down) instead of in the same response.

## D6. Approvals and review moderation (moderation → events, learning, catalog) — no behaviour change, implemented

Moderation's endpoints approve/reject/request changes on events, trainings, courses, programs and reviews,
writing those domains' tables plus audits and notifications. Proposal: moderation becomes a thin front:
each action calls the owner's internal API (`POST /internal/v1/events/{id}/moderation {action, reason,
actor}`, same for trainings/programs/reviews), which runs the monolith's same transition **in the owner's
single transaction**. Moderation keeps only `review_moderation_log`. Synchronous, so responses and
consistency are as today.

## D7. Meetings (meetings ↔ events, learning) — no behaviour change, implemented

Already designed and half built (events and learning send snapshots and store the links meetings returns,
in the same request): meetings' own endpoints authorize staff through `POST /internal/v1/events/{id}/staff-access`
(and trainings), read registrations/enrolments for join links and reminders through the owners' APIs, and
store links generated by background retries through `POST …/meeting-writes`.

## D8. Names and dashboards that read many domains — no behaviour change, implemented

Communication's display-name lookup and some dashboards read names from training enrolments, addresses,
orders and reviews. Proposal: each owner exposes `POST /internal/v1/names {user_ids}`; the caller asks in
parallel with a 1 s timeout; an unanswered domain counts as "no name there" (the monolith's behaviour for
a missing row). Note: these screens become slower by one round trip.

---

### Decisions (approved by the user: "Approve all four")

| # | Question | Decision |
|---|---|---|
| D1 | Stock reservations in catalog (15-min hold if commerce crashes mid-checkout)? | **approved** |
| D2 | Accept a short "paid but still pending" window for event orders, retried until delivered? | **approved** |
| D3 | Add an outbox table to each new service database? | **approved** |
| D5 | Group-chat membership added just after registration instead of in the same request? | **approved** |
