# Event walk-in registration: backend contract (Phase 2.5)

An organizer registers, and by default checks in, an attendee at the venue. A walk-in is a **normal registration**
(`EventRegistration`, confirmed, its own QR value, in the normal attendee list and dashboard); the only difference
is `registration_source = "walk_in"`. There is no walk-in table and no second ticket format.

Online registration, checkout, the waitlist, event-level check-in, session attendance, meeting links, `status`,
`lifecycle_state`, `event_type` and `modules` behave exactly as before.

## Endpoint

`POST /api/v1/events/{event_id}/walk-in` → `201` `EventWalkInResponse`. Organizer only.

| Caller | Result |
|---|---|
| owning tenant `admin` / `provider` | allowed |
| active platform `super_admin` | allowed (any tenant) |
| customer | `403`, even for their own email |
| foreign tenant staff, inactive super admin | `403` |
| no token | `401` |
| unknown or deleted event | `404` |

Same dependency as the other organizer routes (`require_event_staff`, on top of Phase 2.1 event ownership). The
event always comes from the path; the request cannot name a tenant.

### Request `EventWalkInRequest`

| Field | Notes |
|---|---|
| `participant_name` | required, trimmed, 1..255 |
| `participant_email` | required, must look like an email; stored trimmed and **lower-cased**; the key of the duplicate rule |
| `ticket_type_id` | one of THIS event's ticket types. Required when a **paid** event has ticket types; optional otherwise, validated when sent |
| `custom_fields` | answers to the event's registration form, keyed by form field id (see below) |
| `meal_selections` | optional list of meal option ids (Phase 2.6); validated by the same validator as online registration (`422` if meals are off, unknown, retired or duplicated) |
| `accommodation_selections` | optional list of accommodation option ids (Phase 2.7); validated by the same validator as online registration (`422` if accommodation is off, unknown, retired or duplicated) |
| `check_in` | default **true**: register and admit in one step (the usual venue flow) |
| `session_id` | optional: also check the attendee in to this **one** session (Phase 2.4) |

The first four fields are named and typed exactly like `EventRegistrationCreate`, so a client can reuse its
registration payload minus the group fields. **Unknown fields are refused (`422`)**: `tenant_id`,
`enterprise_id`, `event_id`, `registration_source`, `status`, `qr_code`, `payment_status`, `amount`, `order_id`,
`payment_provider`, payment ids, `group_size`, `group_members`, `checked_in_by`. There is no phone field because
the registration has no phone column; a phone question can be a registration-form field. The email is required
because the registration and the one-active-registration rule are keyed on it (a walk-in without an email is not
supported).

### Response `EventWalkInResponse`

| Field | Content |
|---|---|
| `message` | e.g. `Walk-in registered and checked in` |
| `registration` | the Phase 2.3 `EventAttendeeResponse`, exactly as the list shows it (`registration_source: walk_in`, `payment_status`, `order_id`, `session_attendance`, `custom_answers`, ...) |
| `ticket` | `registration_reference` = `qr_code` (the value existing check-in scans), `qr_image_path` (existing `GET .../registrations/{reg_id}/qr`), ticket type |
| `payment` | `required`, `status` (`free` / `paid` / `pending`), `amount`, `currency`, `order_id`, `note` |
| `check_in` | `requested`, `performed`, `checked_in_at`, `reason` (`payment_pending` when a requested check-in was not performed) |
| `session_check_in` | `requested`, `session_id`, `performed`, `reason` |

## Eligibility

In this order, before anything is written: caller owns the event; the event exists and is not deleted;
`status` is `published` (`cancelled`/`completed`/`archived`/`suspended` are "closed", everything else "not open":
the same two checks online registration and checkout make); `lifecycle_state` is not `finished` (a published event
whose end has passed takes no walk-ins; upcoming and ongoing do); the session (if sent) is one of this event's own
sessions with an id; the ticket type exists on this event; the price is usable; the registration-form answers are valid.

**The public registration window is not applied** (`registration_open_at`, `registration_close_at`,
`registration_cutoff`): the organizer is at the venue. Status, lifecycle, capacity, ticket, duplicate, payment and
ownership rules all still are. This is the only bypass.

## Capacity

One seat per walk-in. It is rejected, never waitlisted, when any of these would be exceeded:

1. **Event capacity**: `_seats_taken` (the Phase 2.1 accounting: each seat once, multi-quantity confirmed orders
   included) **plus live waitlist payment offers** (`payment_pending`, not past `payment_offer_expires_at`; the exact
   rule promotion and the dashboard's `seats_reserved` use). A seat held for someone who is paying is not given away.
2. **Ticket-type capacity** (`_seats_taken` for that ticket), as checkout applies.
3. **`max_participants`** (active registrations), as free registration applies.

Unparseable or empty capacity is unlimited; `0` is full. The error is `400`:
`Event is at full capacity (N participants). Walk-in registrations do not use the waitlist.` A walk-in never
creates, promotes, offers or otherwise touches a waitlist entry, and never triggers a promotion.

## Duplicates

One active (`confirmed`/`attended`) registration per event and lower-cased email, checked in the application
(`409`, the same status and message as online registration) and by the existing `uq_event_reg_active` index (a
lost race is also a `409`). A cancelled registration does not block re-registration, as online.

## Free and paid

| Event | What is created | Payment |
|---|---|---|
| free | a confirmed registration | `free`, no order |
| paid, ticket costs nothing | registration + order (`confirmed`/`confirmed`, amount `0.0`) | `paid`: nothing was owed |
| **paid, priced ticket** | registration + order (`confirmed` / **`pending`**, amount = the ticket price) | **`pending`**; not admitted |

Price and currency come from the checkout's own rules (`_resolve_ticket`, `_ticket_effective_price`: early-bird,
promo, ticket currency, event price); nothing is recomputed.

**Limitation, stated plainly.** The backend has no offline/manual payment mode. Its checkout is a stub that marks
every order confirmed with no gateway, and no endpoint moves a payment from `pending` to `confirmed`. A walk-in
therefore never records a payment it did not receive: no fake provider, gateway id or "paid". For a priced
ticket it holds the seat with a pending order (`payment_provider` NULL, not "marketplace"), shows `pending` in the
attendee list and dashboard, and adds nothing to revenue. A pending walk-in can be abandoned with the existing cancel
flow (releases the seat). Marking it paid needs an offline-payment mechanism that does not exist yet; that is a
product decision, not something this phase invents. The generic `POST /check-in` has never verified payment, so
staff can still admit a pending attendee explicitly; the payment stays `pending` in every report.

## Check-in

`check_in: true` (default) runs the existing `check_in_service`, staged in the same transaction (a defaulted
`commit=False` keyword was added to it; its default behaviour is unchanged): same rules, same fields
(`status: attended`, `checked_in_at`, `checked_in_by` = the operator), same `check_in` audit row (`method:
walk_in`). It is **not performed while the payment is pending**; the response says `reason: payment_pending`
and the registration is still created. `check_in: false` leaves the attendee `confirmed`.

## Sessions

`session_id` runs the Phase 2.4 `check_in_session_service` (also `commit=False`) for that one session, giving one
`event_session_attendance` row and a `session_check_in` audit row. It is independent of `check_in` (session
attendance never needs event-level check-in) and is also withheld while the payment is pending. No other session is
touched, nothing is ever checked in to "all sessions", and the legacy `event_registrations.session_id` is not written.

## Registration-form answers

The existing registration form (`GET /events/{id}/registration-form`: the event's pinned form version, or the
legacy default) is validated with the existing form validator, `validate_custom_values`: unknown field ids,
wrong types, invalid select/multi-select values, `min_length` / `max_length` / `pattern` and required fields are
rejected (`400`, its messages, e.g. `Required custom field missing: T-shirt size`). Two additions the existing
validator does not cover: it skips its required check when no answers are sent, so that is checked here; and the
bookkeeping keys of group registration (`group_size`, `group_members`, `group_leader`) are refused. Only answers
to the form's own fields are stored, so nothing arbitrary is saved. An event with no form takes no answers.
(Online registration validates none of this today; the walk-in is stricter, on purpose.)

## Atomicity and audit

The registration, its order, the `walk_in_registration` audit row, the event check-in and the session check-in are
staged in **one transaction and committed once**. Any failure (validation, capacity, a check-in refusing, a failed
commit) rolls all of it back: no registration, no order, no orphan audit row.

`walk_in_registration` (`before` null; `after`: registration id, email, name, ticket type, source, status,
payment status, order id, amount, whether check-in was requested, session id), `changed_by` = the operator's user
id. When they happen: `check_in` and `session_check_in` rows, in that order. The confirmation notification (with the
QR) is best-effort after the commit and is not sent while the payment is pending.

## Database

One nullable column, `event_registrations.registration_source VARCHAR(20)` (Alembic `fe61c574e6f5`, parent
`1e40e81e2937`, the Phase 2.4 head; still four heads, no merge). No default, no backfill, no index, no `registered_by`
(the operator is on the audit row). Every existing row and every online registration/checkout leaves it NULL, which
the API reads as `online`.

## Attendee management and dashboard (additive)

- `registration_source` (`online` | `walk_in`) on every attendee response (list, detail, walk-in).
- `GET /attendees?source=walk_in|online` (`online` includes NULL) and the same filter on `/registrations/export`;
  the CSV gets a final `source` column (all earlier columns unchanged).
- Dashboard `registrations` gets `online` and `walk_in` (all statuses, summing to `total`), from the same grouped query.

## Not in this phase

Walk-in without an email, group walk-ins, an offline/manual payment mode or a way to mark a pending walk-in
paid, refunds of pending orders, a walk-in for a waitlisted person.
