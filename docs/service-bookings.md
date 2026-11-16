# Service bookings: API contract

Customers book a Service for a time slot, attend it (in person, virtual or hybrid), cancel it, and get notified. The Enterprise Admin sees and manages every booking of the
enterprise. One booking model serves the customer app and the admin app.

Not in this release (later phase): rescheduling, provider leave and holidays, a provider-availability endpoint, QR check-in, recurring bookings, automatic Zoom / Meet link creation,
and a real payment gateway link (payment is a **status** on the booking, see section 7).

All paths are under `/api/v1`. Every call except `available-slots` needs a bearer token.

## 1. The rules

| Question | Answer |
|---|---|
| New booking: confirmed or approved? | **Auto-confirmed.** A service with `requires_approval: true` leaves it `pending` for the Enterprise Admin to confirm. |
| Who books | A customer books **for themselves**. The Enterprise Admin (or the service's Provider) can book for a customer (`customer_name` / `customer_user_id`, `paid: true` to record the payment). |
| `max_participants` | The capacity of **one time slot** (bookings that start at the same time). A service without it holds one booking per slot. |
| Double booking | A row lock on the service while a booking is made, so two simultaneous requests cannot both take the last seat (one gets 409 `SLOT_FULL`). Plus `Idempotency-Key` and a duplicate check. |
| Provider overlap | A provider cannot be booked twice at overlapping times, **across all their services** (409 `PROVIDER_UNAVAILABLE`). The same service at the same start time is shared (a group session). |
| Slot hold | A paid booking holds its slot for `SERVICE_BOOKING_HOLD_MINUTES` (15) while `unpaid`; when the hold runs out it becomes `expired` and the slot is free again. |
| Cancellation | Inside the free window (or while still `pending`): **instant**. After it: a **request** the Enterprise Admin approves or rejects, or blocked when the service does not allow requests. |
| Refund | Recorded on the booking (`refund_percent`, `refund_amount`, `payment_status`). **No money moves.** |
| Time | Every time is ISO-8601 **with the service's UTC offset** (`2026-11-20T09:00:00+05:30`) plus the IANA `timezone`. The schedule is written in the service's `time_zone` (default Asia/Kolkata). A booking lasts the service's `duration` minutes; slots start every `slot_length` minutes. Stored in UTC. |
| Who sees a booking | Its customer, the Enterprise Admin of its enterprise, its Provider, a Super Admin. Anyone else: **404**. |

## 2. Statuses and transitions

`pending`, `confirmed`, `in_progress`, `completed`, `cancellation_requested`, `cancelled`, `no_show`, `expired`. A customer sees all of them.

| From | Staff can move it to |
|---|---|
| `pending` | `confirmed` (a paid service needs `payment_status: paid` first), `cancelled` |
| `confirmed` | `in_progress` (from 15 min before the start), `completed` and `no_show` (once it has started), `cancelled` |
| `in_progress` | `completed` |
| `cancellation_requested` | decided with `cancellation/approve` or `cancellation/reject` only |
| `completed`, `cancelled`, `no_show`, `expired` | nothing |

A Provider can only start, complete or mark no-show their own bookings. `cancelled` needs a reason. `payment_status`: `not_required`, `unpaid`, `paid`, `refunded`, `partially_refunded`.

## 3. Endpoints

### Customer and public

| | |
|---|---|
| `GET /services/{id}/available-slots` | Public. `date`, or `from` + `to` (max 31 days), calendar dates in the service's zone; optional `timezone` (adds `starts_at_display`), `provider_id`, `delivery_mode`. |
| `POST /service-bookings` | Book. Header `Idempotency-Key` (repeat returns the first booking with 200). |
| `GET /users/me/service-bookings` | My bookings: `status` (comma list), `when=upcoming|past|all`, `page`, `page_size`. |
| `GET /service-bookings/{id}` | One booking (detail below). |
| `POST /service-bookings/{id}/cancel` | `{ "reason" }`: instant cancel or a request (reason of 3+ characters needed for a request). |
| `POST /service-bookings/{id}/pay` | Demo payment: marks my unpaid booking paid. Only where `SERVICE_BOOKING_DEMO_PAYMENT` is on (off in production by default: 403 `PAYMENT_NOT_AVAILABLE`). |
| `GET /service-bookings/{id}/history` | Every change in order. |

### Enterprise Admin (a Provider sees only their own bookings; Super Admin any)

| | |
|---|---|
| `GET /enterprises/{enterprise_id}/service-bookings` | `status`, `service_id`, `provider_id`, `payment_status`, `q` (customer name or e-mail, service, provider), `date_from`, `date_to`, `when=upcoming|past|all`, `page`, `page_size`. Another enterprise's id is a 404. |
| `GET /enterprises/{enterprise_id}/service-bookings/cancellation-requests` | Oldest request first. |
| `PATCH /service-bookings/{id}/status` | `{ "status", "reason", "refund_percent" }` (section 2). |
| `POST /service-bookings/{id}/cancellation/approve` | `{ "reason"?, "refund_percent"? }`: cancels, frees the slot, records the refund (default: the service's `refund_percent_after`). |
| `POST /service-bookings/{id}/cancellation/reject` | `{ "reason" }` required: back to the previous status, the customer is told. |
| `POST /service-bookings/{id}/payment` | `{ "reference"? }`: records the payment (`paid`); a pending booking is confirmed unless the service requires approval. |
| `GET /services/{id}/booking-settings` | The service's booking settings **with the meeting link** (the public catalog never shows it). |

Service form fields (`POST` / `PUT /services`): `delivery_modes` (`in_person`, `virtual`, `hybrid`; a service that never set it is worked out from `delivery_format`), `booking_enabled`,
`requires_approval`, `time_zone`, `cancellation_rules` (`free_cancellation_hours` 24, `refund_percent_before` 100, `refund_percent_after` 0, `allow_request_after_window` true),
`venue_instructions`, `meeting_url`, `meeting_provider`, `meeting_passcode`, `meeting_instructions`. The venue is the service's `location_id` (name, address, lat / lng). The public service answer
also returns `delivery_modes`, `booking_enabled`, `requires_approval`, `time_zone`, `cancellation_rules` and `venue_instructions`, never the meeting fields.

### Create request
```json
{ "service_id": "uuid", "starts_at": "2026-11-20T09:00:00+05:30", "delivery_mode": "in_person", "participants": 1 }
```
`starts_at` must be exactly a slot's `starts_at` from `available-slots`, with an offset (a time without one is a 422). `delivery_mode` is needed only when the service offers several.

## 4. Samples

`available-slots`:
```json
{ "service_id": "uuid", "service_name": "Kids Fitness Program", "time_zone": "Asia/Kolkata", "duration_minutes": 60, "capacity": 5, "delivery_modes": ["in_person"],
  "provider_id": "uuid", "provider_name": "Coach Alex", "bookable": true, "reason": null, "hold_minutes": 15,
  "days": [ { "date": "2026-11-20", "slots": [
    { "starts_at": "2026-11-20T09:00:00+05:30", "ends_at": "2026-11-20T10:00:00+05:30", "starts_at_display": null, "available": true, "available_seats": 5, "capacity": 5, "reason": null },
    { "starts_at": "2026-11-20T10:00:00+05:30", "ends_at": "2026-11-20T11:00:00+05:30", "starts_at_display": null, "available": false, "available_seats": 0, "capacity": 5, "reason": "full" } ] } ] }
```
`reason`: `full`, `provider_busy`, `too_soon` (inside the minimum notice, 60 min), `too_far` (beyond 60 days).

Booking detail, **in_person** (`GET /service-bookings/{id}`, customer):
```json
{ "id": "uuid", "status": "confirmed", "payment_status": "not_required", "service_id": "uuid", "service_name": "Kids Fitness Program", "provider_id": "uuid",
  "provider_name": "Coach Alex", "enterprise_id": "uuid", "enterprise_name": "Spin Health", "delivery_mode": "in_person",
  "starts_at": "2026-11-20T10:00:00+05:30", "ends_at": "2026-11-20T11:00:00+05:30", "timezone": "Asia/Kolkata", "duration_minutes": 60, "participants": 1, "amount": 0, "currency": "INR",
  "venue": { "name": "Spin Health Clinic", "line1": "12 Road No 1", "line2": null, "city": "Hyderabad", "state": null, "postal_code": null, "country": null,
             "lat": 17.38, "lng": 78.48, "phone": null, "instructions": "Reception, 1st floor" },
  "meeting": null,
  "can_cancel": true, "cancel_mode": "instant", "cancel_blocked_reason": null, "can_reschedule": false, "can_join": false, "join_blocked_reason": null, "can_check_in": false,
  "cancellation_policy_summary": "Free cancellation until 24 hours before the start; after that you can ask and the clinic decides", "cancellation_policy": null,
  "hold_expires_at": null, "cancellation": { "requested_at": null, "reason": null, "decision_reason": null, "cancelled_at": null, "cancelled_by_role": null, "refund_percent": null, "refund_amount": null },
  "payment_id": null, "qr_payload": null, "customer": { "user_id": "uuid", "name": "Priya Sharma", "email": "priya@example.com", "phone": null }, "notes": null,
  "history": [ { "action": "created", "from_status": null, "to_status": "confirmed", "actor_role": "customer", "actor_name": null, "reason": null, "at": "2026-11-16T13:05:00+05:30" } ],
  "created_at": "2026-11-16T13:05:00+05:30" }
```
**virtual**: `venue: null` and
```json
"meeting": { "provider": "zoom", "joins_at": "2026-11-20T09:50:00+05:30", "available": false, "url": null, "passcode": null, "instructions": null },
"can_join": false, "join_blocked_reason": "The meeting link opens 10 minutes before the start"
```
From `joins_at` until 30 minutes after the end, a **confirmed** booking has `available: true`, `can_join: true` and `url`, `passcode`, `instructions` filled. Staff always see the link.
**hybrid**: both `venue` and `meeting`; the customer chooses how to attend on the day.

Admin detail adds `allowed_statuses` and `created_by_role`, and `history[].actor_name`.

## 5. Errors

Body `{ "detail": { "message": "...", "code": "...", ... } }`.

| HTTP | `code` | When |
|---|---|---|
| 404 | `BOOKING_NOT_FOUND`, `SERVICE_NOT_FOUND`, `ENTERPRISE_NOT_FOUND` | not found, or not yours / not your enterprise |
| 409 | `SLOT_FULL` (+ `available_seats`) | the slot has no seats for this party |
| 409 | `PROVIDER_UNAVAILABLE` | the provider is booked at an overlapping time |
| 409 | `DUPLICATE_BOOKING` (+ `booking_id`) | the same person already holds that slot |
| 409 | `SERVICE_NOT_BOOKABLE`, `SLOT_NOT_BOOKABLE`, `SLOT_IN_THE_PAST` | service closed / switched off, too soon or too far, passed |
| 422 | `INVALID_STARTS_AT`, `SLOT_NOT_OFFERED`, `DELIVERY_MODE_REQUIRED`, `DELIVERY_MODE_NOT_OFFERED`, `PROVIDER_NOT_FOR_SERVICE`, `CUSTOMER_REQUIRED`, `REASON_REQUIRED` | bad request |
| 403 | `BOOK_FOR_SELF_ONLY`, `NOT_YOUR_SERVICE`, `NOT_ALLOWED`, `PAYMENT_NOT_AVAILABLE` | not allowed |
| 409 | `CANCEL_NOT_ALLOWED` (+ `policy`) | the policy or the status blocks a cancellation: show `message` |
| 409 | `INVALID_TRANSITION` (+ `allowed`), `PAYMENT_REQUIRED`, `NOT_STARTED`, `DECIDE_THE_REQUEST`, `NO_REQUEST` | status rules |
| 409 | `HOLD_EXPIRED`, `NOTHING_TO_PAY`, `BOOKING_CLOSED` | payment |

## 6. Notifications

Platform push + inbox (`GET /users/me/notifications`). `data` is a flat map of strings: `category`, `booking_id`, `service_id`, `service_name`, `entity_type: "service_booking"`, `entity_id` (= booking id),
`starts_at` (ISO with offset), `delivery_mode`, `status`; a free-text `reason` is in the inbox only, never in the push. Deep link: the booking detail (reminder: with Join / Check-in focus).

| `category` | To | When |
|---|---|---|
| `service_booking_confirmed` | customer | confirmed (automatically, after payment, or by an admin) |
| `service_booking_pending_approval` | customer | waits for an admin |
| `service_booking_cancelled` | customer (and staff when the customer cancelled) | cancelled, or a request approved |
| `service_booking_cancellation_rejected` | customer | the admin did not approve (reason in the inbox) |
| `service_booking_reminder` | customer | 24 h and 1 h before the start (`kind: "24h"` / `"1h"`), confirmed bookings only |
| `service_booking_completed`, `service_booking_no_show` | customer | marked by staff |
| `service_booking_new`, `service_booking_cancellation_requested` | provider and the enterprise's admins | a booking needs approval; a customer asked to cancel |

An unpaid booking sends nothing until it is paid. Reminders: `SERVICE_BOOKING_REMINDERS_ENABLED` (on in production), `SERVICE_BOOKING_REMINDER_HOURS` (`24,1`).

## 7. Payment (status only)

There is no link to the payments module yet: `payment_id` is always null. A paid service starts `unpaid` with a hold; the Enterprise Admin records the payment (`POST /payment`), or in
non-production the customer can press the demo `POST /pay`. Cancelling a paid booking records the refund percent and amount and sets `payment_status` to `refunded` (100%) or `partially_refunded`:
inside the free window the service's `refund_percent_before`; an approved late request the service's `refund_percent_after` (or the admin's `refund_percent`); a cancellation by the clinic 100% unless it says otherwise.
Nothing is paid out or charged. Connecting the payments module later means a `service_booking` payment type that sets `payment_status` the same way.

## 8. Migration, settings, rollout

* Migration `p5a8c1e4f7b2` (after `n3e6a9c2d7f4`): ten columns on `services` (all defaulted: existing services are bookable with the defaults, in Asia/Kolkata, in person) and the tables `service_bookings`
  and `service_booking_events`. The repository has other open migration heads that are not part of this work: check `alembic heads` first.
* Settings: `SERVICE_BOOKING_HOLD_MINUTES` 15, `SERVICE_BOOKING_MIN_NOTICE_MINUTES` 60, `SERVICE_BOOKING_MAX_ADVANCE_DAYS` 60, `SERVICE_BOOKING_JOIN_MINUTES_BEFORE` 10, `SERVICE_BOOKING_DEMO_PAYMENT`,
  `SERVICE_BOOKING_REMINDERS_ENABLED`, `SERVICE_BOOKING_REMINDER_HOURS`, `SERVICE_BOOKING_REMINDER_INTERVAL_SECONDS`.
* Rollback: `alembic downgrade n3e6a9c2d7f4` drops the tables and columns (bookings are lost).
* Tested: `tests/test_service_bookings.py` (37 tests, real routes on an in-memory SQLite database): slots, capacity, provider overlap, holds, payment status, approval, cancellation, status
  rules, isolation, history, meeting and venue, notifications, reminders. Not tested: PostgreSQL (the row lock that serialises simultaneous bookings is a no-op on SQLite, so concurrency was
  not exercised), real push delivery, the identity service lookup of the enterprise's admins (stubbed).
