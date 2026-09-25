# Event attendee management and dashboard: backend contract (Phase 2.3)

Two read-only capabilities for event managers, built on the existing `EventRegistration`, `EventOrder` and
`EventWaitlist` tables. **No schema change, no migration.** Registration, checkout, ticket, QR, waitlist,
meeting-link, `status` and `lifecycle_state` behaviour is untouched, and the Phase 2.2 `event_type`/`modules`
are reported but still not enforced.

## Endpoints

| Method + path | Purpose | Response |
|---|---|---|
| `GET /api/v1/events/{event_id}/attendees` | Paginated attendee list with search, filters, sorting | `EventAttendeePaginatedResponse` |
| `GET /api/v1/events/{event_id}/registrations/{reg_id}` | One attendee | `EventAttendeeResponse` |
| `GET /api/v1/events/{event_id}/dashboard` | Registrations, capacity, attendance, waitlist, orders, revenue | `EventDashboardResponse` |
| `GET /api/v1/events/{event_id}/registrations/export` | CSV (existing; now filterable and richer) | `text/csv` |
| `GET /api/v1/events/{event_id}/reports?type=revenue` | Legacy report (existing; now order-based) | same shape, see below |

`GET /{event_id}/registrations` (the bare array Web consumes) is **unchanged**. The list needed a paginated,
typed shape, so it lives on its own path instead of changing that one.

### Access

Same ownership rule as every other event-management route (Phase 2.1): the owning tenant's `admin` or
`provider`, or an **active** platform `super_admin`. A foreign tenant is `403`, a customer `403`, no token
`401`, an unknown or deleted event `404`, an inactive super admin `403`. A tenant in a query string is never
consulted. A registration of a different event than the one in the path is `404`.

## Attendee list

Query parameters (all optional):

| Parameter | Meaning |
|---|---|
| `q` | Case-insensitive contains-match on name, email and registration reference; a pasted registration id matches exactly. `%` and `_` are literal. Max 200 chars. |
| `status` | `confirmed`, `attended`, `cancelled`, `no_show` |
| `ticket_type_id` | exact ticket type id |
| `payment_status` | `free`, `unpaid`, `pending`, `paid`, `refund_requested`, `refunded`, `cancelled`, `failed` |
| `checked_in` | `true` = status `attended`; `false` = everything else |
| `source` | `walk_in` = registered by an organizer at the venue (Phase 2.5); `online` = everything else, including every row that predates the column (NULL) |
| `registered_from`, `registered_to` | inclusive whole days (UTC, `YYYY-MM-DD`); from > to is `422` |
| `sort` | `newest` (default), `oldest`, `name`, `email`. Every order ends in the registration id, so pages never repeat or skip rows |
| `page`, `page_size` | project convention: `page >= 1`, `page_size` 1..100 (default 20). Response carries `pagination {total, page, page_size, total_pages}` |

Each item: `registration_id`, `event_id`, `participant_name`, `participant_email`, `registration_status`,
`registration_reference` (the QR value), `ticket_type_id`, `ticket_type_name`, `quantity`, `payment_status`,
`order_id`, `order_status`, `amount`, `currency`, `is_checked_in`, `checked_in_at`, `checked_out_at`,
`registered_at`, `registration_source` (`online` / `walk_in`, Phase 2.5), `custom_answers[{field_id, label, value}]`, `session_attendance[{session_id, title, checked_in,
checked_in_at, checked_in_by, checked_out_at}]` (Phase 2.4, one entry per session of the event, separate from
`is_checked_in`; empty without sessions and in the CSV export).

Never returned: payment provider / refund reason, `checked_in_by`, session id, tenant or enterprise ids,
meeting links, the raw `custom_fields` dict.

### Payment status (derived, never stored)

`EventRegistration` has no payment column and gets none. The status comes from the paired order:

| Order `status` / `payment_status` | Attendee `payment_status` |
|---|---|
| `refunded` in either | `refunded` |
| status `cancelled` | `cancelled` (even if `payment_status` still says `refund_requested`: an admin can move `refund_requested -> cancelled` without touching the payment column) |
| `refund_requested` in either | `refund_requested` |
| payment `failed` | `failed` |
| payment `pending` | `pending` |
| payment `confirmed` and status `confirmed`/`completed` | `paid` |
| anything else (unknown, NULL) | `pending` (never `paid`) |
| **no order**, free event | `free` |
| **no order**, paid event | `unpaid` |

The list filter (SQL `CASE`), the displayed value (Python) and the dashboard order counts share this one
definition (`app/utils/event_payments.py`); a test walks every status combination through all three. The
backend writes order `status` `confirmed`, `completed`, `cancelled`, `refund_requested`, `refunded` and
`payment_status` `confirmed`, `refund_requested`, `refunded`; `pending` and `failed` are accepted for data
written elsewhere.

### Registration to order pairing

`EventOrder` has no registration foreign key. A registration is paired with the **latest order for the same
event and email (case-insensitive) created at or before the registration**. Checkout inserts the order first,
so this is exact; a person who cancels and rebooks gets each registration paired with its own order. Unlike
Phase 2.1's `_linked_order_for_registration`, no 5-minute look-ahead is applied (no flow needs it, and it would
attach the second order to the first, cancelled registration). They agree for every active registration.

Registrations created without an order (free registration, group members) simply have none.

### Custom answers

Answers are read from the registration's stored `custom_fields`. Labels come from the event's pinned Event Form
version, or the legacy default version like `GET /events/{id}/form-configuration`, matching on field id. A key
the form no longer defines is shown with its id as label. `group_size`, `group_members` and `group_leader` are
bookkeeping, not answers, and are omitted.

## Dashboard

```
event         id, title, status, lifecycle_state, event_type, modules, pricing_type, currency, dates, time_zone
registrations total, active, confirmed, attended, cancelled, no_show, other,
              online, walk_in   (Phase 2.5: by source, all statuses, summing to total)
capacity      capacity, unlimited, seats_taken, seats_reserved, available_seats, is_full, fill_percentage
attendance    checked_in, not_checked_in, attendance_percentage
waitlist      total, waiting, payment_pending, promoted, expired, left, other
orders        total, successful, pending, refund_requested, refunded, cancelled, failed
revenue       currency, total_revenue, refunded_amount, pending_refund_amount, paid_orders,
              mixed_currency, by_currency[], unparseable_orders
sessions      [{session_id, title, session_date, start_time, registered_count, checked_in_count,
              attendance_percentage}]   (Phase 2.4; see event-session-attendance-contract.md)
generated_at
```

| Figure | Source |
|---|---|
| registrations | `EventRegistration.status`, grouped. `active` = confirmed + attended. `other` = any other status |
| attendance | `checked_in` = `attended`; `not_checked_in` = `confirmed`; `attendance_percentage` = attended / (confirmed + attended), 1 decimal, `null` when nobody is expected |
| capacity | `Event.capacity` (the limit every seat-taking path enforces); unparseable or empty = unlimited |
| `seats_taken` | `event_service._seats_taken`, the same function registration, checkout and waitlist promotion use |
| `seats_reserved` | waitlist entries `payment_pending` whose offer has not expired (`payment_offer_expires_at` null or in the future), the rule `_try_promote_from_waitlist` uses |
| `available_seats` | `max(capacity - seats_taken - seats_reserved, 0)`; `is_full` when that is 0 |
| `fill_percentage` | seats_taken / capacity, 1 decimal (can exceed 100 for an overbooked legacy event) |
| waitlist | `EventWaitlist.status`, grouped (raw status; an offer that has lapsed but is not yet swept still reads `payment_pending`, but reserves no seat) |
| orders | `EventOrder`, classified by the derived payment status above; `successful` = paid |
| revenue | `EventOrder` only; see below |

### Capacity: no double counting

A paid checkout creates **one order and one registration for one purchase**. `_seats_taken` counts each active
registration once and adds only the *extra* seats of a multi-quantity confirmed order (an order of 3 holds 3
seats but makes one registration: 1 + 2). Orders are never counted on top of their registration; refunded,
requested-refund and cancelled orders add no extra seats. Waitlist entries that are merely `waiting` hold
nothing. `capacity` is **not** `max_participants`, which only the free-registration form enforces.

### Revenue

`total_revenue` is the sum of `EventOrder.amount` over **paid** orders. `amount` is the order total (unit
price x quantity is applied at checkout), so quantity is not multiplied again. Ticket prices and registration
counts are never used: the old `type=revenue` report multiplied ticket price by active registrations, which
counted unpaid and free registrations and ignored quantity and refunds.

- `refunded_amount` and `pending_refund_amount` sit beside it; refunded, refund-requested, cancelled, pending
  and failed orders are not in `total_revenue`.
- Amounts are strings in the database. They are grouped as text and summed as `Decimal` (rounded once, to two
  places), so a malformed value cannot break a query. Unreadable amounts (`""`, `abc`, `NaN`, `inf`) are
  excluded from every total and counted in `unparseable_orders`; the order itself is still counted in `orders`.
- One currency: the scalar fields describe it. More than one currency: `mixed_currency: true`, the scalar
  amounts are `null` (a sum across currencies is meaningless) and `by_currency` has one line per currency.
- No orders: `0.0` in the event's currency.

The legacy `GET /events/{id}/reports?type=revenue` keeps its shape (`total_revenue`, `by_ticket_type`,
`currency`), now sourced from orders, and adds `mixed_currency` and `by_currency`. With mixed currencies its
headline total is the event's own currency only.

### Read-only

The dashboard never writes, promotes, offers seats or expires anything.

## Export

`GET /{event_id}/registrations/export` keeps `text/csv` and the filename. The first five columns (`id`, `name`,
`email`, `status`, `qr_code`) are unchanged and first; appended: `ticket_type`, `quantity`, `payment_status`,
`order_id`, `amount`, `currency`, `checked_in`, `checked_in_at`, `registered_at`, `answers`, `source` (last, Phase 2.5). It accepts the same
filters as the list (not pagination) and the same access rule. Its role gate now also admits an active
super admin, like the new routes. Text cells starting with `=`, `+`, `-`, `@`, tab or CR are prefixed with an
apostrophe (spreadsheet formula injection).

## Cost

Every request issues a fixed number of statements however large the event is (no per-attendee queries; tests
assert this). Attendees are paired to orders with one ranked join, which the database can hash-join, so cost
grows with (registrations + orders) of that event, not their product. `q` is an unindexed contains-match but
is bounded to one event's rows. If a single event reaches the hundreds of thousands of rows, an index on
`event_orders (event_id, lower(participant_email))` would help; it is not needed at current sizes and is not
added here.

## Not in this phase

Session check-in, walk-in registration, meals, accommodation, analytics beyond the above, certificates,
sponsors, exhibitors, polls, Q&A. Modules are not enforced anywhere.
