# Event session attendance: backend contract (Phase 2.4)

Session-level check-in for events with sessions, tracked per attendee per session. It is **additive**:
event-level check-in (`/check-in`, `/uncheck-in`, `/check-out`, `/batch-check-in`, `/validate-qr`), the
registration status, tickets, capacity, payment, orders, the waitlist, meeting links, `status`,
`lifecycle_state`, `event_type` and `modules` behave exactly as before.

## Data model

Sessions live in `events.sessions` (JSONB); there is no session table and none was added. One new table,
`event_session_attendance`, records that an attendee attended a session:

| Column | Notes |
|---|---|
| `id` | UUID primary key |
| `event_id` | FK `events.id`, `ON DELETE CASCADE` |
| `registration_id` | FK `event_registrations.id`, `ON DELETE CASCADE` |
| `session_id` | `VARCHAR(100)`: an id **inside** `events.sessions`. Deliberately not a foreign key |
| `checked_in_at` / `checked_in_by` | when, and the operator's user id (UUID; null if the caller's id is not a UUID, like `event_registrations.checked_in_by`) |
| `checked_out_at` / `checked_out_by` | null until checked out |
| `created_at` / `updated_at` | housekeeping |

`UNIQUE (event_id, registration_id, session_id)` keeps it to one row per attendee per session. Indexes:
`(event_id, session_id)` and `(registration_id)`; the unique constraint's index already serves
`(event_id, registration_id)` and `event_id` alone, so nothing duplicates it.

State is carried by the timestamps: a row exists = checked in; `checked_out_at` set = checked out. There is no
status enum. Undoing a check-in **deletes** the row; the audit trail keeps the history.

`event_registrations.session_id` (set by event-level check-in when a session is named) is **not** used and not
changed. An attendee can attend many sessions, which one column cannot express.

## Endpoints

All `POST`, all `200`, all under `/api/v1/events/{event_id}/sessions/{session_id}/`. The body identifies the
attendee by `registration_id` or by their **existing registration QR value** (`qr_code`); there is no second QR
format. `registration_id` wins if both are sent. This mirrors the existing event-level routes (POST with a
body, not DELETE), so a scanner uses the same identity for both.

| Path | Body | Result |
|---|---|---|
| `check-in` | `registration_id \| qr_code`, `method?` | `EventSessionAttendanceResponse`, `outcome`: `checked_in` or `already_checked_in` |
| `uncheck-in` | `registration_id \| qr_code`, `reason?` | `outcome: unchecked_in` |
| `check-out` | `registration_id \| qr_code` | `outcome`: `checked_out` or `already_checked_out` |
| `batch-check-in` | `participants: [{registration_id \| qr_code}]` (1..1000) | `EventSessionBatchCheckInResponse` |

The existing `POST /{event_id}/check-in` and `POST /{event_id}/batch-check-in` still accept an optional
`session_id`; that legacy field is unchanged and does **not** create session attendance.

### Validation (in this order)

| Check | Failure |
|---|---|
| caller owns the event (below) | 401 / 403 |
| event exists and is not deleted | 404 |
| `session_id` is a session of **this** event with a non-empty id | 404 `Session not found` |
| registration exists **in this event** (by id or QR) | 404 `Registration not found` (400 if neither id nor QR is sent) |
| event is not `cancelled`/`completed`/`archived`/`suspended` | 400 (same set event-level check-in refuses) |
| registration is not `cancelled` | 400 |
| registration was not refunded (`_registration_is_refunded`, the event-level rule) | 400 |

Eligibility deliberately reuses the event-level rules instead of a second system. `confirmed`, `attended`
and `no_show` registrations can be checked in, as at event level. Undo needs only an existing attendance row
(so a correction is still possible after a cancellation). Check-out refuses `cancelled`/`no_show` and needs a
prior check-in to that session.

### Idempotency

A repeat check-in is `200` with `outcome: already_checked_in`, keeps the original time and operator, and writes
no audit row; the unique constraint backs this up under concurrency (a racing second scan is reported as
`already_checked_in`, never a 500 and never a duplicate). Same for a repeat check-out. Checking in again after a
check-out does **not** reopen the session; undo first (as at event level).

### Independence from event-level check-in

Session check-in does not require event-level check-in and never sets or clears `status`, `checked_in_at`,
`checked_in_by`, `checked_out_at` or `session_id` on the registration. An attendee can be event checked in and
attend sessions A and B but not C, or attend a session without being event checked in. Event-level undo and
check-out do not touch session attendance.

### Batch

Each entry is validated on its own; a bad one (unknown, other event, cancelled, refunded, no identifier) fails
alone with a message and never blocks the rest. Result `status`: `checked_in`, `already_checked_in`, `failed`;
`succeeded` counts the first two. A registration listed twice is checked in once. Whole-request errors are the
session 404, an event state that refuses check-in (400) and a body outside 1..1000. Registrations, existing
attendance and refunds are loaded with one query each for the whole batch (no per-attendee queries); rows are
inserted together, and if a concurrent scan races one of them the pass is redone insert-by-insert so only that
attendee is reported as `already_checked_in`. All rows and audit rows commit once.

## Security

Same event-ownership dependency as the Phase 2.3 attendee/dashboard routes (`require_event_staff`, on top of
Phase 2.1 `require_event_owner`): the owning tenant's `admin` or `provider`, or an **active** platform
`super_admin`. Foreign tenant `403`, customer `403` (even for their own registration), inactive super admin
`403`, no token `401`. The session and the registration are always looked up inside the event in the path, so
neither a session id nor a registration id/QR from another event resolves. No request field can name a tenant.
The event-level check-in routes keep their existing gate (`admin`/`provider`).

## Audit

Existing `event_audits` table and `_log_audit(..., commit=False)`: each change stages its audit row in the same
transaction and commits once, so a failed commit rolls both back (no orphan audit row).

| Action | before | after |
|---|---|---|
| `session_check_in` | null | registration, email, session id + title, `checked_in_at/by`, `method` (`batch` for batch) |
| `session_uncheck_in` | the attendance that was removed | `checked_in: false`; `notes` = reason |
| `session_check_out` | attendance without `checked_out_at` | attendance with it |

`changed_by` is the operator's user id. Repeats and rejected requests write nothing.

## Reporting

`summarize_session_attendance` (two aggregate statements, however many attendees) for the event's **current**
sessions that have an id:

- `registered_count`: the event's active registrations (`confirmed` + `attended`). A registration is for the
  event, not for one session, so every session has the same denominator.
- `checked_in_count`: attendance rows whose registration is still active, so the percentage cannot exceed 100.
- `attendance_percentage`: checked in / registered, 1 decimal; `null` when nobody is registered.

Where it appears (all additive):

- **Dashboard** `GET /{event_id}/dashboard`: new `sessions[]` (`session_id`, `title`, `session_date`,
  `start_time`, `registered_count`, `checked_in_count`, `attendance_percentage`); `[]` without sessions. Every
  existing dashboard number is unchanged.
- **Attendance report** `GET /{event_id}/attendance` `attendance_by_session` and
  `GET /{event_id}/reports?type=attendance` `by_session`: same keys as before (`total`, `attended`) now fed by the
  dedicated records (`total` = registered, `attended` = checked in), plus `session_id`, `title`,
  `attendance_percentage`. One entry per session; `null` (`{}` in the report) when the event has no sessions.
  **Behaviour change:** these used to be built from `event_registrations.session_id`.
- **Attendee list / detail** (`/attendees`, `/registrations/{reg_id}`): new `session_attendance[]`, one entry per
  session of the event (`session_id`, `title`, `checked_in`, `checked_in_at`, `checked_in_by`,
  `checked_out_at`). One extra query per page, only for events that have sessions. Not in the CSV export.

## Session ids and their limits

Session ids are assigned by the existing paths (event create/update normalisation and add/update/delete
session) and are uuid4 strings. **No read or attendance call ever writes to `events.sessions`.** A legacy session
without an id cannot take attendance (404) until one of those paths gives it an id; the id is then stable. An event
update that resends a session **without its id** gets a new id, orphaning that session's attendance (Web resends
ids, so this only affects a client that drops them).

Deleting a session (existing endpoint) leaves its attendance rows in place as history; they are simply no longer
reported, and cannot receive new attendance. Registrations are never hard-deleted by the app; if one is, its rows
cascade.

## Migration

`1e40e81e2937` creates the table and two indexes, nothing else, no data. Parent `adae1a2909cc` (Phase 2.2, the
head that carries the event-domain schema work), the same strategy 2.2 used: it advances that head, still four
heads, no merge, no history change; deployments keep running `alembic upgrade heads`. Downgrade drops the two
indexes and the table.

## Not in this phase

Walk-in registration, meals, accommodation, session capacity or per-session registration, re-entry, a
per-session roster endpoint, module enforcement (`modules.sessions` / `modules.check_in` are still only
reported), backfilling `event_registrations.session_id` into the new table.
