# Training approval & enrollment notifications

System-generated notifications (`notification_type: "automatic"`) on the **existing** platform feed.
There is no separate Web or mobile channel: one record is written per event and then delivered three ways.

| Surface | How |
|---|---|
| Feed (Web, mobile, Super Admin) | `notifications` + `user_notifications` rows → `GET /users/me/notifications`, `GET /users/me/notifications/unread-count`, `PUT /users/me/notifications/{id}/read` |
| Realtime | the generic Socket.IO `notification` event, emitted to the recipient's room `user:<user_id>` |
| Mobile push | FCM to the recipient's registered device tokens (`POST /devices/register`), if push is enabled for them |

Code: `app/services/training_workflow_notifications.py` (admin recipients),
`app/services/training_notifications.py` (learner), `app/services/notification_idempotency.py`.

## Types, recipients, triggers

| `category` | Recipient | Sent when |
|---|---|---|
| `training_submitted` | Platform / Super Admins | a Training enters `pending_approval` — first submission **and** every resubmission (`/resubmit`, `PATCH /status`, or created straight into the queue) |
| `training_approved` | owning Enterprise Admin(s) | Super Admin approves (`approved`) |
| `training_rejected` | owning Enterprise Admin(s) | Super Admin rejects (`rejected`); `reason` when given |
| `training_changes_requested` | owning Enterprise Admin(s) | Super Admin requests changes (`needs_revision`); `reason` when given |
| `training_enrolled` | owning Enterprise Admin(s) | a learner's enrollment is **confirmed** (`enrolled`) — see rules below |
| `training_enrollment_accepted` | the learner | an Enterprise Admin accepts a pending enrollment (also emailed) |
| `training_enrollment_rejected` | the learner | an Enterprise Admin rejects a pending enrollment; `reason` when given |

Notifications are created only **after** the state change is committed. A failed or invalid transition
(HTTP 4xx) sends nothing, and a notification failure never fails the request that caused it.

### Who the recipients are

- **Platform / Super Admins:** active users flagged `isSuperAdmin` / `super_admin` in the Invigorate
  tenant-user listing — the same resolver Event approvals use
  (`event_notification_service.resolve_platform_admin_user_ids`). Ordinary tenant admins are excluded.
- **Owning Enterprise Admins:** the Training's enterprise → its tenant → that tenant's active users whose
  role is `admin` / `tenant_owner`. Providers, tenant admins, ordinary members and other tenants are excluded.
- **Learner:** `training_enrolments.user_id` (set when they enrol themselves), else looked up by email.
- Recipients are re-derived from the saved Training/enrollment rows when the notification is sent, never
  taken from the request. With no eligible recipient nothing is written (no orphan feed record).

### Super Admin feed

A Super Admin's feed is the same endpoint everyone else uses: `GET /users/me/notifications` with their
login token. Rows are stored against the user id found in the Invigorate tenant-user listing, and the feed
reads rows for the user id that `GET /auth/me` returns for the logged-in session — **these two ids must be the
same for the notification to show up**. Realtime delivery needs the Super Admin's Socket.IO connection to
authenticate (see `docs/socket-web-session-auth.md`); push needs a registered device token. Requires
`INVIGORATE_AUTH_BASE_URL` + `INVIGORATE_INTERNAL_API_KEY`; without them Super Admin / Enterprise Admin
recipients cannot be resolved and nothing is sent (logged).

## Payloads

Every notification's `metadata` repeats `category`, so push `data` can be routed (the FCM message has no
separate category field). Keys with no value are omitted.

| `category` | `metadata` |
|---|---|
| `training_submitted` / `training_approved` | `category`, `training_id`, `entity_type: "training"`, `entity_id`, `status` |
| `training_rejected` / `training_changes_requested` | the above + `reason` (when given) |
| `training_enrolled` | `category`, `training_id`, `entity_type`, `entity_id`, `enrollment_id`, `status: "enrolled"` |
| `training_enrollment_accepted` | same, `status: "enrolled"` |
| `training_enrollment_rejected` | same, `status: "rejected"`, `reason` (when given) |

`entity_id` is always the Training id; `status` is the final status of the thing that changed (the Training's
status for approval events, the enrollment's for enrollment events).

Feed item (`GET /users/me/notifications`):

```json
{
  "id": "0b6f3c1e-…", "notification_id": "4e7d2c90-…", "is_read": false,
  "title": "Enrollment rejected",
  "message": "Your enrollment in \"Ergonomics 101\" was not accepted.",
  "notification_type": "automatic",
  "category": "training_enrollment_rejected",
  "metadata": {
    "category": "training_enrollment_rejected",
    "training_id": "62078973-39ac-46e5-b867-6196935025ba",
    "entity_type": "training",
    "entity_id": "62078973-39ac-46e5-b867-6196935025ba",
    "enrollment_id": "a972be06-bdb7-4307-ae7c-7a734f234662",
    "status": "rejected",
    "reason": "Seat full"
  },
  "created_at": "2026-10-05T10:15:00"
}
```

Socket.IO `notification` event: `{notification_id, title, message, metadata, created_at}` — same `metadata`
as the feed item, with `category` inside it (no top-level category).

Push (FCM): `notification: {title, body}` plus `data` = `metadata` as strings, **without `reason`**:

```json
{
  "notification": {"title": "Enrollment rejected", "body": "Your enrollment in \"Ergonomics 101\" was not accepted."},
  "data": {
    "category": "training_enrollment_rejected", "training_id": "62078973-…", "entity_type": "training",
    "entity_id": "62078973-…", "enrollment_id": "a972be06-…", "status": "rejected"
  }
}
```

Push is deliberately minimal: titles/bodies carry the Training title only — no learner name or email — and
free-text fields (`reason`) are in the feed and socket event only (`PUSH_OMITTED_METADATA_KEYS`).

> **Rename.** The learner decision notifications were previously `enrolment_approved` / `enrolment_rejected`
> with an `enrolment_id` key. They are now `training_enrollment_accepted` / `training_enrollment_rejected` with
> `enrollment_id` (American spelling, per the contract). Other training notifications
> (`training_enrolment_confirmation`, `enrolment_cancelled`, …) keep their names and `enrolment_id`.

## Enrollment rules

**Automatic acceptance** (`requires_approval` is off): the enrollment is `enrolled` immediately. The Enterprise
Admins get `training_enrolled`; the learner gets the normal `training_enrolment_confirmation`. They do **not**
get `training_enrollment_accepted` — no admin decided anything.

**Approval required:** the enrollment is `pending_approval`; nobody is told it is *confirmed*. When an Enterprise
Admin accepts, the learner gets `training_enrollment_accepted` and the Enterprise Admins get `training_enrolled`
— except the admin who clicked accept, who is excluded. A rejection sends `training_enrollment_rejected` to the
learner only (with `reason`), never `training_enrolled`.

**Paid trainings** (`price > 0`): `training_enrolled` is sent only once a payment is recorded — an order for that
learner with `payment_status: "confirmed"`. Checkout creates the enrollment and that order in one transaction, so
the notification fires after it commits. Enrolling through plain `POST /enrol` records no payment, so no
`training_enrolled` is sent for it.
- *Pending payment / failed payment:* the current checkout has no pending or failed state — the order is created
  already `confirmed`, and a failed checkout rolls back, leaving no enrollment and no notification. When a
  payment gateway is added, call `training_workflow_notifications.notify_enrollment_confirmed_to_admins(training,
  enrollment)` from its payment-success handler; the payment check and the once-only key are already inside it.
- *Paid + approval required:* the learner pays at checkout, the enrollment waits as `pending_approval`, and
  `training_enrolled` goes out when the admin accepts.

**Waitlist promotion:** a promoted learner who becomes `enrolled` triggers `training_enrolled` (free trainings
only — a waitlisted learner has not paid). A promotion into `pending_approval` waits for the admin's decision.

**Cancellation:** cancelling sends the learner their existing `enrolment_cancelled` and nothing new to admins. An
earlier `training_enrolled` is not retracted (the feed is append-only); re-enrolling creates a new enrollment and a
new `training_enrolled`.

**Refunds:** requesting, approving or rejecting a refund changes only the order. It emits none of these types and
does not change the enrollment status (that is how refunds already work).

**Repeated status changes:** a notification is sent only when the status really changes, and each real decision has
its own idempotency key. Re-accepting an accepted enrollment or re-rejecting a rejected one (a retry) is silent;
reject → accept → reject notifies the learner each time; `training_enrolled` is once per enrollment.

## Preventing duplicates on retries

Every delivery first inserts a unique row into `notification_event_log` (`dedupe_key`); only the caller that
inserts it delivers. If delivery then produces nothing, the row is deleted so a retry can still deliver.

| Event | `dedupe_key` |
|---|---|
| `training_submitted` / `_approved` / `_rejected` / `_changes_requested` | `<type>:<training_id>:<moderation_history length>` — each real transition appends one history entry, so resubmission notifies again and a retry of the same transition does not |
| `training_enrolled` | `training_enrolled:<enrollment_id>` |
| `training_enrollment_accepted` / `_rejected` | `<type>:<enrollment_id>:<moderation_history length>` |

## The caller's token reaches the identity lookups

Recipients are found through the Invigorate tenant / tenant-user listings. Those calls now carry the **caller's own
Bearer access token** (`Authorization: Bearer …`, taken from the request's header or session cookie) in addition to the
internal API key when one is configured; with only a token — no key — the lookup is still made. This covers
`training_submitted`, `training_approved`, `training_rejected`, `training_changes_requested`, `training_enrolled`
and the new-training fan-out.

The token is passed from every handler that changes a Training's status or an enrolment — the admin approve / reject /
request-changes / publish routes for trainings **and** the `/admin/courses/...` aliases, `PATCH /status`, publish,
unpublish, suspend, cancel, archive, restore, resubmit, enrol, checkout, waitlist join/leave, enrolment cancel and
enrolment approve/reject — through the status/enrolment services into the background job. It is never logged or stored.
Whether Invigorate accepts a user token on these listing endpoints is up to Invigorate: if it does not, the lookup
simply returns nothing (the same as an unset key) and `GET /api/v1/admin/notifications/diagnostics` shows it.

## The learner's user id on every enrolment

`training_enrollment_accepted` / `_rejected` reach a learner's feed only when the enrolment knows their application user id
(`training_enrolments.user_id`). It is now saved on every path: **direct enrolment, checkout, the waitlist
(`training_waitlist.user_id`, copied to the enrolment on promotion)**. "The caller is the learner" is decided on their
verified identity — the token's email claim, then the email in their `/auth/me` profile — not only on an exact claim
match, so differing case or a token with no email claim still works. Staff enrolling someone else never get their own id
stamped on that person's row, and a body email that belongs to a different person than the caller is not attributed to
the caller.

### Rows that have no user id yet

Resolved from sources that do not depend on anything a client sent, never guessed:

1. **At decision time** — when an admin accepts or rejects such an enrolment the server looks for the learner: the same
   email (case-insensitive) on their other enrolment / waitlist rows that have an id, then the Auth service's member list of
   the training's tenant, matched on email. A match is saved on the row and the notification is sent. No match = the
   decision still succeeds and nothing is guessed.
2. **When the learner next opens `GET /trainings/my/enrolments`** while logged in, every older row carrying *their*
   verified email (profile email if present, and not marked unverified) is linked to their user id. This is the only way
   to reach a learner who belongs to no tenant and has no other record.
3. **In bulk** — `python -m scripts.backfill_training_enrolment_user_ids` (dry run, prints counts) and then `--apply`.
   Uses the same two server-side sources; learners it cannot resolve are left for step 2.

## When nothing arrives

Recipients come from the Invigorate tenant-user listing, which needs `INVIGORATE_AUTH_BASE_URL` and
`INVIGORATE_INTERNAL_API_KEY`. **If the key is unset the listing is empty, every Super Admin / Enterprise Admin
resolution comes back empty, and the workflow sends nothing without raising** (the state change itself still
succeeds). It now logs a WARNING (`… NOT sent … no eligible recipients (invigorate_internal_api_configured=…)`).

`GET /api/v1/admin/notifications/diagnostics?event_id=<id>` (or `training_id=`) — **active Platform Super Admin only**
(the Enterprise-Admin fallback is refused) — reports, with ids and counts only:

- `config`: whether the internal API is configured, whether Redis is configured for Socket.IO, and whether the
  realtime loop is attached;
- `platform_admins`: tenants/users scanned, the Super Admin ids that resolve, and why others were excluded;
- `event` / `training`: the owning tenant, the Enterprise Admin ids that resolve, the roles the listing actually
  contains (`roles_seen`) and the notification rows recorded for that entity with their recipient ids.

The ids that resolve must equal the id `GET /auth/me` returns for that person's session; the feed is read by the
latter.

## Realtime delivery

The Socket.IO `notification` event is emitted right after the feed rows are saved. Sync REST routes and background
jobs have no asyncio loop, so emission goes through `app/realtime/loop_bridge.py`, which hands the coroutine to the
server's own loop (captured at startup). Before this, `asyncio.get_event_loop()` raised in those threads and the
event was dropped while the feed row was still written. In a split deployment the API process publishes through
the Socket.IO Redis manager (`SOCKETIO_REDIS_URL`) to reach the socket server.

## Deploy

`alembic upgrade d8f2b4a9c1e6` — adds `notification_event_log` (`b9d4f2a6c8e1`) and `training_waitlist.user_id` (`d8f2b4a9c1e6`). Then run the backfill script once (dry run first).
