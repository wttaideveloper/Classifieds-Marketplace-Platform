# Group Chat API

For the web and mobile developers, QA, and the clinic's compliance team. All paths start with `https://chat.wisdomtooth.tech/api/v1`. Swagger is at `/docs` (tag "Group Chat").

This replaces the earlier generic design in `group-chat-feature.md` wherever the two differ. That document was written before the healthcare rules (verified providers, consent, care and program groups, audit trail).

Everything here is built and tested, except the items in section 11, which belong to infrastructure, the apps or legal.


## 1. The idea in one page

A group is one conversation with several people, a name, and a group admin. In a healthcare app three rules apply to every group:

1. Only a **verified provider** or a **clinic admin** can create a group. A patient cannot.
2. **Nobody is added without agreeing.** An invited person sees nothing in the group until they accept.
3. Every group is **linked to a program, an appointment series, a care episode or a training**. There are no free-form groups. Training groups have their own document: `group-chat-training.md`.

There are two kinds of group:

| | Care group | Program group |
|---|---|---|
| What it is | one patient's care team | several patients in one program (a class) |
| Members | up to 15 | up to 50 |
| Patients see each other as | full names | display names only |
| Who has read a message | everyone can see | patients cannot, providers can |
| Typing indicator | yes | no |
| Reactions | off | a small set |
| "Providers only" posting | off by default | on by default |
| A new member sees earlier history | only if the patient agreed and the owner shares it | never |
| Appears for a patient in | the Groups tab | the Groups tab |

The sizes, the 15 minute edit window and the three-admin limit are settings (section 10).


## 2. Who can do what

Roles in a group: **owner** (the provider who created it), **admin**, **member**. Each member is also a **provider** or a **patient**. A person counts as a provider only if their registration has been verified (section 9).

| Action | Owner | Admin | Member (provider) | Member (patient) | Compliance |
|---|---|---|---|---|---|
| Create a group | yes (it is the creator) | | verified provider, clinic admin | **no** | |
| Edit name, description, picture | yes | yes | | | |
| Invite people | yes | yes | | | |
| Remove a member | yes | yes (not an admin, not the owner) | | | |
| Remove or demote an admin | yes, with a reason | | | | yes, with a reason |
| Make someone an admin | yes | yes | | | yes |
| Be made an admin | | | verified provider only | **never** | |
| Mute someone from posting | yes | yes (not the owner or another admin) | | | |
| Remove any message ("hide") | yes | yes | | | |
| Pin a message | yes | yes | yes | no | |
| Switch providers-only posting | yes | yes | | | |
| Close the group | yes | yes | | | |
| Reopen the group | yes, with a reason | no | | | yes, with a reason |
| Hand the group to another provider | yes | | | | |
| Reassign the group (owner unreachable) | | | | | yes, with a reason |
| Share earlier history (care group) | yes, with the patient's agreement | | | | |
| Read the audit trail | yes | | | | yes |
| Read the secure record (originals) | yes | | | | yes |
| Legal hold | | | | | yes |
| Verify providers | | | | | yes (own clinic), Super Admin (any) |

Compliance means the **clinic's Enterprise Admin** (for that clinic's groups only) or the **platform's Super Admin** (all groups). Compliance can read a group's details and reports, but not its messages.

Two rules that are easy to miss:
- The owner can never be removed by anyone. To change the owner, the owner hands the group over, or compliance reassigns it.
- The owner cannot leave until the group has been handed to another verified provider.


## 3. The flows

### Creating and joining

1. A verified provider calls `POST /groups` with a name, the kind, the link (program, appointment series, care episode or training) and, optionally, people to invite.
2. Each invited person gets a notification. They are **not members** yet: the group does not appear in their list, and every group endpoint answers 403.
3. The invited person opens `GET /groups/invitations/{id}`. It shows what the group is for, who the providers are, how many people can see it, and what members can see. It shows no messages.
4. They accept with `POST /groups/{id}/accept` and `consent: true`. Someone under 18 must also send a parent's or guardian's name and relationship. The invitation text they saw is saved as their consent record.
5. From that moment they see messages sent **after** they joined. A line "Dr Rao added Priya" appears in the chat.

### Leaving

- Anyone can leave: `POST /groups/{id}/leave`.
- A patient can instead withdraw consent: `POST /groups/{id}/consent/withdraw`. It has the same effect (access ends at once) and the consent record shows the withdrawal time.
- The owner must first hand the group over with `POST /groups/{id}/transfer-ownership`.

### Closing

When the program or care episode ends, an admin calls `POST /groups/{id}/close`. The group becomes read-only and the retention period starts. Reopening needs the owner and a reason.


## 4. API list

### Groups and invitations

| Method | Path | Who | What it does |
|---|---|---|---|
| POST | /groups | verified provider, clinic admin | create a group |
| GET | /groups | any member | my groups (the Groups tab). Filters: `kind`, `archived`, `q`, `page`, `page_size` |
| GET | /groups/{id} | member, or compliance | group details |
| PATCH | /groups/{id} | owner, admin | change name, description, picture |
| PATCH | /groups/{id}/settings | owner, admin | `admins_only_posting` (providers-only posting) |
| PATCH | /groups/{id}/preferences | member | mute or pin the group for me |
| GET | /groups/invitations | anyone | my pending invitations |
| GET | /groups/invitations/{id} | the invited person | one invitation, as shown before agreeing |
| POST | /groups/{id}/accept | the invited person | agree and join |
| POST | /groups/{id}/decline | the invited person | decline |
| POST | /groups/{id}/leave | member | leave |
| POST | /groups/{id}/consent/withdraw | member (not the owner) | take consent back (leaves at once). The owner gets 409 until the group is handed over |
| POST | /groups/{id}/close | owner, admin | close (read-only, retention starts). No body needed; optional `reason` |
| POST | /groups/{id}/reopen | owner, compliance | reopen. Body `{ "reason": "..." }`, required, 3 to 255 characters |

Archiving a group for yourself uses the existing `PATCH /conversations/{id}/archive`.

### Members and roles

| Method | Path | Who | What it does |
|---|---|---|---|
| GET | /groups/{id}/members | member | who is in the group (admins also see pending invitations) |
| POST | /groups/{id}/members | owner, admin | invite more people (named invitation only; there are no invite links) |
| DELETE | /groups/{id}/members/{user_id}?reason= | owner, admin, compliance | remove a member or cancel an invitation |
| PATCH | /groups/{id}/members/{user_id}/role | owner, admin, compliance | `admin` or `member` |
| POST | /groups/{id}/members/{user_id}/mute-posting | owner, admin | can read, cannot post. Optional `until` |
| DELETE | /groups/{id}/members/{user_id}/mute-posting | owner, admin | unmute |
| POST | /groups/{id}/transfer-ownership | owner | hand the group to another verified provider |
| POST | /groups/{id}/reassign-owner | compliance | name a new owner, with a reason |
| POST | /groups/{id}/members/{user_id}/share-history | owner | care group: let a provider see earlier messages |
| POST / DELETE | /groups/{id}/history-sharing/consent | the patient | agree, or stop agreeing, that earlier history may be shared |

### Messages

| Method | Path | Who | What it does |
|---|---|---|---|
| GET | /groups/{id}/messages | member | read messages. `cursor`, `limit` |
| POST | /groups/{id}/messages | member | send. Supports `reply_to_id`, `mentions`, `mention_all` |
| POST | /groups/{id}/read | member | mark the group read; sends `group_read` (section 6) |
| PATCH | /groups/{id}/messages/{mid} | the author | edit, within 15 minutes |
| POST | /groups/{id}/messages/{mid}/retract | the author | take my message out of the group |
| POST | /groups/{id}/messages/{mid}/hide | owner, admin | remove any message, with a reason |
| PUT / DELETE | /groups/{id}/messages/{mid}/pin | provider | pin or unpin |
| GET | /groups/{id}/pinned | member | pinned messages |
| PUT | /groups/{id}/messages/{mid}/reactions | member | react (program groups) |
| DELETE | /groups/{id}/messages/{mid}/reactions?emoji= | member | take my reaction back |
| GET | /groups/{id}/reactions | member | which reactions are allowed |
| GET | /groups/{id}/messages/{mid}/read-status | member | "seen by 4 of 6" |
| GET | /groups/{id}/search?q= | member | search, only what was sent after joining |
| GET | /groups/{id}/files | member | shared files, only after joining |
| POST | /groups/{id}/messages/{mid}/save-to-record | provider | save a message to a patient's clinical record |
| GET | /groups/{id}/clinical-records | provider, compliance | messages saved to records |
| GET | /groups/{id}/messages/{mid}/original | owner, compliance | the secure record: original text and versions |

Files and photos are uploaded with the existing attachment API, then sent by `attachment_id` in the message.

The old generic endpoints (`GET /conversations/{id}/messages`, `PATCH` and `DELETE /messages/{id}`, the message read-status and message search) refuse a group with `409 This is a group chat. Use the /api/v1/groups endpoints`. The socket `send_message` event and `POST /messages` still work for a group, with all the group rules applied.

### Compliance, privacy and verification

| Method | Path | Who | What it does |
|---|---|---|---|
| GET | /groups/{id}/audit | owner, compliance | audit trail (read only) |
| POST | /groups/{id}/reports | member | report a message or the group |
| GET | /groups/reports | compliance | reports (clinic: its own, Super Admin: all). Filters: `status`, `group_id` |
| PATCH | /groups/reports/{id} | compliance | `reviewing`, `resolved`, `dismissed`, with a note |
| POST / DELETE | /groups/{id}/legal-hold | compliance | put on, or lift, a legal hold |
| GET | /groups/compliance/groups | compliance | all groups of the clinic (or all, for the Super Admin). Details only, no messages |
| GET | /groups/consents/me | anyone | everything I agreed to, and when I withdrew |
| POST | /groups/data-requests | anyone | ask for a copy of my group chat data |
| GET | /groups/data-requests/me | anyone | my requests |
| GET | /groups/grievance-officer | anyone | who to contact with a complaint |
| POST | /groups/verified-providers | clinic admin, Super Admin | record that a provider's registration was verified |
| GET | /groups/verified-providers | clinic admin, Super Admin | list |
| GET | /groups/verified-providers/candidates | clinic admin, Super Admin (`tenant_id`) | the clinic's members that can be verified, for the "Add provider" picker. `q`, `all_roles`, `include_verified`, `page`, `page_size` |
| DELETE | /groups/verified-providers/{user_id} | clinic admin, Super Admin | revoke |

Data requests: `POST /api/v1/groups/data-requests` accepts `{ "request_type": "copy", "conversation_id": null, "note": null }`. Fields are optional; `copy` is the default and other types return 422. A group-specific request accepts current or former members. `GET /api/v1/groups/data-requests/me` lists the caller's requests, including `status`, `error_code`, `completed_at` and `expires_at`.

The database is the durable queue and private export store. API workers poll every 30 seconds; an atomic claim and a 15-minute lease allow recovery after a restart and fence off stale workers. The state transitions are `received` → `in_progress` → `completed` or `failed`; an interrupted claim is retried up to three times. The owner can submit `POST /api/v1/groups/data-requests/{id}/retry` for a failed request; other states return 409. Normal generation failures require this explicit retry. Pre-migration `in_progress` requests without a lease are left untouched and need operational review. No email is sent.

`GET /api/v1/groups/data-requests/{id}/download` requires the request owner and returns a JSON attachment with `Cache-Control: private, no-store`. Another user's ID returns 404; an unfinished request returns 409; expired or unavailable output returns 410. The output expires seven days after completion and expired content is removed by the worker. Create another request after expiry. Exports are limited to 10 MiB; `export_too_large` is reported as a failure.

The version 1 JSON contains `format`, `version`, `memberships`, and `messages`. Memberships include conversation ID, membership status, join/leave times and group role; active memberships additionally include the current group name, kind and description. Messages include ID, conversation ID, content, type, an attachment ID only if uploaded by the requester in that conversation and still available, creation and edit times. It excludes system messages, deleted or hidden messages, other authors' messages, attachment bytes and storage paths, credentials, and other participants' profiles. These are narrow implementation choices, not a statement of legal entitlement; retention and broader export scope require product/compliance review.


## 5. Samples

These are real responses from the tests.

### Create a group

`POST /groups`
```json
{
  "name": "Yoga batch Oct 2026",
  "kind": "program",
  "description": "Weekly yoga for beginners",
  "context_type": "program",
  "context_id": "e7ef5b55-c305-4dea-b4fe-47119768f2b1",
  "invitees": [
    { "user_id": "c34d2811-7602-4561-ad95-742a7e4e84a6" },
    { "user_id": "d30e894d-15fd-4b37-86b4-c128012dbb44", "is_minor": true }
  ]
}
```
`context_type` is `program`, `appointment_series` or `care_episode`. The name must not contain a patient's name or a health condition (422). For a program the server checks the program exists.

Response 201 (the same shape comes back from `GET /groups/{id}`):
```json
{
  "id": "6918ddf8-e923-4bd1-8067-84c444842799",
  "name": "Yoga batch Oct 2026",
  "kind": "program",
  "description": "Weekly yoga for beginners",
  "avatar_url": null,
  "context_type": "program",
  "context_id": "e7ef5b55-c305-4dea-b4fe-47119768f2b1",
  "tenant_id": "f1c33ada-26de-4f7c-beeb-3336dc458622",
  "status": "open",
  "admins_only_posting": true,
  "created_by": "6173a3a3-7248-4643-a533-4607c43e9d0b",
  "created_at": "2026-10-12T10:41:29.770727",
  "member_count": 1,
  "visibility_notice": "1 person can see this",
  "emergency_notice": { "text": "Not for emergencies. In an emergency call 112.", "number": "112" },
  "forwarding_blocked": true,
  "screenshots_blocked": true,
  "limits": { "max_members": 50, "max_admins": 3, "edit_window_minutes": 15 },
  "my_role": "owner",
  "my_kind": "provider",
  "muted": false,
  "pinned": false,
  "is_archived": false,
  "unread_count": 0,
  "last_message_at": "2026-10-12T10:41:29.786900",
  "last_message_preview": "Dr Rao created the group",
  "closed_at": null,
  "closed_reason": null,
  "retention_started_at": null,
  "retention_until": null,
  "legal_hold": false
}
```
Every group object also has `permissions` (what the caller can do right now: `can_post`, `can_edit_group`, `can_change_settings`, `can_invite`, `can_remove_members`, `can_make_admin`, `can_remove_admin`, `can_mute_members`, `can_hide_messages`, `can_pin_messages`, `can_close`, `can_reopen`, `can_transfer_ownership`, `can_reassign_owner`, `can_leave`, `can_withdraw_consent`, `can_view_audit`, `can_view_members`, `can_view_pending_invitations`, `can_set_legal_hold`, `can_report`, `can_mute_group`). Use it to show or hide controls; the rules are in `group-chat-training.md`, section 9.6.

`member_count` counts the people who can see the group (active members). It does not count pending invitations. Show `visibility_notice` and `emergency_notice` on every group screen.

### The invitation

`GET /groups/invitations/{id}`
```json
{
  "conversation_id": "6918ddf8-e923-4bd1-8067-84c444842799",
  "name": "Yoga batch Oct 2026",
  "kind": "program",
  "description": "Weekly yoga for beginners",
  "purpose": "This is a program group for several patients taking part in one program.",
  "invited_by_name": "Dr Rao",
  "provider_names": ["Dr Rao"],
  "member_count": 1,
  "visibility_notice": "1 person can see this",
  "what_members_can_see": "Everyone in the group can read every message and file posted after they join. Patients see each other by display name only; no phone numbers or profile details are shared. You can leave at any time.",
  "emergency_notice": { "text": "Not for emergencies. In an emergency call 112.", "number": "112" },
  "requires_guardian_consent": false,
  "invited_at": "2026-10-12T10:41:29.771712"
}
```
Show all of it before the person taps accept. When `requires_guardian_consent` is true, the app must collect the parent's or guardian's name and relationship.

### Accept

`POST /groups/{id}/accept`
```json
{ "consent": true, "display_name": "Priya S.", "guardian": { "name": "Meena Kumar", "relationship": "mother" } }
```
`guardian` is only needed when the invitation says so. `display_name` is what other patients see in a program group. If it is left out, the server uses first name and the initial of the last name ("Priya S."). An email address is never accepted as a display name. The response is the group (above).

### Members, as a patient sees them in a program group

`GET /groups/{id}/members`
```json
{
  "members": [
    { "user_id": "6173a3a3-…", "name": "Dr Rao", "role": "owner", "kind": "provider", "status": "active", "is_me": false, "joined_at": "2026-10-12T10:41:29.767587", "posting_muted": false, "posting_muted_until": null },
    { "user_id": "c34d2811-…", "name": "Priya Sharma", "role": "member", "kind": "patient", "status": "active", "is_me": true, "joined_at": "2026-10-12T10:41:29.846219", "posting_muted": false, "posting_muted_until": null },
    { "user_id": "d30e894d-…", "name": "Ravi K.", "role": "member", "kind": "patient", "status": "active", "is_me": false, "joined_at": "2026-10-12T10:41:29.874831", "posting_muted": false, "posting_muted_until": null }
  ],
  "total": 3
}
```
Providers are shown in full. Another patient is shown by display name only. In a care group everyone is shown in full. There is never a phone number, email or profile detail. `posting_muted` is only filled in for admins. Admins also see people with `status: "invited"`.

**Who can be made an admin or owner.** The owner and the admins also get three extra fields on every member, so the app does not need `GET /groups/verified-providers` (which stays closed to group owners):

```json
{ "user_id": "9a1c…", "name": "Dr Mehta", "role": "member", "kind": "provider", "status": "active",
  "is_verified_provider": true, "can_be_admin": true, "can_receive_ownership": true }
```

| Field | Meaning |
|---|---|
| `is_verified_provider` | the person is on the verified-provider list right now (a verification revoked after they joined shows `false`) |
| `can_be_admin` | `PATCH …/role {"role":"admin"}` would be accepted: active, verified provider, currently a plain member, and the group is below its admin limit |
| `can_receive_ownership` | `POST …/transfer-ownership` would be accepted for them: active, verified provider, not the owner and not the person asking |

For anyone who is not an owner or admin all three are `null`. They are worked out on each request from the same rules the two endpoints enforce, and those endpoints still check everything themselves (422 not a verified provider, 409 admin limit). Use them to show or hide the buttons, together with `permissions.can_make_admin` and `permissions.can_transfer_ownership` on the group (the person using the app may use the action at all).

### Send a message, with a reply

`POST /groups/{id}/messages`
```json
{ "content": "Thanks, see you then", "reply_to_id": "deb9dd74-ae28-45ba-b2c7-9f8775fde045" }
```
For an @-tag send `"mentions": ["<user id>"]`. Providers can send `"mention_all": true`. For a file, upload it first and send `{ "message_type": "image", "attachment_id": "…" }`.

Response 201:
```json
{
  "id": "0b92cf4d-d944-4b15-82a5-e19efcc76de0",
  "conversation_id": "6918ddf8-e923-4bd1-8067-84c444842799",
  "sender_id": "c34d2811-7602-4561-ad95-742a7e4e84a6",
  "sender_name": "Priya Sharma",
  "sender_kind": "patient",
  "message_type": "text",
  "content": "Thanks, see you then",
  "attachment_id": null,
  "status": "visible",
  "placeholder": null,
  "reply_to": { "id": "deb9dd74-…", "sender_name": "Dr Rao", "preview": "Class moves to 7am on Friday", "is_hidden": false },
  "mentions": [],
  "mentions_all": false,
  "mentions_me": false,
  "is_edited": false,
  "edited_at": null,
  "pinned": false,
  "reactions": {},
  "my_reactions": [],
  "read_by": null,
  "created_at": "2026-10-12T10:41:30.008326"
}
```
Names in a message are chosen for the person reading it, so the same message can show a different `sender_name` to a provider and to another patient.

### Read messages

`GET /groups/{id}/messages?limit=50` returns
```json
{
  "items": [ { "…": "messages as above, oldest first" } ],
  "has_more": false,
  "next_cursor": null,
  "unread_count": 2,
  "first_unread_message_id": "…",
  "pinned_count": 0
}
```
- To load older messages, pass `next_cursor` as `cursor`.
- Put the "new messages" marker above `first_unread_message_id`, then call `POST /groups/{id}/read`.
- A message taken out of the group is returned with no text:
```json
{ "id": "500ad83d-…", "sender_name": "Ravi K.", "message_type": "text", "content": null, "attachment_id": null,
  "status": "removed_by_admin", "placeholder": "Message removed by admin", "…": "…" }
```
`status` is `visible`, `retracted` (the author took it back, placeholder "Message retracted") or `removed_by_admin`. Show the `placeholder` text in its place.
- `read_by` is a list of user ids in a care group and `null` in a program group.
- `read_summary` says how many people have read the viewer's **own** message (see "Read counts on each message" below). It is present on every message.
- System lines ("Dr Rao added Priya", "Dr Mehta left") have `message_type: "system"`.

### Who has seen a message

`GET /groups/{id}/messages/{mid}/read-status`
```json
{ "message_id": "deb9dd74-…", "seen_by_count": 0, "recipient_count": 2, "label": "seen by 0 of 2", "seen_by": [] }
```
In a care group everyone can call it. In a program group a patient gets 403 and a provider gets the answer. `recipient_count` is worked out when you ask: the active members other than the sender, leaving out anyone who cannot see that message (they joined after it was sent). Someone who leaves or is removed drops out of both numbers.

### Read counts on each message

Every message in `GET /groups/{id}/messages` (and in the pinned list, in search results and in the response to sending) has a `read_summary`, worked out for the whole page in one go, so the app does not need to call read-status once per message. It is for the viewer's **own** messages.

```json
"read_summary": { "available": true, "read_count": 3, "recipient_count": 5, "all_read": false, "reason": null }
```

| Field | Meaning |
|---|---|
| `available` | `true` when the counts below are filled in |
| `read_count` | recipients who have read the group up to this message. `null` when not available |
| `recipient_count` | active members other than the sender who can see this message. `null` when not available |
| `all_read` | `read_count` equals `recipient_count`. `false` when there are no recipients. `null` when not available |
| `reason` | `null` when available, otherwise one of the values below |

When `available` is `false` the three counts are `null` and `reason` is one of:

| `reason` | When |
|---|---|
| `system_message` | a system line such as "Dr Rao added Priya" |
| `hidden` | the message was retracted or removed by an admin |
| `not_shown_to_patients` | a program group and the viewer is a patient. Patients never see read information there |
| `not_your_message` | the message was sent by someone else. Counts are only given to the sender |

The order is the order of the table: a system line is always `system_message`, whoever asks. The counts follow the same rules as read-status: in a care group every member sees them for their own messages, and in a program group only providers do. Nothing existing changed. `read_by` keeps its meaning (care groups only), and `GET /groups/{id}/messages/{mid}/read-status` remains for the list of people.

The counts can change without any message event: when someone joins, leaves or is removed, refetch the page (or use `group_read`, below, for readers).

An own message in a care group, after one of two recipients has read the group:
```json
{ "id": "deb9dd74-…", "sender_id": "<me>", "content": "hello team",
  "read_by": ["<priya>"],
  "read_summary": { "available": true, "read_count": 1, "recipient_count": 2, "all_read": false, "reason": null }, "…": "…" }
```
Someone else's message, or any message in a program group seen by a patient:
```json
"read_summary": { "available": false, "read_count": null, "recipient_count": null, "all_read": null, "reason": "not_your_message" }
```

### Edit, retract, remove

- `PATCH /groups/{id}/messages/{mid}` `{ "content": "…" }` works for the author within 15 minutes, only for text. The message then shows `is_edited: true`. Every earlier version is kept.
- `POST …/retract` takes the author's own message out of the group, at any time.
- `POST …/hide` `{ "reason": "shared a phone number" }` is for an owner or admin.
- Nothing is deleted. The owner and compliance can read the original: `GET …/original`
```json
{
  "message_id": "500ad83d-…",
  "sender_id": "d30e894d-…",
  "status": "removed_by_admin",
  "content": "oops wrong chat",
  "versions": [],
  "current_content": "oops wrong chat",
  "hidden_by": "6173a3a3-…",
  "hidden_reason": "shared a phone number",
  "hidden_at": "2026-10-12T10:41:30.067753",
  "created_at": "2026-10-12T10:41:30.036883"
}
```
`content` is the text as first written, `versions` are the earlier versions (oldest first) and `current_content` is the text now. Every read is written to the audit trail.

### Audit trail

`GET /groups/{id}/audit?action=&target_user_id=&page=&page_size=`
```json
{ "items": [
  { "id": "73be3e7c-…", "conversation_id": "6918ddf8-…", "actor_id": "6173a3a3-…", "actor_role": "provider",
    "action": "message_hidden", "target_user_id": "d30e894d-…", "message_id": "500ad83d-…",
    "reason": "shared a phone number", "details": null, "created_at": "2026-10-12T10:41:30.070032" },
  { "action": "settings_changed", "details": { "admins_only_posting": { "from": true, "to": false } }, "…": "…" }
], "total": 12 }
```
It never contains message text. Actions logged: `group_created`, `group_updated`, `member_invited`, `invitation_cancelled`, `consent_granted`, `consent_declined`, `consent_withdrawn`, `member_removed`, `member_left`, `role_changed`, `ownership_transferred`, `owner_reassigned`, `posting_muted`, `posting_unmuted`, `settings_changed`, `group_closed`, `group_reopened`, `message_edited`, `message_retracted`, `message_hidden`, `message_pinned`, `message_unpinned`, `history_sharing_agreed`, `history_sharing_withdrawn`, `history_shared`, `report_filed`, `report_resolved`, `report_dismissed`, `report_reviewing`, `legal_hold_set`, `legal_hold_cleared`, `clinical_record_saved`, `original_viewed`. There is no route that changes or deletes an entry.

### Report

`POST /groups/{id}/reports` `{ "message_id": "…", "reason": "privacy", "details": "…" }`. `message_id` is optional (leave it out to report the group). `reason` is one of `harassment`, `privacy`, `health_information`, `spam`, `inappropriate`, `safety`, `other`. The response is the report with `status: "open"`; compliance and the platform team are notified.

### What I agreed to

`GET /groups/consents/me` returns one item per group the person agreed to join, with `granted_at`, `withdrawn_at`, the guardian details if there were any, and `notice`, which is the exact invitation text they saw.


## 6. Realtime events (Socket.IO)

The group events use the same Socket.IO connection as 1:1 chat. A few event names are shared with 1:1 chat (`new_message`, `message_updated`, `typing`, `notification`). Every group event carries **`"conversation_type": "group"`**, so the app can tell the two apart. A 1:1 chat's events are unchanged and do not carry that field. Existing field names did not change; `conversation_type` is the only addition.

### Joining rooms and reconnecting

- On connect the server puts the socket in the person's own room (`user:<id>`), nothing more. It does not join any group room.
- **After every connect, reconnect and page refresh, send `join_room` with `{"conversation_id": "<group id>"}` for each group the app wants live updates for.**
  - Success: `joined_room` with `{conversation_id, room, authorized: true}`.
  - Failure: an `error` event with `{event: "join_room", detail: "..."}`. For example "Not authorized to join this conversation" (not an active member), "conversation_id is required", "Not authenticated".
  - `leave_room` works the same way and answers with `left_room`.
- Accepting an invitation puts that person's connected devices into the group's room straight away. Being removed or leaving takes them out. A device that connects later still has to send `join_room`.
- Nothing is replayed after a disconnect. On reconnect, rejoin the rooms and refetch over REST: the message list from the last message the app holds, `GET /groups/{id}/members`, the group details and the group list (which has the unread counts). Events have no id or sequence number: dedupe `new_message` by `message.id`, and replace state by `message.id` on `message_updated` and `message_hidden`. `reaction_updated` carries the full counts, so overwrite.
- The sender also receives the event for their own REST action, if their socket joined the room.

### Events to the group's room (`conversation:<id>`)

Every payload has `conversation_id` and `conversation_type: "group"`, plus:

| Event | When | Other fields |
|---|---|---|
| `new_message` | a message was sent | `message`: `id`, `conversation_id`, `sender_id`, `content`, `message_type`, `attachment_id`, `reply_to_id`, `mentions`, `mentions_all`, `is_deleted` (false), `is_edited` (false), `edited_at` (null), `created_at`, `read_by` (empty). The same for every reader; no names |
| `message_updated` | a message was edited | `message`: the same fields, with `is_edited: true` and `edited_at` |
| `message_hidden` | retracted or removed by an admin | `message_id`, `status` (`retracted` or `removed_by_admin`), `placeholder` |
| `message_pinned` | pinned or unpinned | `message_id`, `pinned` |
| `reaction_updated` | a reaction was added or removed | `message_id`, `reactions` (the new counts) |
| `member_joined` | someone accepted | `user_id`, `role`, `kind` |
| `member_left` | someone left or withdrew consent | `user_id` |
| `member_removed` | someone was removed | `user_id` |
| `member_role_changed` | admin or owner changed, ownership transferred or reassigned | `user_id`, `role` (`owner`, `admin` or `member`) |
| `group_updated` | name, description or picture edited | `name`, `description`, `avatar_url` |
| `group_updated` | the posting setting changed | `admins_only_posting` |
| `group_closed` | closed | `closed_at` |
| `group_reopened` | reopened | |
| `typing` | care groups | `user_id`, `is_typing` |

Sending through the socket `send_message` event also works for a group, and then also sends `conversation_updated` (with `conversation_type: "group"`). REST is preferred for groups.

### Events to one person's room (`user:<id>`)

Also with `conversation_type: "group"`:

| Event | When | Fields |
|---|---|---|
| `group_invitation` | they were invited | `conversation_id` |
| `member_removed` | they were removed. Use it to close the open screen and drop the group from the list | `conversation_id`, `user_id` |
| `posting_muted` | an admin muted them | `conversation_id`, `until` (null when not timed) |
| `posting_unmuted` | an admin unmuted them | `conversation_id` |
| `group_read` | someone read the group (below) | `conversation_id`, `user_id`, `read_at`, `read_through_message_id` |

### Read receipts: `group_read`

`POST /groups/{id}/read` (and the socket `mark_read` event with a group's `conversation_id`) marks the group read for the caller and sends `group_read` to each person who may see read receipts:

```json
{ "conversation_id": "…", "conversation_type": "group", "user_id": "<who read>", "read_at": "2026-10-26T09:30:00.123456",
  "read_through_message_id": "deb9dd74-…" }
```

- `read_through_message_id` is the newest message (not a system line, not deleted) that existed when the read was saved. It is the read boundary **for that member**: that person has read every message up to and including it, so a message created after it has not been read by them. `null` when the group has no such message. Use it to find the boundary in the messages you hold, instead of comparing times. It does not mean everyone has read; the recipient counts (`read_summary`) say how many have.
- `read_at` is the time that was saved, in UTC, written without a `Z`. It is exactly the `read_at` returned by `POST /groups/{id}/read`.

- `user_id` has read **every message sent up to `read_at`** (that is, messages with `created_at` up to `read_at`). It is not a single message, so update every message you hold that is that old.
- Who receives it follows the read-status rules:
  - **Care group:** every active member.
  - **Program group:** only providers. Patients never receive it, and never learn who has read.
  - The reader's own devices always receive it, so another device of theirs can clear its unread count.
- It goes to each person's own room (`user:<id>`), not the group room. The app does not need to have joined the group room to receive it.
- The REST response of `POST /groups/{id}/read` is unchanged. For a message-by-message count use `GET /groups/{id}/messages/{mid}/read-status`.
- A group's messages cannot be marked read one by one through the generic `PATCH /messages/{id}/read` or the socket `mark_read` with a `message_id`: both refuse a group with 409.
- Unread counts are not pushed. They come from the message list and the group list.

Messages carry no sender names because each person may see a different name for the same sender. The app takes names from `GET /groups/{id}/members`, which it should refresh on `member_joined`, `member_left`, `member_removed` and `member_role_changed`.

### Running the REST API and Socket.IO as separate processes

Set `SOCKETIO_REDIS_URL` (the same Redis) on **both** the REST process and the Socket.IO process. Then:

- Events sent from a REST route travel through Redis to the process holding each connection.
- Joining and leaving group rooms (accepting an invitation, being removed or leaving) is published on the Redis channel `classifieds:group-room-sync`. Every process listens on it at startup and the one holding that person's sockets moves them. This means a removed person stops receiving messages straight away even when the removal is made on another process.
- Without `SOCKETIO_REDIS_URL` everything is assumed to run in one process, and an event or room change made by a different process will not reach the sockets.
- If Redis is down, the REST call still succeeds (events are best effort), the listener reconnects with a growing delay, and the app should refetch over REST after a reconnect.
- Use `scripts/entrypoint-api.sh` (several workers, REST only) and `scripts/entrypoint-socket.sh` (one worker) for a split deployment. The combined `scripts/entrypoint.sh` is one process and refuses more than one socket worker.

## 7. Notifications

They use the existing notification feed (`GET /users/me/notifications`) and the Socket.IO `notification` event. `metadata.category` and `metadata.conversation_id` are always set, so the app can open the right group.

| Category | Sent to | When |
|---|---|---|
| group_invitation | the invited person | someone invited them |
| group_message | every active member except the sender, and except those who muted the group | a message was sent |
| group_removed | the person removed | an admin removed them |
| group_role_changed | the person | they became an admin, a member or the owner (`metadata.role`) |
| group_closed, group_reopened | all active members | the group was closed or reopened |
| group_report_filed | the clinic's Enterprise Admins and the Super Admins | a report was filed (`metadata.report_id`) |

Privacy rules that the app can rely on:

- A message notification says only **"New message"**. It has no group name, no sender and no text. The same is true of push, the in-app notification and the realtime event. Email and SMS are not used for group messages at all.
- An invitation notification does not contain the group name either.
- An @-tag notifies the tagged person with the same "New message" text (`mentioned: true` in the chat notification data). In a **care group** it reaches them even if they muted the group. In a program group a mute is always respected.
- Muting a group (`PATCH /groups/{id}/preferences`, with an optional `muted_until`) never removes access.


## 8. Things the app must do

The server enforces the rules above, but a few things are the app's job:

1. Show `visibility_notice` ("N people can see this") and `emergency_notice` ("Not for emergencies") on every group screen and on the invitation.
2. Patients see groups in a separate **Groups** tab (`GET /groups`). `GET /conversations` leaves groups out for patients. Providers get groups in the same inbox as 1:1 chats, marked `conversation_type: "group"` with `group_kind`; the group name is in `subject`.
3. Block forwarding out of the app. There is no forward endpoint. Block screenshots where the phone allows it (`screenshots_blocked` is the hint).
4. Do not show a patient's real name in a program group. Use the `name` the API returns.
5. Hide the controls the person cannot use: create (patients), admin actions (members), pin (patients), reactions (care groups), typing (program groups), read-by (program groups, patients).
6. Treat 403 on any group endpoint as "you are not in this group" and remove it from the list.


## 9. Verified providers

A provider can create a group, own one or be made an admin only if their registration has been verified. A clinic admin (for their own clinic) or the Super Admin records it:

`POST /groups/verified-providers`
```json
{ "user_id": "…", "registration_number": "MH-12345", "registration_council": "State Medical Council" }
```

The person must be an **active member of the clinic** (the identity service's member list), with a clinician or administrator role: `provider`, `internal_user`, `staff`, `tenant_admin`, `tenant_owner` or `enterprise_admin`. So an Enterprise Admin or the clinic owner can be verified the same way, for example to be made an admin of a Provider's Training Group. The call is refused with 422 when the user is not a member of the clinic ("The user is not a member of this clinic"), the membership is not active, or the role is a learner (`external_user`), and with 503 when the member list cannot be read right now (nothing is saved). A Super Admin must send `tenant_id` (the clinic the person belongs to); a clinic admin's own clinic is used automatically. Verifying someone again after a revoke goes through the same check.

An Enterprise Admin's clinic is taken from their authenticated tenant identity; they cannot supply another clinic. A Platform Super Admin can verify for any clinic, but must include that clinic's `tenant_id` in the request. A verification only authorizes groups in the recorded clinic.

Before the first group can be created, every provider who needs to must be added this way. This is a one-time load: use `GET /groups/verified-providers` to check. A provider whose verification is revoked can no longer create groups; groups they already own are left alone (the owner can hand them over).

This list belongs to group chat only: nothing else in the system adds a provider to it, so every provider who should use group chat is added once (a small admin screen over `GET`/`POST`/`DELETE /groups/verified-providers`). A clinic admin can create groups without being in this list. If a clinic admin should also be made an admin of someone else's group, add them to the list.

The list returned by `GET /groups/verified-providers` includes each provider's `name`. For the screen that adds providers, `GET /groups/verified-providers/candidates` lists the clinic's active members who are not verified yet (a provider whose verification was revoked is listed as `revoked`, so they can be verified again). A ready-made panel for the Enterprise Admin area is in `group-chat-verified-providers-panel.html`. This list belongs to group chat only: nothing else in the system adds a provider to it, so every provider who should use group chat is added once (a small admin screen over `GET`/`POST`/`DELETE /groups/verified-providers`). A clinic admin can create groups without being in this list. If a clinic admin should also be made an admin of someone else's group, add them to the list.


A provider who is not verified, when invited into a group, is treated like a patient there (they cannot pin, tag everyone or be made an admin).


## 10. Settings

Set in the server environment. The values below are the defaults.

| Setting | Default | Meaning |
|---|---|---|
| GROUP_MAX_MEMBERS_CARE | 15 | members in a care group (to confirm) |
| GROUP_MAX_MEMBERS_PROGRAM | 50 | members in a program group (to confirm) |
| GROUP_MAX_ADMINS | 3 | admins besides the owner |
| GROUP_EDIT_WINDOW_MINUTES | 15 | how long an author can edit (to confirm) |
| GROUP_REQUIRE_VERIFIED_PROVIDER | true | providers need a verification to create a group |
| GROUP_PROGRAM_PATIENTS_CAN_POST | true | false = patients can never post in a program group |
| GROUP_PROGRAM_REACTIONS | 👍 ❤️ 🙏 👏 😊 | reactions allowed in a program group |
| GROUP_NAME_BLOCKED_TERMS | empty | comma-separated health conditions that may not appear in a name (whole words) |
| GROUP_EMERGENCY_NUMBER | 112 | shown in the emergency notice |
| GROUP_RETENTION_DAYS | not set | how long the record is kept after a group closes (set with legal advice) |
| GROUP_GRIEVANCE_OFFICER_NAME / _EMAIL / _PHONE | empty | returned by `GET /groups/grievance-officer` |

Pending invitations count against the member limit.


## 11. What this backend does not do

These come from the same specification but are outside what an API can do, or wait for a decision:

| Item | Where it belongs |
|---|---|
| Messages and files encrypted | database and storage encryption (infrastructure) |
| Providers sign in with multi-factor authentication; sessions lock when idle | the login service and the apps |
| Files limited to approved types and scanned for malware | the upload endpoint takes the type limits it always had; there is no malware scanner yet |
| Blocking screenshots and forwarding | the apps. The API has no forward endpoint and returns `screenshots_blocked` as a hint |
| Saved messages reaching the real clinical record | the API stores a snapshot (`GET /groups/{id}/clinical-records`). Sending it to a health-record system is an integration, if one is planned |
| Deleting data when the retention period ends | nothing is ever deleted by the API. `retention_until` is the date a clean-up job can use, and `legal_hold` stops it |
| "Read-only until reassigned" when an owner cannot be reached | compliance can close the group, reassign it, and reopen it |
| Socket rooms in a split (multi-server) deployment | needs `SOCKETIO_REDIS_URL` on every process (section 6). The room changes are published over Redis; this has been tested with a fake Redis client only, not a live Redis |
| Name checks for a health condition | the name is checked against the people in the group and the configured blocked words, not by understanding the text |
| Appointment series and care episodes existing | a program link is checked against the programs table. An appointment series or care episode has no table in this service yet, so only its type is checked |

To confirm with legal and compliance (from the specification): retention periods by group type and for minors; the group sizes and the edit window; which data-protection rules and breach timelines apply; whether HIPAA applies; whether patients may post to each other in program groups at all (`GROUP_PROGRAM_PATIENTS_CAN_POST`).


## 12. Errors

| Status | Meaning here |
|---|---|
| 401 | not logged in |
| 403 | not allowed: not a member, not an admin, a patient trying a provider action, muted, providers-only posting. The `detail` says which |
| 404 | the group, message, member or invitation does not exist, or the message is from before the person joined |
| 409 | the state does not allow it: group closed, group full, owner must hand over first, already closed |
| 422 | the input is wrong: name with a patient's name or a condition, missing link, consent not true, guardian missing for a minor, a reason is required, unknown emoji |

Errors look like `{"detail": "message"}`.


## 13. Deploy

1. Run the repository's full migration deployment command: `alembic upgrade heads`. The group-chat migration is `e5b2c7a9d1f3` (after `b6d1f3a8c2e4` and `d3a8f1c6e9b2`); `b3e7c1a9d5f4` follows it. The group migration adds the group columns and new tables without changing existing chats (every current participant becomes `active`).
2. Add the verified providers (section 9) and set the settings you need (section 10).
3. Set `GROUP_GRIEVANCE_OFFICER_*` and the retention period once legal has confirmed them.

## 14. One inbox: 1:1 chats and groups in one list

`GET /api/v1/conversations/` (the existing inbox, also `?status=archived`, `?search=`, `GET /conversations/archived`) returns **direct chats and the groups the caller has accepted** in the same `items` array, in the same shape. This now includes patients: it used to leave their groups to the Groups tab (`GET /groups` still works). Messages stay separate: a direct chat uses `/conversations/{id}/messages`, a group uses `/groups/{id}/messages` (the generic endpoint answers a group with 409).

```json
{
  "items": [
    { "id": "a1b2…", "status": "open", "conversation_type": "direct", "conversation_subtype": "standard", "subject": "Dr. James",
      "last_message_at": "2026-10-29T10:15:00", "last_message_preview": "See you tomorrow",
      "last_message": { "sender_id": "1111…", "sender_name": "Dr. James", "preview": "See you tomorrow", "read_by": [] },
      "unread_count": 2, "assigned_provider_id": "2222…", "other_participant_user_id": "2222…", "is_archived": false, "archived_at": null,
      "updated_at": "2026-10-29T10:15:00", "member_count": null, "can_post": null, "visibility_notice": null },
    { "id": "46fc…", "status": "open", "conversation_type": "group", "conversation_subtype": "group", "group_kind": "care", "subject": "groupchat3",
      "last_message_at": "2026-10-27T06:31:40", "last_message_preview": "fsdgsg",
      "last_message": { "sender_id": "3c11…", "sender_name": "enzo provider", "preview": "fsdgsg", "read_by": null },
      "unread_count": 23, "assigned_provider_id": null, "other_participant_user_id": null, "is_archived": false, "archived_at": null,
      "updated_at": "2026-10-27T06:31:40", "member_count": 8, "can_post": false, "visibility_notice": "…" }
  ],
  "pagination": { "total": 2, "page": 1, "page_size": 20, "total_pages": 1 }
}
```

- **`conversation_type`** is exactly `"direct"` (every one-to-one chat, whatever its stored type) or `"group"`. `conversation_subtype` keeps the stored type (`standard`, `preview`, `booking`, `group`). The provider list `GET /conversations/provider` is unchanged and still returns the stored type.
- **Order:** newest `last_message_at` first, then the most recently updated; a chat with no message yet goes last.
- **Only accepted groups.** An invitation not yet accepted, a group the person left, or one they were removed from is not listed. Pending invitations stay on `GET /groups/invitations`.
- **A group row follows the group's own privacy rules,** the same as `GET /groups`:
  - the last message is the newest one the caller may see: nothing from before they joined, and a retracted or admin-removed message is skipped, never shown;
  - a system line ("Priya joined") has `sender_id: null` and `sender_name: null`;
  - `sender_name` is the name this viewer is allowed to see for that person (in a program group a patient sees another patient's display name, not their real name);
  - `unread_count` ignores the caller's own messages, hidden messages, system lines and what was said before they joined;
  - `last_message.read_by` is `null` (use the read-status endpoint), `member_count`, `can_post` (false when the group is closed, the caller is muted, or only providers may post) and `visibility_notice` are set. For a direct chat those three are `null`.
- **Search** finds a group by its name only (never by its last message text, which could be a retracted message), and a direct chat by name or last message.
- `last_message.sender_name` for a direct chat is the sender's display name, never an email address. It is `null` when it cannot be resolved.
