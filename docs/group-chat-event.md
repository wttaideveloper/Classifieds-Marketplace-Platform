# Event Group Chat: API contract

A group chat for the **online registrants of one Event**, run by the **Enterprise Admin of the enterprise that owns the Event**. It is the
existing Group Chat (`/api/v1/groups`) with a new link type, so messages, replies, reactions, attachments, voice messages, search, pinned
messages, read receipts, unread counts, Socket.IO events, member management, moderation and close/reopen are exactly the ones described in
`group-chat-api.md`. Direct Chat and Training Group Chat are unchanged. Training equivalent: `group-chat-training.md`.

All paths below are under `/api/v1/groups`. Every call needs a bearer token.

## 1. The rules in one page

| Question | Answer |
|---|---|
| Who creates and manages an Event group? | The **Enterprise Admin** (`role: admin`) of the tenant that owns the Event. Not a Provider (assigned or not), not a speaker or organiser named in the event form, not the Super Admin, not an attendee. Checked on every call, not only when the group is made. |
| Group context | `kind: "program"`, `context_type: "event"`, `context_id: <event id>` |
| Which Events | Own enterprise's only. A group can be **created** for a `published` or `completed` Event; an existing group stays usable whatever happens to the Event afterwards. |
| Who can be invited as a registrant | An **online** registration (never walk-in) that is `confirmed` or `attended`, whose payment is **`paid` or `free`**, that has a **platform `user_id`**; one member per person. |
| Providers | The enterprise's **verified Providers** can be invited. They join as ordinary members and can never be made admin or owner. |
| Limit | **50** members per group, owner and pending invitations included (`GROUP_MAX_MEMBERS_EVENT`). |
| Several groups per Event | Yes. |
| Close / reopen | **Manual only** (`POST /{id}/close`, `/reopen`). Nothing closes a group when the Event ends, is cancelled or is rescheduled. |

## 2. Eligibility of a registrant (server-side, every time)

| Registration | Eligible? | `reason` when not |
|---|---|---|
| `confirmed`, `attended` | yes | |
| `cancelled` | no | `registration_cancelled` |
| `no_show` | no | `registration_no_show` |
| walk-in (`registration_source = walk_in`) | **never listed, never invitable** | (counted in `walk_in_excluded`) |

| Payment (derived from the paired order) | Eligible? | `reason` when not |
|---|---|---|
| `free` (free event, or a free ticket, no order) | yes | |
| `paid` | yes | |
| `unpaid` (paid event, no order) | no | `payment_unpaid` |
| `pending` | no | `payment_pending` |
| `failed` | no | `payment_failed` |
| `cancelled` (order cancelled) | no | `payment_cancelled` |
| `refund_requested` | no | `payment_refund_requested` |
| `refunded` | no | `payment_refunded` |

Then: no platform account found → `no_account`; same person twice → the later row is `duplicate_registration`; (with `group_id`) already in
the group → `already_member`, already invited → `already_invited`.

The same rules run when the group is created, when more people are invited later (by id, in bulk, or with `POST /{id}/members`), and when an
invited person **accepts**: someone whose registration was cancelled or refunded after the invitation was sent gets
`409 This invitation is no longer valid...`. People who are already members are **not** removed automatically when they cancel or are refunded:
the Enterprise Admin removes them (`DELETE /{id}/members/{user_id}`).

## 3. Endpoints

### 3.1 `GET /events` - Events I can start a group for

Query: `q` (title), `status`, `page` (1), `page_size` (20, max 100). Response:

```json
{ "items": [ { "id": "uuid", "title": "Wellness Day", "status": "published", "delivery_mode": "in_person",
               "start_date": "2026-11-20T09:00:00", "end_date": "2026-11-20T17:00:00",
               "registered_count": 42, "group_count": 1, "can_create_group": true, "reason": null } ],
  "total": 1, "page": 1, "page_size": 20 }
```
`registered_count` = confirmed or attended online registrations (payment is checked in the attendees list). `can_create_group` is false with
`reason: "event_not_open"` for any status other than `published` / `completed`.

### 3.2 `GET /events/{event_id}/attendees` - invitation candidates

Query: `q` (name or email), `eligible_only` (true), `registration_status` (`confirmed|attended|cancelled|no_show`), `payment_status`
(`free|paid|unpaid|pending|failed|cancelled|refund_requested|refunded`), `not_in_group` (false), `group_id`, `page` (1), `page_size` (50, max 200).
By default only people who can be invited are listed; `eligible_only=false`, `registration_status` or `payment_status` shows the others with
their `reason`. `not_in_group=true` hides everyone already in or invited to a group of this event (or to `group_id`). Without `group_id` the list
is for a **new** group (`can_invite` ignores other groups; `group_state` / `group_ids` still say where the person is).

```json
{ "event_id": "uuid", "event_title": "Wellness Day", "event_status": "published",
  "items": [ { "registration_id": "uuid", "name": "Priya Sharma", "email": "priya@example.com", "registration_status": "confirmed",
               "payment_status": "paid", "user_id": "uuid-or-null", "can_invite": true, "reason": null,
               "group_state": "none", "group_ids": [], "registered_at": "2026-11-01T10:00:00" } ],
  "total": 1, "page": 1, "page_size": 50, "eligible_total": 42, "walk_in_excluded": 3, "max_members": 50 }
```
`user_id` is the **authoritative platform user id** to send when inviting. `group_state`: `none | invited | member | declined | left | removed`.
`eligible_total` ignores the filters. A missing `user_id` is looked up (by email, from the enterprise's member list) and saved on the registration.
`GET /api/v1/events/{event_id}/attendees` (the attendee management API) is **unchanged** and still has no `user_id`: use this endpoint for chat.

### 3.3 `GET /events/{event_id}/providers`
```json
{ "event_id": "uuid", "items": [ { "user_id": "uuid", "name": "Dr Rao", "registration_number": "REG-123", "is_me": false } ], "total": 1 }
```
Query `q` (name or registration number). Verified Providers of the enterprise only. Speakers and organisers typed into the event form are text:
nobody is invited from them.

### 3.4 `GET /events/{event_id}/groups`
The standard group list (`GroupListResponse`, see `group-chat-api.md`): every group of this event, open and closed, with `context_name` = the
event title. The Enterprise Admin sees all of them (details only, never the messages of a group they are not in).

### 3.5 `POST /` - create the group
```json
{ "name": "Wellness Day Chat", "kind": "program", "description": "Updates for the day", "avatar_url": null,
  "context_type": "event", "context_id": "<event id>",
  "invitees": [ { "user_id": "<from the attendees list>" } ],
  "invite_all_registered": false, "exclude_user_ids": [], "admins_only_posting": null }
```
* `kind` must be `program` (422 otherwise). `invite_all_registered` is for `context_type: "event"` only; `invite_all_enrolled` is for Trainings only (422).
* `invitees` are checked as in section 2. With `invite_all_registered: true` every eligible, unique registrant (minus `exclude_user_ids`, minus
  the people in `invitees`) is invited too, up to the free places. The people left out because the group is full are **not** reported by this call;
  use `members/invite-registered` afterwards (it reports `not_invited_group_full`).
* `admins_only_posting` defaults to **true** for a program group (announcements): send `false` if attendees should be able to post.
* The creator becomes the owner. Invitees are **not** members and see nothing until they accept.
* Response: the standard `GroupResponse`: `kind: "program"`, `context_type: "event"`, `context_id`, `context_name` (event title), `my_role: "owner"`,
  `limits.max_members: 50`.

### 3.6 `POST /{group_id}/members/invite-registered` - invite more, later
Body: exactly one of `{ "user_ids": ["..."] }` or `{ "all_eligible": true, "exclude_user_ids": ["..."] }`. 201:
```json
{ "invited": ["uuid"], "skipped": ["uuid"], "not_invited_group_full": 0, "no_account": 0, "free_places": 12 }
```
`skipped`: already a member, already invited, or the caller. `no_account`: eligible registrations with no platform account. `not_invited_group_full`:
eligible people left out because the group is full (make another group for them). Use it again later for people who registered afterwards.
`POST /{group_id}/members` (named invitees) applies the same eligibility checks to an event group.

### 3.7 Everything else
Accept / decline (`POST /{id}/accept`, `/decline`), `GET /invitations`, members, remove, mute, messages, replies, reactions, attachments, voice,
search, pinned, read receipts, `POST /{id}/close`, `POST /{id}/reopen`, audit, reports: unchanged (`group-chat-api.md`). Differences for an event group:
* An invitation's `purpose` says "the people registered for the event "<title>"", and the invitation notification names the event and carries
  `context_type: "event"` and `context_id` in its metadata so the app can open it.
* Making a member an admin (`PATCH /{id}/members/{user_id}/role`) is **403**; handing the group to a Provider (`POST /{id}/transfer-ownership`) is **403**.
* Pending invitees' names in the member list (admin view) come from their registration.

## 4. Errors

Plain FastAPI `detail`, except invitee errors:

| Status | When | `detail` |
|---|---|---|
| 401 | no / invalid token | |
| 403 | not an Enterprise Admin; an event of another enterprise; no tenant identity; making an admin / handing over an event group | message string |
| 404 | `group_id` filter is not a group of this event; unknown group | |
| 409 | event is not `published` / `completed` (creating); group full; group closed; invitation no longer valid on accept | message string |
| 422 | unknown event (`The linked event was not found`); `kind` not `program`; wrong invite flag; both / neither of `user_ids` and `all_eligible`; invitee not eligible | |

Invitee not eligible (422):
```json
{ "detail": { "message": "These people do not have an eligible registration for this event and are not verified providers of the enterprise, so they cannot be invited.",
              "code": "INVITEE_NOT_ELIGIBLE", "invalid_user_ids": ["uuid"],
              "reasons": { "uuid": "registration_cancelled" } } }
```
`reasons` values: the reasons of section 2, or `not_registered` (no online registration of this event for that account).

## 5. Registrant user ids (`event_registrations.user_id`)

Nullable. Set only from a verified identity, never from request input:
1. **New registrations**: `POST /events/{id}/registrations` and `POST /events/{id}/checkout` save the caller's user id when the caller is the person
   registered. Staff registering someone else, group members added by the registrant, waitlist promotions and walk-ins stay empty.
2. **Recovered**: opening the attendees list or inviting resolves empty ids from the same email on the person's other registrations, then from the enterprise's
   member list (identity service), and saves them.
3. **`GET /api/v1/events/my/registrations`** links the caller's own older registrations (their verified email) to their user id.
4. **Backfill**: `python -m scripts.backfill_event_registration_user_ids` (dry run) / `--apply`. Needs the identity service settings
   (`INVIGORATE_AUTH_BASE_URL`, `INVIGORATE_INTERNAL_API_KEY`) to use the member list.

A registrant who belongs to no enterprise and has never opened "my registrations" cannot be found: they show `reason: "no_account"` until they do.

## 6. Migration and rollout

* Migration `n3e6a9c2d7f4` (after `m2d5f8b3a6c1`): adds `event_registrations.user_id` (nullable UUID, no foreign key) and indexes
  `ix_event_registrations_user_id`, `ix_event_registrations_event_status`. Existing rows stay `NULL`. The repository has other open migration heads
  that are not part of this work: check `alembic heads` before `alembic upgrade`.
* Setting: `GROUP_MAX_MEMBERS_EVENT` (default 50). It is separate from `GROUP_MAX_MEMBERS_PROGRAM`.
* After deploying run the backfill in dry-run mode, then `--apply`.
* Rollback: `alembic downgrade m2d5f8b3a6c1` drops the column (the saved ids are lost; they can be recovered again by the backfill). Existing event groups keep
  working as ordinary groups; the previous code would reject `context_type: "event"` on new groups only.

## 7. Decisions taken for this version

1. Enterprise Admin only: no assigned host, no speaker administration.
2. Walk-ins are excluded entirely.
3. Registrants need `confirmed`/`attended` + `paid`/`free`; unpaid, pending, failed, cancelled, refund-requested and refunded are excluded.
4. Verified Providers can be invited as ordinary members (no admin, no ownership).
5. 50 members including pending invitations; several groups per event; a full group refuses new invitations.
6. Manual close and reopen only. A cancelled or rescheduled event does not touch its groups; a new group cannot be created for a cancelled event.
7. Added on top of the brief: accepting an invitation re-checks eligibility (a refund between invite and accept blocks joining), and members are not
   removed automatically when they later cancel or are refunded.

## 8. Tested and not tested

Tested (`tests/test_group_chat_event.py`, 38 tests, real routes on an in-memory SQLite database): the link type, who may manage, cross-enterprise
isolation, the events list, every eligibility case above, walk-in exclusion, user id recovery / linking / backfill, de-duplication, invitations
(named, everyone, exclude, later, repeated), the limit, several groups, accept re-check, Provider members, messaging and manual close / reopen, and that
Program / Care / Training groups are unchanged (the existing group chat, chat and event registration suites also pass).

Not tested: PostgreSQL (the payment derivation reuses the attendee list's SQL, but every query of this feature was only run on SQLite); the identity service (its member list is stubbed); real Socket.IO delivery and push / email (captured, not sent); the real
registration and checkout routes end to end (the registration service is called directly); load with thousands of registrants.
