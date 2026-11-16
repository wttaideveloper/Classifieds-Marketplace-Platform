# Unified Service Booking — review contract (not a release)

Use **service-booking-review.openapi.json** in Swagger Editor for both Mobile and Enterprise Admin. Paths include `/api/v1`. OpenAPI examples contain full booking bodies; examples below are field excerpts. No change to the public base URL, Service CRUD paths, or authentication. This is the proposed target, not evidence of deployment.

| Area | In the checkout today | Proposed change requiring implementation |
|---|---|---|
| Booking | One booking + history model, customer and enterprise lists, assigned provider, holds and slot checks | Serialize cross-Service provider bookings, validate active identity, bind idempotency keys to payloads |
| Money | Local status, demo pay / staff payment recording; no real payment relationship | Booking is a payable; actual payment, invoice, refund and credit note determine financial state |
| Attendance | Staff lifecycle transitions; manual meeting data on detail | Authorized join resource; atomic reschedule and provider availability. No customer check-in API yet |
| Banner | Nullable string on Service CRUD; shared upload with public download | Tenant-owned image upload and asset validation before assignment |
| Cancellation | Structured rules with defaults, optional staff percentage override | Snapshot terms; legacy text goes to review; no unrestricted percentage override |

## Create, pay and retry

Use POST /service-bookings with an Idempotency-Key and one of the three named request/response pairs in Swagger: free in-person, paid virtual, free hybrid. Manual creation uses the same route with customer_user_id and optional contact details; an admin may act only within their enterprise. Customer identity comes from auth. Never accept a client-supplied paid flag as settlement.

* Free, no approval: 201 confirmed / not_required. Free + approval: 201 pending / not_required and reserves seats until decided. Paid: 201 pending / unpaid, hold_expires_at set. POST /payments with `{"purchase_type":"service_booking","purchase_id":"550e8400-e29b-41d4-a716-446655440002"}`. Use the returned payment action and existing verification flow. Prices, tax and currency come from the server. One paid booking is one payable, no cart order.
* Payment succeeds before expiry: confirmed unless approval required; otherwise pending / paid with no expiring payment hold. An admin cannot confirm an unpaid booking. A failed payment releases its hold: booking expired, payment_status failed. Example after failure: `{"status":"expired","payment_status":"failed","hold_expires_at":null,"can_join":false}`. Abandoned holds release at the deadline even if the cleanup job is late. Default 15 minutes, configurable.
* Expiry and capture must serialize on the booking row. Confirm only if the reservation still owns capacity; an expired reservation never displaces a later customer. A late capture is reconciled and reversed/refunded using a durable payment-linked operation; never fake a successful refund. Duplicate callbacks do not duplicate credits, notifications or capacity changes.
* Require a key per create/reschedule. Store key, normalized request fingerprint and booking ID atomically with the write, scoped to actor, tenant and operation. Same request returns 200 and the same booking; changed body returns 409 IDEMPOTENCY_CONFLICT. Retrying a failed/expired booking requires a fresh availability check and key.
* Reuse payment visibility, invoice generation, refund decisions, credit notes and tax reversal. Expose nullable payment_id, invoice_id, refund_id, refund_status; do not expose other customers' transactions. External full refund completion cancels an active booking and releases its seats once. Preserve completed/no_show history, record financial reconciliation without releasing historical capacity. Partial refunds change money, not seats. Transitions and downstream effects need durable, uniquely keyed reconciliation; no gateway replay on a generic timeout.

## Availability, identity and time

max_participants is seats per slot (default 1), not concurrent bookings per provider. Count participants in confirmed, in-progress, pending approval, unexpired payment holds and cancellation requests. Release on cancellation or expiry. Read results are advisory. Serialize writers on a stable provider identity across Services as well as the Service, including manual bookings and reschedules; recheck overlaps and remaining seats within the committing transaction. Half-open intervals [start,end) allow back-to-back appointments. Share a group slot only for the same Service and identical interval. Read GET /services/{id}/available-slots for service slots; proposed scoped provider availability also requires service_id so duration, mode and capacity are unambiguous.

provider_user_id is the assigned internal application user UUID; provider_id on the booking refers to that same identity. Verify active authorized membership of the Service tenant through Identity on assignment and booking; reject mismatches and fail closed if membership cannot be established. Historical display name is snapshotted; identity changes must not confer access to earlier bookings. Recheck active staff membership when serving staff routes. No Group Chat registry lookup.

The service's duration (minutes) determines elapsed appointment length. availability_schedule is local wall time; slot_length is the step, not the duration. Use IANA time_zone, default Asia/Kolkata for existing Services. Return explicit ISO offsets and zone, store UTC instants. Proposed DST policy: skip nonexistent starts; return both distinct offsets for ambiguous starts, provided each duration fits the opening interval. Reject offsetless timestamps, invalid zones, nonexistent offered slots and out-of-range intervals. Historical appointments keep their original instants.

## Actions, credentials and history

| Actor | Scope | Actions |
|---|---|---|
| Customer | Own booking only | Create, read, history, cancel/request, pay, eligible join and proposed reschedule |
| Assigned active provider | Assigned booking in own tenant | Read, history, start, complete, mark no-show, see authorized meeting settings. No manual creation, confirmation, cancellation decisions, refund or reschedule |
| Enterprise Admin | Own enterprise within tenant | Manual create, confirm paid/free pending booking, cancel, decide requests, reschedule, manage lifecycle, read history and provider availability |
| Super Admin | Existing platform scope | Same management actions under existing platform permission checks; no special refund-amount override |

All statuses are visible to the owning customer. Server returns allowed_actions and eligibility flags. No client writes payment status. pending → confirmed after required gates; unpaid pending → expired on failed/expired hold. pending/confirmed → cancelled or cancellation_requested by policy. Request approval → cancelled, rejection → previous pending/confirmed with original reservation intact. Confirmed → in_progress from 15 minutes before start; confirmed → completed/no_show after start; in_progress → completed. Assigned providers and administrators may perform attendance transitions. Completion, no-show, cancellation and expiry are terminal for scheduling. Store actor, role, UTC transition time, old/new status, reason and decision timestamp; require reason for staff cancellation or rejection and for customer cancellation requests. History is append-only. Other tenant/customer access returns 404; authenticated but forbidden action returns 403; missing auth returns 401.

In-person: venue/address, appointment time, provider and reception instructions. Virtual: provider + joins_at before opening; URL/passcode/instructions withheld until authorized. Hybrid: both, no separate attendance-mode choice at booking; select hybrid when offered, choose on the day. Proposed join resource returns credentials only for authorized confirmed/in-progress participants during the join window (default ten minutes before through thirty minutes after the appointment). Cancelled/expired bookings cannot join. Keep staff management access separate. Do not include credentials in public Service detail, pushes, previews or audit text. Manual links initially; retain meeting provider and optional internal integration reference so Zoom/Meet/Teams adapters can attach real meetings later. No external meeting is created by this proposal.

## Policy, rescheduling and samples

Snapshot terms on create. If structured cancellation_rules exist, apply free_cancellation_hours and the before/after percentages; when late, allow a request only if the rules permit it. Otherwise return CANCEL_NOT_ALLOWED and the displayed reason. A legacy nonempty cancellation_policy without structured rules is shown verbatim and routed for manual interpretation; do not infer percentages or promise an automatic refund. If neither exists, display the proposed default before booking (24 hours, 100% before, late review with 0% after). No silent retroactive policy changes. Policy-approved refund amount is capped by the remaining captured balance; idempotency key derives from booking and cancellation decision. A cancelled booking can have a pending or failed refund; release scheduling capacity on accepted cancellation, report settlement separately.

Example late cancellation: POST /service-bookings/{id}/cancel `{"reason":"Unable to attend"}` → 200 `{"status":"cancellation_requested","can_join":false}`. Admin POST .../cancellation/reject `{"reason":"Appointment has already started"}` restores the prior state and records time/reason. POST .../cancellation/approve `{"reason":"Policy permits a partial refund"}` → 200 cancelled with refund_id and actual refund_status when a refund exists. No refund_percent input. Use policy snapshot rather than a new admin-selected amount.

Proposed POST .../reschedule (customer/authorized admin): Idempotency-Key + `{"starts_at":"2026-11-21T09:00:00+05:30"}`. Same Service, provider, participants, price and mode; no price-changing transfer. Only before start on a confirmed or approval-pending booking, no unpaid or cancellation-requested booking. Lock old and new allocation in deterministic order; validate then atomically swap. 409 leaves the old booking untouched. Keep booking ID, payment links and history; record both instants and notify. Until implemented return can_reschedule=false and hide the action. Advanced attendance/QR, provider leave, holiday overrides and recurring appointments deferred. No simulated attendance endpoint.

| Example | Response |
|---|---|
| Slot full | 409 `{"detail":{"code":"SLOT_FULL","message":"No seats remain for this party","available_seats":0}}` |
| Conflicting provider | 409 `{"detail":{"code":"PROVIDER_UNAVAILABLE","message":"Provider has an overlapping appointment"}}` |
| Another customer's detail | 404 `{"detail":{"code":"BOOKING_NOT_FOUND","message":"Booking not found"}}` |
| Invalid offset or unoffered time | 422 INVALID_STARTS_AT / SLOT_NOT_OFFERED; normal body validation retains FastAPI's error list |
| Capture failed | Payment API returns its gateway failure/status; re-fetch booking for failed/expired. Never interpret an HTTP timeout as confirmation. |

## Notifications

Use platform inbox + push, routed by application user IDs. Deduplicate by booking + transition ID (reminder additionally by scheduled instant and lead time). Write occurred_at UTC, booking_id, service_id, category, entity_type=service_booking, entity_id, status, starts_at with offset, timezone, delivery_mode. Proposed client routes: mobile /bookings/{id}, enterprise /enterprise/bookings/{id}; deep links still require authenticated detail fetch. No credentials, medical notes or rejection reasons in push. Authorized inbox/history may include the reason.

| Category | Recipient(s) |
|---|---|
| service_booking_new | Assigned provider and owning enterprise admins on creation; customer gets the corresponding pending/confirmed/hold state |
| service_booking_payment_pending (new) | Customer on new unpaid hold |
| service_booking_pending_approval, service_booking_confirmed | Customer; provider/admin informed of confirmed schedule |
| service_booking_cancellation_requested | Provider and owning admins; customer sees pending status |
| service_booking_cancelled, service_booking_cancellation_rejected | Customer, assigned provider and owning admins on decision; inbox distinguishes instant vs approved |
| service_booking_rescheduled (new), service_booking_started (new) | Customer, assigned provider and owning admins |
| service_booking_reminder | Customer on confirmed booking, 24 h and 1 h before; invalidate old schedules after moves |
| service_booking_completed, service_booking_no_show | Customer; lifecycle change remains visible in staff history |
| service_booking_expired (new) | Customer when a hold expires; invalidate reminders |

## Service banner decision for review

Existing generic POST /uploads/ accepts any authenticated user, trusts broad MIME families, allows up to 100 MiB, and returns a public local-download URL. It lacks Service ownership enforcement. Reuse image inspection/storage helpers, **not this public contract for scoped asset creation**. Proposed POST /services/banner-uploads?enterprise_id=UUID accepts one multipart file, tenant admin/super admin only, JPEG/PNG/WebP verified by decoding, default limit 10 MiB. Return `{"url":"/api/v1/uploads/service-banners/opaque.webp","size":82134,"type":"image/webp"}`. Reject oversize 413, unsupported media 415, invalid image 422. Asset bytes are public listing imagery; upload/manage is protected. Validate uploaded asset ownership when attaching it to the Service. Use opaque unique names; no shared mutable path.

External banner URL: HTTPS only, approved configured CDN hostname, no userinfo, private/IP literals, nonstandard ports or redirects; store reference only, never fetch arbitrary URLs. Preserve stored legacy URLs on read and when omitted. PUT banner_image with new reference replaces; explicit null clears; omitted preserves. Both list and detail return banner_image. Do not delete an old asset while a service still references it; garbage collect only owned unreferenced uploads after a grace period, never delete external assets. No gallery. Existing availability_schedule, availability_status, duration, max_participants, provider_user_id, delivery_format, cancellation_policy and CRUD paths remain.

## Acceptance before enabling clients

Required release checks include concurrent final-seat claims and overlapping provider writes on PostgreSQL, expiry/capture races, failed reschedule rollback, role/tenant access, refund webhook replay, late capture compensation, banner ownership and DST overlaps. The review artifact documents intended guarantees, not passing results for functionality not yet implemented.
