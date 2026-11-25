# Service & Service Booking: gap report against "Revised Booking & Scheduling Management"

> **Update (approval removal confirmed by the client).** Done locally, not committed or deployed:
> * New bookings never wait for approval (`requires_approval` is still accepted and stored, with no effect). Free: confirmed at once. Paid: pending payment, confirmed by the payment.
> * Approval bookings made before the change are never changed automatically: `stage: legacy_pending_approval`, `GET /enterprises/{id}/service-bookings?stage=legacy_pending_approval`, counted in `/summary`; an admin confirms or cancels each.
> * `reference` (SB-XXXXXXXXXX), `stage`, `source` on every booking; filters `source`, `delivery_mode`, `stage`; search by phone and reference; `GET /enterprises/{id}/service-bookings/summary`.
> * Per-service `min_notice_minutes`, `max_advance_days`, `blocked_periods` (slot reason `blocked`, 409 `SLOT_BLOCKED`, manual bookings too).
> * 409 `CAPACITY_BELOW_BOOKINGS` when capacity is lowered under the seats already booked.
> * Notifications: provider + admins on a new confirmed booking (manual: provider only, customer told who booked); provider on a merchant cancellation; customer on payment expiry.
> * `cancellation.cancelled_by` (customer | merchant | system) and `cancellation.refund_state: recorded_not_processed`.
> * Fix: `expire_stale_holds` committed inside booking creation and released the service row lock; it now runs before the lock.
> * `refund_status` (pending | processing | completed | failed, null = none owed) independent of `status` and `payment_status`; a cancellation no longer sets payment_status to refunded. `completed` only from a gateway confirmation (not built yet). Merchant refund default is the setting SERVICE_BOOKING_MERCHANT_REFUND_PERCENT (policy pending).
> * `POST /services/{id}/availability-impact`: the upcoming bookings and payment holds a planned block / schedule / capacity change would touch (reasons blocked, outside_schedule, over_capacity). Nothing is saved.
> * Migration `t9e2a5c8f1b3d` (also adds refund_status; old refunded rows get refund_status pending, payment_status untouched). Tests: 54 service-booking tests pass (SQLite).
> * Provider: `GET /service-bookings/assigned` (own appointments only, own tenant, with venue/meeting/allowed_statuses). Provider services: existing `GET /services/` is already provider-scoped server-side.
> * Super Admin (read only): `GET /service-bookings/admin`, `/admin/summary`, `/admin/service-counts?service_ids=`. No migration. Tests: 59 service-booking tests pass.
> Still open: rescheduling, payments-module integration (purchase type `service_booking` in `payment_payables`), per-provider working hours, affected-bookings warning.

Audit of the backend as it is in the working tree, against §§1–10, 28–31 and the Service parts of §§32–41 of the revised document. Nothing here is deployed; staging was not checked. Tests are SQLite only (no PostgreSQL, no payment gateway, no FCM).

Legend: **Done** = already implemented · **Partial** = some of it · **Missing** · **Change** = exists but must be modified · **Decision** = needs a client/business answer first.

## 0. Approval removal (§1.2): not confirmed, nothing changed

Nobody has told me the client approved removing approval, and §1.2 of the document says it needs confirmation. So: `requires_approval`, Approve/Decline (`PATCH /service-bookings/{id}/status`), the approval notifications and every historical approval record are untouched. **Status: Decision (blocked on the client).**

What the backend already does, so the change is mostly configuration:

| Flow | Today with `requires_approval: false` (the default) |
|---|---|
| Free service | booked → `confirmed` at once |
| Paid service | `pending` + `payment_status: unpaid` + 15-minute hold → payment recorded → `confirmed` |

If the client approves:
1. Stop new approval bookings: ignore/force `requires_approval: false` for new bookings (API field kept, so nothing breaks).
2. Do **not** touch existing `pending` approval bookings. Keep Approve/Decline working for them until they are cleared, and report them (a list of pending-approval bookings with availability/capacity/payment checked) so a person decides each one. They are never auto-confirmed (§41).
3. Remove only the *creation* of `service_booking_pending_approval` / `service_booking_new` notifications; keep the categories so old notifications still render.
4. No migration is needed for step 1–3; a data report is, for step 2.

## 1. Requirement-by-requirement

### §2 Service create/edit and availability

| Requirement | Status | Notes / endpoint |
|---|---|---|
| Price, currency, duration, provider | Done | `POST/PATCH /services` |
| In-person / Virtual / Hybrid (`delivery_modes`) | Done | Meeting URL not required before activation: **Change** (validation), needs your yes |
| Structured cancellation rules | Done | `cancellation_rules`. **Change**: presets (**Decision**: numbers), `0` hours wording for No Refund, one displayed policy (the generated summary) |
| Weekly working days, time ranges, slot length, capacity | Done | `availability_schedule`, `max_participants` |
| Holidays, blocked dates, unavailable time ranges | **Missing** | Needs a per-service blocked-periods list |
| Min / max advance booking notice | **Partial** | Global only (`SERVICE_BOOKING_MIN_NOTICE_MINUTES` 60, `SERVICE_BOOKING_MAX_ADVANCE_DAYS` 60). Needs per-service values |
| Provider availability across services | **Partial** | Overlap across all of a provider's services is blocked (`provider_busy`). No per-provider working hours |
| Existing bookings kept when schedule/provider changes | **Partial** | Kept by design (a booking stores its own time and provider). Missing: the warning and a list of affected bookings |
| Capacity cannot drop below existing bookings | **Missing** | No check today |
| Inactive service: no new bookings, existing kept | Done | |

### §3–4 Customer booking and automatic confirmation

| Requirement | Status | Notes |
|---|---|---|
| Slots, validation, revalidation at booking, past dates | Done | `GET /services/{id}/available-slots`, `POST /service-bookings` |
| Duplicate submissions | Done | Optional `Idempotency-Key`, plus `DUPLICATE_BOOKING` |
| Final-slot race | Partial | Service row is locked (`FOR UPDATE`), which SQLite ignores: **not tested on PostgreSQL** |
| Unique booking **reference** | **Missing** | Only the UUID id exists |
| Free service auto-confirms | Done | |
| Paid: hold, Pending Payment, expiry | Partial | Hold and expiry done (status `pending` + `unpaid`). No notification on expiry, no payment-deadline reminder |
| Paid: gateway payment, verification, retry, reconciliation, receipt | **Missing** | See §5 below |

### §5 Admin booking management

| Requirement | Status | Notes |
|---|---|---|
| List, detail, history, pagination | Done | `GET /enterprises/{id}/service-bookings`, `GET /service-bookings/{id}` |
| Customer, provider, status, payment status | Done | |
| Booking source (Customer / Manual) | **Partial** | `created_by_role` only in the detail; not in the list, not filterable |
| Filters | **Partial** | Have: status, service, provider, payment status, search (name/email/service/provider), dates, upcoming/past. Missing: delivery mode, booking source, customer phone |
| Merchant cancellation, mandatory reason | Done | `PATCH /status` `cancelled` (reason 3+ chars), capacity released |
| Provider told of a merchant cancellation | **Change** | Only the customer is told today |
| New confirmed booking told to admin and provider | **Change** | Only the customer is told for a `confirmed` booking |
| Attendance actions with time checks | Done | Start 15 min before, complete / no-show after start |
| Calendar sync status | Missing | Calendar integration is out of this scope |

### §6–7 Cancellations

| Requirement | Status | Notes |
|---|---|---|
| Customer cancel by policy: instant / request / blocked | Done | `POST /{id}/cancel`, `can_cancel`, `cancel_blocked_reason` |
| Late request, admin approve/reject | Done | `/cancellation/approve`, `/cancellation/reject` |
| Booking stays active until a request is decided | Done | |
| Cancel one participant of a group booking | Missing | A booking is cancelled as a whole |
| Refund initiated / processing / completed, refund failure | **Missing** | Refund is a recorded percentage only; `refunded` means "owed" |
| Refund notifications | Missing | |

### §8 Rescheduling: **Missing entirely** (`can_reschedule` is always false)

Proposed design (needs a migration):
- New table `service_booking_reschedules`: `id`, `booking_id`, `requested_by_role` (customer | admin), `requested_by`, `new_starts_at`, `reason`, `status` (requested | proposed | accepted | declined | expired | withdrawn), `hold_expires_at`, `decided_by`, `decided_at`, timestamps. One open request per booking (partial unique index).
- Customer: `POST /service-bookings/{id}/reschedule-requests` `{new_starts_at, reason}`. Admin: `POST /service-bookings/{id}/reschedule-proposals` `{new_starts_at, reason}`.
- `GET /service-bookings/{id}/reschedules`; `POST /service-bookings/reschedules/{rid}/accept | decline | withdraw` (admin decides a customer request; the customer decides an admin proposal).
- The proposed slot is held (counted by the capacity engine) until `hold_expires_at` (new setting, configurable); the original stays `confirmed` until accept. Accepting moves `starts_at` / `ends_at` on the **same booking id** and records the previous and new schedule in the history. Stale acceptance is `409`.
- Reminders already key on `starts_at`, so they reset after a move. Provider and customer are notified.
- Price change during a reschedule needs the payments integration; phase 1 would allow same-price moves only.
- **Decision**: customer-requested reschedule reviewed by the admin, or direct within policy (the document mentions both).

### §9 Manual booking

| Requirement | Status | Notes |
|---|---|---|
| Free manual booking, creator and source recorded, same engine, duplicates blocked | Done | |
| Select a **registered** customer, consent recorded, no account → register first | **Change / Decision** | Today a walk-in with only a name is accepted. No customer search endpoint, no consent field |
| Paid manual booking restricted | **Decision** | The backend still allows it for an admin (`paid: true`, or unpaid with a 15-minute hold). Say if it should be refused |
| Provider creating manual bookings | **Decision** | The document describes manual booking as an Enterprise Admin feature; a Provider can still create them today |
| Provider `paid: true` bypass | **Fixed** | See section 2 |

### §10 Provider

| Requirement | Status | Notes |
|---|---|---|
| Own services and bookings only | Done | |
| Upcoming schedule | Partial | `when=upcoming` list; no day/week schedule view |
| Start / complete / no-show with time checks | Done | |
| Cannot modify payment, cancel or reschedule | Done | Payment endpoint blocks providers; `paid: true` bypass now closed; `cancelled` is not a provider target |
| Provider notifications: new confirmed, cancelled, reminders | **Change** | Not sent today (the provider only gets cancellation requests and customer cancellations) |

### §28–31 Lifecycle, capacity, payments, history

| Requirement | Status | Notes |
|---|---|---|
| Statuses | Partial | Have pending, confirmed, in_progress, completed, cancelled, no_show, expired, cancellation_requested. Missing: Rescheduled (and its sub-statuses). "Cancelled by Customer/Merchant" is `cancelled` + `cancelled_by_role` |
| Payment statuses (pending, failed, verification pending, paid, not required, refund pending/failed, refunded) | **Change** | Today: not_required, unpaid, paid, refunded, partially_refunded |
| Atomic capacity, same engine for all, expired holds release | Done (PostgreSQL untested) | |
| Price, discount, tax, exchange rate retained | Missing | Only `amount` and `currency` |
| Booking history with actor, role, time, reason | Done | |
| Payment/refund references in history | **Change** | Stored in the event detail but not returned by the API |

### §32–39 Notifications, reminders, edge cases

| Item | Status |
|---|---|
| Booking confirmed, admin cancelled, late-cancellation outcome | Done |
| Appointment reminders: 24 h and 1 h, confirmed only, no duplicates, no past ones | Done (timings are one global setting, `SERVICE_BOOKING_REMINDER_HOURS`) |
| Payment expired, payment deadline approaching, delayed payment verified | Missing |
| Reschedule proposed / accepted / declined / expired | Missing (depends on §8) |
| Meeting details changed | Missing |
| Manual-booking wording ("booked by <enterprise>") | Change (small) |
| Provider notifications; Enterprise Admin "new confirmed booking" | Change |
| Notifications only after the transaction, delivery failure never reverses a booking | Done |
| Edge cases: double tap, overlapping provider, past date, inactive service, no-show before start | Done |
| Edge cases: capacity reduction, blocked date over a booked date, stale reschedule, payment after expiry, refund failure | Missing |

### §5 (your list) Paid bookings through Commerce: **Missing; the largest item**

Service bookings are not connected to the payments module (`payment_id` is always null). Required:
- A booking becomes a purchase (`purchase_type: "service_booking"`) like a cart order; mobile initiates payment, the gateway callback confirms the booking through the existing payment hooks; the booking is confirmed only on verified payment.
- Idempotent callbacks, retry within the hold, "verification pending" state, late payment after expiry reconciled and refunded, never overbooked.
- Receipt/invoice from the commerce module; refunds through `refund_service` with the initiated / processing / completed lifecycle and notifications.
- Migration: booking `payment_id`, widened payment-status values, refund fields.
- **Needs decisions**: which gateway, tax/discount/currency rules, and whether this is in the first release. The demo `/pay` stays off in production meanwhile.

## 2. What was changed

**Phase 0 (done, tested, not deployed):** a Provider can no longer create a booking with `paid: true`. It answers `403 PROVIDER_CANNOT_RECORD_PAYMENT` and creates nothing. A provider's other manual bookings behave as before. Test: `test_a_provider_cannot_mark_a_booking_paid_when_creating_it`. 40 service-booking tests pass on SQLite.

## 3. Proposed order

| Phase | Work | Migration | Needs a decision? |
|---|---|---|---|
| 1 | Notify the provider and admin of a new confirmed booking and the provider of an admin cancellation; payment-expired notification; booking reference; booking source in the list plus source/delivery-mode/phone filters; return payment/refund references in history; block capacity reduction below bookings | `booking_reference` column | No |
| 2 | Per-service min/max notice, blocked dates and time ranges, "affected bookings" warning, meeting-URL validation, cancellation presets and the No Refund wording | per-service notice columns and a blocked-periods column | Presets, URL rule |
| 3 | Rescheduling (design above) | new reschedules table | Direct vs reviewed customer reschedule |
| 4 | Payments integration, refund lifecycle, receipts | payment link and refund columns | Gateway, tax, release scope |
| 5 | Approval removal: stop creating approval bookings, controlled report of existing ones | none | **Client approval** |
| — | Calendar sync and auto-generated meeting links | — | Out of this scope |

## 4. Ready for frontend now

- `POST /service-bookings` for a Provider with `paid: true` now returns 403 (error code `PROVIDER_CANNOT_RECORD_PAYMENT`).
- Nothing else changed; all existing contracts are intact.
